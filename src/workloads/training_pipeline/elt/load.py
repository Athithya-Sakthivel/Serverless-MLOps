"""Load clean parquet data and write ELT checkpoints to Azure Blob Storage."""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .transform import TransformMetrics

LOG = logging.getLogger(__name__)
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE = "application/json"
DEFAULT_CLEAN_CONTAINER = "clean"
DEFAULT_CHECKPOINT_CONTAINER = "checkpoints"


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """Configuration for writing clean output and checkpoints."""

    storage_account_name: str
    clean_container_name: str = DEFAULT_CLEAN_CONTAINER
    checkpoint_container_name: str = DEFAULT_CHECKPOINT_CONTAINER

    @property
    def account_url(self) -> str:
        return f"https://{self.storage_account_name}.blob.core.windows.net"


def build_blob_service_client(storage_account_name: str) -> BlobServiceClient:
    """Build a BlobServiceClient using DefaultAzureCredential."""
    if not storage_account_name:
        raise ValueError("storage_account_name is required")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=credential,
    )


def _ensure_container(container_client) -> None:
    """Create a container only if it does not already exist."""
    try:
        container_client.create_container()
        LOG.info("Created container: %s", container_client.container_name)
    except ResourceExistsError:
        LOG.info("Container already exists: %s", container_client.container_name)


def clean_blob_name(raw_blob_name: str) -> str:
    """Mirror the input blob path in the clean container."""
    blob_name = raw_blob_name.strip().lstrip("/")
    if not blob_name:
        raise ValueError("raw_blob_name is required")
    return blob_name


def checkpoint_blob_name(raw_blob_name: str) -> str:
    """Derive a deterministic checkpoint object name from the raw blob path."""
    blob_name = clean_blob_name(raw_blob_name)
    if blob_name.endswith(".parquet"):
        blob_name = blob_name[: -len(".parquet")]
    return f"elt/{blob_name}.json"


def checkpoint_payload(
    *,
    raw_blob_name: str,
    clean_blob_name: str,
    validation_report: dict[str, Any],
    transform_metrics: TransformMetrics,
    started_at: datetime,
    finished_at: datetime,
    status: str = "completed",
) -> dict[str, Any]:
    """Build the JSON payload written after a successful ELT run."""
    return {
        "status": status,
        "raw_blob_name": raw_blob_name,
        "clean_blob_name": clean_blob_name,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "finished_at": finished_at.astimezone(UTC).isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "validation": validation_report,
        "transform": asdict(transform_metrics),
    }


def checkpoint_exists(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
) -> bool:
    """Return True when a completed checkpoint already exists."""
    service_client = build_blob_service_client(storage_account_name)
    blob_client = service_client.get_blob_client(
        container=checkpoint_container_name,
        blob=checkpoint_blob_name(raw_blob_name),
    )

    try:
        return blob_client.exists()
    except HttpResponseError:
        return False


def write_clean_frame(
    frame: pl.DataFrame,
    *,
    storage_account_name: str,
    clean_container_name: str,
    clean_blob_name_value: str,
) -> None:
    """Upload the clean parquet to Azure Blob Storage."""
    if frame.height == 0:
        raise ValueError("Refusing to upload an empty clean frame")

    service_client = build_blob_service_client(storage_account_name)
    container_client = service_client.get_container_client(clean_container_name)
    _ensure_container(container_client)

    temp_handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    temp_path = Path(temp_handle.name)
    temp_handle.close()

    try:
        frame.write_parquet(temp_path, compression="zstd")
        with temp_path.open("rb") as handle:
            container_client.upload_blob(
                name=clean_blob_name_value,
                data=handle,
                overwrite=True,
                content_settings=ContentSettings(content_type=PARQUET_CONTENT_TYPE),
            )
    except ClientAuthenticationError as exc:
        raise RuntimeError("Azure authentication failed while writing the clean blob") from exc
    except HttpResponseError as exc:
        error_code = str(getattr(exc, "error_code", "") or "")
        message = str(exc)
        if (
            "AuthorizationPermissionMismatch" in error_code
            or "AuthorizationPermissionMismatch" in message
        ):
            raise RuntimeError(
                "Upload was authenticated but not authorized. "
                "Assign Storage Blob Data Contributor on the clean container or account."
            ) from exc
        raise
    finally:
        temp_path.unlink(missing_ok=True)

    LOG.info("Uploaded clean parquet to %s/%s", clean_container_name, clean_blob_name_value)


def write_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
    payload: dict[str, Any],
) -> str:
    """Write a deterministic checkpoint JSON and return its blob name."""
    service_client = build_blob_service_client(storage_account_name)
    container_client = service_client.get_container_client(checkpoint_container_name)
    _ensure_container(container_client)

    blob_name = checkpoint_blob_name(raw_blob_name)
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    container_client.upload_blob(
        name=blob_name,
        data=body,
        overwrite=True,
        content_settings=ContentSettings(content_type=JSON_CONTENT_TYPE),
    )
    LOG.info("Wrote checkpoint to %s/%s", checkpoint_container_name, blob_name)
    return blob_name


def read_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
) -> dict[str, Any] | None:
    """Read a checkpoint if it exists."""
    service_client = build_blob_service_client(storage_account_name)
    blob_client = service_client.get_blob_client(
        container=checkpoint_container_name,
        blob=checkpoint_blob_name(raw_blob_name),
    )
    try:
        downloader = blob_client.download_blob()
        return json.loads(downloader.readall().decode("utf-8"))
    except ResourceNotFoundError:
        return None
    except ClientAuthenticationError as exc:
        raise RuntimeError("Azure authentication failed while reading the checkpoint") from exc
