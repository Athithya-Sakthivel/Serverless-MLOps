from __future__ import annotations

import logging

import azure.functions as func
from shared.aca import start_training_job
from shared.config import get_settings

# Blueprint keeps trigger definitions modular. The root FunctionApp simply
# imports and registers this blueprint during startup.
blob_bp = func.Blueprint()

logger = logging.getLogger(__name__)


@blob_bp.blob_trigger(
    arg_name="blob",
    # Only blobs under raw/monthly/ invoke this function. Uploads elsewhere
    # in the storage account are ignored by the Functions runtime.
    path="raw/monthly/{name}",
    # SOURCE_STORAGE resolves to either a connection string (local
    # development) or a managed identity connection prefix in Azure.
    connection="SOURCE_STORAGE",
)
def blob_created(blob: func.InputStream) -> None:
    settings = get_settings()

    # InputStream metadata is populated by the Functions runtime without
    # downloading the entire blob into memory.
    blob_name = (getattr(blob, "name", "") or "").strip()
    blob_length = getattr(blob, "length", None)

    if not blob_name:
        logger.warning("Blob trigger fired without a blob name; skipping.")
        return

    logger.info(
        "Blob trigger fired for blob=%s length=%s bytes; starting ACA job=%s.",
        blob_name,
        blob_length,
        settings.job_name,
    )

    # The Function performs orchestration only. The training logic,
    # checkpointing and ML execution remain inside the Container Apps Job.
    start_training_job(
        settings=settings,
        logger=logger,
    )
