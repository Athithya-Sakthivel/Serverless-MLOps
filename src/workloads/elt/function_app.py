"""Azure Function entry point for the ELT pipeline."""

from __future__ import annotations

import json
import logging
import os
from pathlib import PurePosixPath

import azure.functions as func
from src.extract import read_parquet_from_blob
from src.load import (
    DEFAULT_CLEAN_BLOB_NAME,
    DEFAULT_CLEAN_CONTAINER,
    DEFAULT_QUEUE_NAME,
    STORAGE_CONNECTION_PREFIX,
    get_blob_service_client,
    send_training_trigger,
    upload_parquet_to_blob,
)
from src.transform import transform

app = func.FunctionApp()
logger = logging.getLogger(__name__)


def _is_truthy(value: str | None) -> bool:
    """Parse a permissive boolean flag from environment variables."""

    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@app.function_name(name="OnRawData")
@app.blob_trigger(
    arg_name="raw_blob",
    path="raw/monthly/{name}",
    connection=STORAGE_CONNECTION_PREFIX,
    source=func.BlobSource.EVENT_GRID,
)
def transform_new_data(raw_blob: func.InputStream) -> None:
    """Transform a new Parquet blob and write the cleaned output."""

    blob_path = PurePosixPath(raw_blob.name)
    blob_name = blob_path.name
    blob_length = getattr(raw_blob, "length", None)

    logger.info(
        "Blob trigger fired for %s (%s bytes)",
        raw_blob.name,
        blob_length if blob_length is not None else "unknown",
    )

    if not blob_name.lower().endswith(".parquet"):
        logger.info("Skipping non-Parquet blob %s", raw_blob.name)
        return

    try:
        source_bytes = raw_blob.read()
        extracted = read_parquet_from_blob(source_bytes, filename=blob_name)
        cleaned = transform(extracted)

        blob_service_client = get_blob_service_client()
        upload_parquet_to_blob(
            cleaned,
            container_name=DEFAULT_CLEAN_CONTAINER,
            blob_name=DEFAULT_CLEAN_BLOB_NAME,
            blob_service_client=blob_service_client,
        )

        if _is_truthy(os.getenv("TRIGGER_ML_TRAINING")):
            training_message = json.dumps(
                {
                    "event": "new_clean_data",
                    "source_blob": blob_name,
                    "rows": int(len(cleaned)),
                    "output_container": DEFAULT_CLEAN_CONTAINER,
                    "output_blob": DEFAULT_CLEAN_BLOB_NAME,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            send_training_trigger(
                message=training_message,
                queue_name=DEFAULT_QUEUE_NAME,
            )

    except Exception:
        logger.exception("Pipeline failed for blob %s", raw_blob.name)
        raise
