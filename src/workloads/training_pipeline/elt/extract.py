"""Extract raw parquet data from Azure Blob Storage into Polars."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

LOG = logging.getLogger(__name__)
DEFAULT_INPUT_CONTAINER = "raw"
DEFAULT_INPUT_BLOB_NAME_ENV = ("INPUT_BLOB_NAME", "RAW_BLOB_NAME", "EVENT_GRID_BLOB_NAME")
PARQUET_SUFFIX = ".parquet"


@dataclass(frozen=True, slots=True)
class ExtractConfig:
    """Configuration for reading a raw parquet blob."""

    storage_account_name: str
    input_container_name: str = DEFAULT_INPUT_CONTAINER
    input_blob_name: str = ""

    @property
    def account_url(self) -> str:
        return f"https://{self.storage_account_name}.blob.core.windows.net"


def resolve_input_blob_name(explicit_blob_name: str | None = None) -> str:
    """Resolve the blob name from an explicit value or common environment variables."""

    if explicit_blob_name:
        blob_name = explicit_blob_name.strip().lstrip("/")
        if blob_name:
            return blob_name

    for env_name in DEFAULT_INPUT_BLOB_NAME_ENV:
        value = os.getenv(env_name, "").strip().lstrip("/")
        if value:
            return value

    raise ValueError(
        "Input blob name is required. Set INPUT_BLOB_NAME, RAW_BLOB_NAME, "
        "EVENT_GRID_BLOB_NAME, or pass it explicitly."
    )


def build_blob_service_client(storage_account_name: str) -> BlobServiceClient:
    """Build a BlobServiceClient using DefaultAzureCredential."""
    if not storage_account_name:
        raise ValueError("storage_account_name is required")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=credential,
    )


def download_blob_to_tempfile(
    *,
    storage_account_name: str,
    container_name: str,
    blob_name: str,
) -> Path:
    """Download a blob to a temporary parquet file and return the local path."""

    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    service_client = build_blob_service_client(storage_account_name)
    blob_client = service_client.get_blob_client(container=container_name, blob=blob_name)

    temp_file = tempfile.NamedTemporaryFile(suffix=PARQUET_SUFFIX, delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        downloader = blob_client.download_blob()
        with temp_path.open("wb") as handle:
            downloader.readinto(handle)
    except ResourceNotFoundError as exc:
        temp_path.unlink(missing_ok=True)
        raise FileNotFoundError(f"Blob not found: {container_name}/{blob_name}") from exc
    except ClientAuthenticationError as exc:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("Azure authentication failed while reading the raw blob.") from exc
    except HttpResponseError as exc:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download blob {container_name}/{blob_name}: {exc}") from exc

    return temp_path


def read_parquet_from_blob(
    *,
    storage_account_name: str,
    container_name: str,
    blob_name: str,
) -> pl.DataFrame:
    """Read a parquet blob from Azure Blob Storage into a Polars DataFrame."""

    temp_path = download_blob_to_tempfile(
        storage_account_name=storage_account_name,
        container_name=container_name,
        blob_name=blob_name,
    )
    try:
        frame = pl.read_parquet(temp_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read parquet from {temp_path.name}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

    if frame.height == 0:
        raise ValueError(f"Raw blob {container_name}/{blob_name} is empty")

    LOG.info(
        "Downloaded raw blob %s/%s with %d rows and %d columns",
        container_name,
        blob_name,
        frame.height,
        frame.width,
    )
    return frame
