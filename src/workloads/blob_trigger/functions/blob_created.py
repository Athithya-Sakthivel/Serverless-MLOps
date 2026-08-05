"""
Blob trigger function that starts the ACA training job when a new Parquet
file lands in the raw/monthly/ container.

Flex Consumption requires Event Grid-based blob triggers.  The decorator
uses `source=func.BlobSource.EVENT_GRID` to tell the runtime to expect
events from Event Grid rather than using the legacy polling mechanism.

The Event Grid subscription that delivers these events is created by
run.sh after the Function code is deployed, because the blobs_extension
system key only becomes available once the host has indexed this trigger.
"""

from __future__ import annotations

import logging

import azure.functions as func
from shared.aca import start_training_job
from shared.config import get_settings

blob_bp = func.Blueprint()
logger = logging.getLogger(__name__)


@blob_bp.function_name(name="blob_created")
@blob_bp.blob_trigger(
    arg_name="blob",
    path="raw/monthly/{name}",  # only Parquet files under raw/monthly/
    connection="SOURCE_STORAGE",  # identity-based connection (managed identity)
    source=func.BlobSource.EVENT_GRID,  # required for Flex Consumption
)
def blob_created(blob: func.InputStream) -> None:
    """
    Called by the Azure Functions runtime whenever a new blob matches
    the trigger path.  Starts the ACA training job via ARM REST API,
    passing the blob name so the pipeline knows which file to process.
    """
    settings = get_settings()

    # The runtime may fire the trigger during host startup with an empty
    # blob name.  Ignore those invocations.
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

    start_training_job(
        settings=settings,
        logger=logger,
        blob_name=blob_name,
    )
