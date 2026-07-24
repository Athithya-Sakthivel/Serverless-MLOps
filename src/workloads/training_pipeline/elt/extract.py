"""Extract raw parquet from Azure Blob Storage"""

from __future__ import annotations

import logging
import os

import polars as pl
from utils.storage import build_blob_service_client, download_blob_to_tempfile

LOG = logging.getLogger(__name__)
DEFAULT_INPUT_BLOB_NAME_ENV = ("INPUT_BLOB_NAME", "RAW_BLOB_NAME", "EVENT_GRID_BLOB_NAME")


def resolve_input_blob_name(explicit_blob_name: str | None = None) -> str:
    if explicit_blob_name:
        cleaned = explicit_blob_name.strip().lstrip("/")
        if cleaned:
            return cleaned

    for env_name in DEFAULT_INPUT_BLOB_NAME_ENV:
        value = os.getenv(env_name, "").strip().lstrip("/")
        if value:
            return value

    raise ValueError(
        "Input blob name is required. Set INPUT_BLOB_NAME, RAW_BLOB_NAME, "
        "EVENT_GRID_BLOB_NAME, or pass it explicitly."
    )


def read_parquet_from_blob(
    *,
    storage_account_name: str,
    container_name: str,
    blob_name: str,
) -> pl.DataFrame:
    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    service_client = build_blob_service_client(storage_account_name)
    temp_path = download_blob_to_tempfile(
        service_client,
        container_name=container_name,
        blob_name=blob_name,
        suffix=".parquet",
    )
    try:
        frame = pl.read_parquet(temp_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read parquet from {blob_name}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

    if frame.height == 0:
        raise ValueError(f"Raw blob {container_name}/{blob_name} is empty")

    LOG.info(
        "Downloaded raw blob %s/%s: %d rows, %d columns",
        container_name,
        blob_name,
        frame.height,
        frame.width,
    )
    return frame
