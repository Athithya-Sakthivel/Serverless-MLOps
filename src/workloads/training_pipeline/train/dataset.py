"""Load clean parquet, create binary target, and split deterministically."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from utils.storage import build_blob_service_client, download_blob_to_tempfile

LOG = logging.getLogger(__name__)

TARGET_COLUMN = "TARGET"
PINCP_COLUMN = "PINCP"
_SPLIT_INDEX_COLUMN = "__split_row_index__"


@dataclass(frozen=True, slots=True)
class DatasetSplits:
    """Train / validation / test Polars DataFrames."""

    train_frame: pl.DataFrame
    validation_frame: pl.DataFrame
    test_frame: pl.DataFrame
    target_column: str = TARGET_COLUMN


def load_clean_frame(
    *,
    storage_account_name: str,
    container_name: str,
    blob_name: str,
    blob_service_client: Any = None,
    credential: object | None = None,
) -> pl.DataFrame:
    """Download a clean parquet blob and load it into a Polars DataFrame."""
    if not container_name:
        raise ValueError("container_name is required")
    if not blob_name:
        raise ValueError("blob_name is required")

    if blob_service_client is None:
        blob_service_client = build_blob_service_client(
            storage_account_name,
            credential=credential,  # type: ignore[arg-type]
        )

    temp_path = download_blob_to_tempfile(
        blob_service_client,
        container_name=container_name,
        blob_name=blob_name,
        suffix=".parquet",
    )

    try:
        frame = pl.read_parquet(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if frame.height == 0:
        raise ValueError(f"Clean blob {container_name}/{blob_name} is empty")

    LOG.info(
        "Loaded clean blob %s/%s: %d rows, %d columns",
        container_name,
        blob_name,
        frame.height,
        frame.width,
    )
    return frame


def create_target_column(
    frame: pl.DataFrame,
    *,
    income_threshold: int = 50_000,
) -> pl.DataFrame:
    """Add a binary TARGET column based on PINCP >= threshold."""
    if PINCP_COLUMN not in frame.columns:
        raise ValueError(f"{PINCP_COLUMN} column is required")

    return frame.with_columns(
        pl.when(pl.col(PINCP_COLUMN).cast(pl.Float64, strict=False) >= income_threshold)
        .then(pl.lit(1))
        .otherwise(pl.lit(0))
        .cast(pl.Int64)
        .alias(TARGET_COLUMN)
    )


def _allocate_counts(total: int, fractions: tuple[float, float, float]) -> np.ndarray:
    """Allocate exact row counts using the largest remainder method."""
    raw = np.asarray(fractions, dtype=np.float64) * float(total)
    counts = np.floor(raw).astype(np.int64)
    remainder = total - int(counts.sum())

    if remainder > 0:
        remainders = raw - counts
        order = np.argsort(-remainders, kind="stable")
        for index in range(remainder):
            counts[order[index % len(counts)]] += 1

    return counts


def _subset_by_row_indices(frame: pl.DataFrame, indices: list[int]) -> pl.DataFrame:
    """Return rows in the given order, preserving the provided index order."""
    if not indices:
        return frame.head(0)

    indexed = frame.with_row_index(_SPLIT_INDEX_COLUMN)
    selected = indexed.filter(pl.col(_SPLIT_INDEX_COLUMN).is_in(indices)).drop(_SPLIT_INDEX_COLUMN)

    if selected.height != len(indices):
        raise RuntimeError(
            "Row selection produced an unexpected number of rows "
            f"(expected {len(indices)}, got {selected.height})"
        )

    return selected


def split_dataset(
    frame: pl.DataFrame,
    *,
    seed: int,
    train_fraction: float,
    validation_fraction: float,
    test_fraction: float,
    target_column: str = TARGET_COLUMN,
) -> DatasetSplits:
    """Stratified train / validation / test split with a fixed seed."""
    fractions = (train_fraction, validation_fraction, test_fraction)

    if any(fraction < 0 for fraction in fractions):
        raise ValueError("Split fractions must be non-negative")
    if not np.isclose(sum(fractions), 1.0):
        raise ValueError("Split fractions must sum to 1.0")
    if target_column not in frame.columns:
        raise ValueError(f"{target_column} column is required")
    if frame.height == 0:
        raise ValueError("Cannot split an empty frame")

    target_series = frame.get_column(target_column).cast(pl.Int64, strict=False)
    if target_series.null_count() > 0:
        raise ValueError(f"{target_column} contains null values")

    target_values = target_series.to_numpy()
    unique_values = set(np.unique(target_values).tolist())
    if not unique_values.issubset({0, 1}):
        raise ValueError(
            f"{target_column} must be binary with values in {{0, 1}}; found {sorted(unique_values)}"
        )

    rng = np.random.default_rng(seed)
    row_indices = np.arange(frame.height)

    train_indices: list[int] = []
    validation_indices: list[int] = []
    test_indices: list[int] = []

    for class_value in np.unique(target_values):
        class_mask = row_indices[target_values == class_value].copy()
        rng.shuffle(class_mask)

        train_n, validation_n, test_n = _allocate_counts(
            class_mask.size,
            fractions,
        )

        train_indices.extend(class_mask[:train_n].tolist())
        validation_indices.extend(class_mask[train_n : train_n + validation_n].tolist())
        test_indices.extend(class_mask[train_n + validation_n :].tolist())

    # Shuffle the order within each split (the final order is random)
    rng.shuffle(train_indices)
    rng.shuffle(validation_indices)
    rng.shuffle(test_indices)

    return DatasetSplits(
        train_frame=_subset_by_row_indices(frame, train_indices),
        validation_frame=_subset_by_row_indices(frame, validation_indices),
        test_frame=_subset_by_row_indices(frame, test_indices),
        target_column=target_column,
    )
