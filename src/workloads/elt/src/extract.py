"""Blob extraction helpers for the ELT workload."""

from __future__ import annotations

import io
import logging

import pandas as pd

logger = logging.getLogger(__name__)


def read_parquet_from_blob(
    blob_data: bytes | bytearray | memoryview, filename: str = "unknown"
) -> pd.DataFrame:
    """Read raw Parquet bytes into a DataFrame."""

    if not isinstance(blob_data, (bytes, bytearray, memoryview)):
        raise TypeError("blob_data must be bytes-like")

    raw_bytes = bytes(blob_data)
    if not raw_bytes:
        raise ValueError(f"Blob {filename} is empty")

    try:
        dataframe = pd.read_parquet(io.BytesIO(raw_bytes), engine="pyarrow")
    except Exception as exc:
        logger.exception("Failed to read parquet blob %s", filename)
        raise ValueError(f"Invalid Parquet data in {filename}") from exc

    logger.info(
        "Loaded parquet blob %s with %d rows and %d columns",
        filename,
        len(dataframe),
        len(dataframe.columns),
    )

    if dataframe.empty:
        logger.warning("Parquet blob %s produced an empty dataframe", filename)

    return dataframe
