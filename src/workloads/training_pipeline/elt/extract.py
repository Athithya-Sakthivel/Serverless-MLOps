"""Extract raw parquet from Azure Blob Storage"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from typing import Any

import polars as pl
from azure.core.credentials import TokenCredential
from utils.storage import build_blob_service_client

LOG = logging.getLogger(__name__)
DEFAULT_INPUT_BLOB_NAME_ENV = ("INPUT_BLOB_NAME", "RAW_BLOB_NAME", "EVENT_GRID_BLOB_NAME")


def resolve_input_blob_name(explicit_blob_name: str | None = None) -> str:
    """Resolve blob name from explicit argument or common environment variables."""
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
    blob_service_client: Any = None,
    credential: TokenCredential | None = None,
) -> pl.DataFrame:
    """Download a parquet blob and load it into a Polars DataFrame.

    A fake *blob_service_client* can be injected for testing.
    """
    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    if blob_service_client is None:
        blob_service_client = build_blob_service_client(storage_account_name, credential=credential)

    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)
    parquet_bytes = blob_client.download_blob().readall()

    try:
        frame = pl.read_parquet(BytesIO(parquet_bytes))
    except Exception as exc:
        raise RuntimeError(f"Failed to read parquet from {blob_name}") from exc

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
