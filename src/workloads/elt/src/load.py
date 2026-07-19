# load.py
"""Data loading utilities using managed identity. Zero connection strings."""

from __future__ import annotations

import io
import logging
import os
from functools import lru_cache

import pandas as pd
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueServiceClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# All configurable values come from environment variables, never hard-coded.
# ---------------------------------------------------------------------------
DEFAULT_CLEAN_CONTAINER = os.getenv("CLEAN_CONTAINER_NAME", "clean")
DEFAULT_CLEAN_BLOB_NAME = os.getenv("CLEAN_BLOB_NAME", "adult_census_clean.parquet")
DEFAULT_QUEUE_NAME = os.getenv("TRAINING_QUEUE_NAME", "training-trigger")
STORAGE_CONNECTION_PREFIX = os.getenv("STORAGE_CONNECTION_PREFIX", "ELT_STORAGE")


def _require_env(name: str) -> str:
    """Return an environment variable value or raise a clear error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise OSError(f"Missing required environment variable: {name}")
    return value


@lru_cache(maxsize=1)
def _get_credential() -> DefaultAzureCredential:
    """Single cached credential for all Azure clients."""
    return DefaultAzureCredential()


def _resolve_blob_service_uri() -> str:
    """Resolve the blob service URL from environment."""
    return _require_env(f"{STORAGE_CONNECTION_PREFIX}__blobServiceUri").rstrip("/")


def _resolve_queue_service_uri() -> str:
    """Resolve the queue service URL from environment."""
    return _require_env(f"{STORAGE_CONNECTION_PREFIX}__queueServiceUri").rstrip("/")


@lru_cache(maxsize=1)
def get_blob_service_client() -> BlobServiceClient:
    """Create a BlobServiceClient using managed identity."""
    account_url = _resolve_blob_service_uri()
    logger.debug("Creating BlobServiceClient for %s", account_url)
    return BlobServiceClient(account_url=account_url, credential=_get_credential())


@lru_cache(maxsize=1)
def get_queue_service_client() -> QueueServiceClient:
    """Create a QueueServiceClient using managed identity."""
    account_url = _resolve_queue_service_uri()
    logger.debug("Creating QueueServiceClient for %s", account_url)
    return QueueServiceClient(account_url=account_url, credential=_get_credential())


def upload_parquet_to_blob(
    dataframe: pd.DataFrame,
    container_name: str | None = None,
    blob_name: str | None = None,
    blob_service_client: BlobServiceClient | None = None,
) -> None:
    """
    Upload a DataFrame as a Parquet blob.

    Uses DEFAULT_CLEAN_CONTAINER and DEFAULT_CLEAN_BLOB_NAME unless
    overridden, which read from env vars CLEAN_CONTAINER_NAME and CLEAN_BLOB_NAME.
    """
    target_container = container_name or DEFAULT_CLEAN_CONTAINER
    target_blob = blob_name or DEFAULT_CLEAN_BLOB_NAME

    client = blob_service_client or get_blob_service_client()
    container = client.get_container_client(target_container)

    # Create container if it doesn't exist (idempotent)
    try:
        container.create_container()
    except ResourceExistsError:
        pass

    buffer = io.BytesIO()
    dataframe.to_parquet(buffer, index=False, engine="pyarrow")
    buffer.seek(0)

    container.upload_blob(name=target_blob, data=buffer, overwrite=True)
    logger.info("Uploaded %d rows to %s/%s", len(dataframe), target_container, target_blob)


def send_training_trigger(
    message: str,
    queue_name: str | None = None,
    queue_service_client: QueueServiceClient | None = None,
) -> None:
    """
    Send a message to the ML training trigger queue.

    Uses DEFAULT_QUEUE_NAME unless overridden, which reads from TRAINING_QUEUE_NAME env var.
    """
    target_queue = queue_name or DEFAULT_QUEUE_NAME

    client = queue_service_client or get_queue_service_client()
    queue = client.get_queue_client(target_queue)

    try:
        queue.create_queue()
    except ResourceExistsError:
        pass

    queue.send_message(message)
    logger.info("Sent training trigger message to queue '%s'", target_queue)
