"""Write clean parquet and manage ELT checkpoints."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceNotFoundError
from utils.storage import (
    JSON_CONTENT_TYPE,
    PARQUET_CONTENT_TYPE,
    build_blob_service_client,
    ensure_container,
    upload_bytes_to_blob,
    upload_file_to_blob,
)

LOG = logging.getLogger(__name__)

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


def clean_blob_name(raw_blob_name: str) -> str:
    """Derive the clean blob path from the raw blob path."""
    raw_blob_name = raw_blob_name.strip().lstrip("/")
    if not raw_blob_name:
        raise ValueError("raw_blob_name is required")
    parts = raw_blob_name.split("/", 1)
    if len(parts) == 2:
        return f"clean/{parts[1]}"
    return f"clean/{raw_blob_name}"


def read_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
    blob_service_client: Any = None,
    credential: TokenCredential | None = None,
) -> dict[str, Any] | None:
    """Read the ELT checkpoint JSON blob if it exists."""
    if blob_service_client is None:
        blob_service_client = build_blob_service_client(storage_account_name, credential=credential)

    blob_client = blob_service_client.get_container_client(
        checkpoint_container_name
    ).get_blob_client(f"elt/{raw_blob_name.lstrip('/')}.json")

    try:
        downloader = blob_client.download_blob()
        return json.loads(downloader.readall().decode("utf-8"))
    except ResourceNotFoundError:
        return None


def write_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
    payload: dict[str, Any],
    blob_service_client: Any = None,
    credential: TokenCredential | None = None,
) -> None:
    """Write (or overwrite) the ELT checkpoint JSON blob."""
    if blob_service_client is None:
        blob_service_client = build_blob_service_client(storage_account_name, credential=credential)

    ensure_container(blob_service_client, checkpoint_container_name)
    blob_name = f"elt/{raw_blob_name.lstrip('/')}.json"
    upload_bytes_to_blob(
        blob_service_client,
        container_name=checkpoint_container_name,
        blob_name=blob_name,
        data=json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
        content_type=JSON_CONTENT_TYPE,
        overwrite=True,
    )
    LOG.info("ELT checkpoint written: %s", blob_name)


def write_clean_frame(
    frame: pl.DataFrame,
    *,
    storage_account_name: str,
    clean_container_name: str,
    clean_blob_name_value: str,
    blob_service_client: Any = None,
    credential: TokenCredential | None = None,
) -> None:
    """Write the clean DataFrame as a Parquet blob."""
    if frame.height == 0:
        raise ValueError("Refusing to write an empty clean frame")

    if blob_service_client is None:
        blob_service_client = build_blob_service_client(storage_account_name, credential=credential)

    fd, tmp_path_str = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    try:
        frame.write_parquet(tmp_path)
        upload_file_to_blob(
            blob_service_client,
            container_name=clean_container_name,
            blob_name=clean_blob_name_value,
            file_path=tmp_path,
            content_type=PARQUET_CONTENT_TYPE,
            overwrite=True,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    LOG.info("Clean frame written: %s (%d rows)", clean_blob_name_value, frame.height)


def checkpoint_payload(
    *,
    raw_blob_name: str,
    clean_blob_name: str,
    validation_report: dict[str, Any],
    transform_metrics: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
    status: str = STATUS_COMPLETED,
) -> dict[str, Any]:
    """Build a standard ELT checkpoint payload."""
    return {
        "status": status,
        "raw_blob_name": raw_blob_name,
        "clean_blob_name": clean_blob_name,
        "validation_report": validation_report,
        "transform_metrics": transform_metrics,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
    }
