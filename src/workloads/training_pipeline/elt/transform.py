"""Clean and standardize the ACS dataset."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import polars as pl

from .validate import REQUIRED_COLUMNS, ValidationReport

LOG = logging.getLogger(__name__)

OUTPUT_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS


@dataclass(frozen=True, slots=True)
class TransformMetrics:
    input_rows: int
    output_rows: int
    duplicates_removed: int
    null_rows_removed: int
    invalid_state_rows_removed: int
    invalid_age_rows_removed: int
    invalid_hours_rows_removed: int
    invalid_year_rows_removed: int
    warnings: tuple[str, ...] = ()

    @property
    def rows_removed(self) -> int:
        return self.input_rows - self.output_rows


def _normalize_state_column(frame: pl.DataFrame) -> pl.DataFrame:
    if "STATE" not in frame.columns:
        return frame
    return frame.with_columns(
        pl.col("STATE")
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("STATE")
    )


def _coerce_numeric_columns(frame: pl.DataFrame) -> pl.DataFrame:
    numeric_casts = [
        pl.col("AGEP").cast(pl.Float64, strict=False).alias("AGEP"),
        pl.col("COW").cast(pl.Float64, strict=False).alias("COW"),
        pl.col("SCHL").cast(pl.Float64, strict=False).alias("SCHL"),
        pl.col("MAR").cast(pl.Float64, strict=False).alias("MAR"),
        pl.col("OCCP").cast(pl.Float64, strict=False).alias("OCCP"),
        pl.col("POBP").cast(pl.Float64, strict=False).alias("POBP"),
        pl.col("RELP").cast(pl.Float64, strict=False).alias("RELP"),
        pl.col("WKHP").cast(pl.Float64, strict=False).alias("WKHP"),
        pl.col("SEX").cast(pl.Float64, strict=False).alias("SEX"),
        pl.col("RAC1P").cast(pl.Float64, strict=False).alias("RAC1P"),
        pl.col("YEAR").cast(pl.Int64, strict=False).alias("YEAR"),
        pl.col("PINCP").cast(pl.Float64, strict=False).alias("PINCP"),
    ]
    return frame.with_columns(numeric_casts)


def clean_raw_frame(
    frame: pl.DataFrame,
    validation_report: ValidationReport | None = None,
) -> tuple[pl.DataFrame, TransformMetrics]:
    input_rows = frame.height
    working = frame.clone()

    if "__index_level_0__" in working.columns:
        working = working.drop("__index_level_0__")

    working = _normalize_state_column(working)
    working = _coerce_numeric_columns(working)

    null_mask = pl.any_horizontal(
        *[pl.col(name).is_null() for name in REQUIRED_COLUMNS if name in working.columns]
    )
    null_rows_removed = working.filter(null_mask).height
    working = working.filter(~null_mask)

    invalid_state_mask = ~pl.col("STATE").str.contains(r"^[A-Z]{2}$", literal=False)
    invalid_state_rows_removed = working.filter(invalid_state_mask).height
    working = working.filter(~invalid_state_mask)

    invalid_age_mask = (pl.col("AGEP") < 0) | (pl.col("AGEP") > 115)
    invalid_age_rows_removed = working.filter(invalid_age_mask).height
    working = working.filter(~invalid_age_mask)

    invalid_hours_mask = (pl.col("WKHP") < 0) | (pl.col("WKHP") > 168)
    invalid_hours_rows_removed = working.filter(invalid_hours_mask).height
    working = working.filter(~invalid_hours_mask)

    invalid_year_mask = (pl.col("YEAR") < 2010) | (pl.col("YEAR") > 2035)
    invalid_year_rows_removed = working.filter(invalid_year_mask).height
    working = working.filter(~invalid_year_mask)

    before_dedup = working.height
    working = working.unique(maintain_order=True)
    duplicates_removed = before_dedup - working.height

    working = working.select([name for name in OUTPUT_COLUMNS if name in working.columns])
    working = working.with_columns(
        pl.col("AGEP").cast(pl.Float64),
        pl.col("COW").cast(pl.Float64),
        pl.col("SCHL").cast(pl.Float64),
        pl.col("MAR").cast(pl.Float64),
        pl.col("OCCP").cast(pl.Float64),
        pl.col("POBP").cast(pl.Float64),
        pl.col("RELP").cast(pl.Float64),
        pl.col("WKHP").cast(pl.Float64),
        pl.col("SEX").cast(pl.Float64),
        pl.col("RAC1P").cast(pl.Float64),
        pl.col("YEAR").cast(pl.Int64),
        pl.col("PINCP").cast(pl.Float64),
    )

    metrics = TransformMetrics(
        input_rows=input_rows,
        output_rows=working.height,
        duplicates_removed=duplicates_removed,
        null_rows_removed=null_rows_removed,
        invalid_state_rows_removed=invalid_state_rows_removed,
        invalid_age_rows_removed=invalid_age_rows_removed,
        invalid_hours_rows_removed=invalid_hours_rows_removed,
        invalid_year_rows_removed=invalid_year_rows_removed,
        warnings=validation_report.warnings if validation_report else (),
    )

    LOG.info(
        "Transform complete: %d -> %d rows (%d removed, %d duplicates)",
        metrics.input_rows,
        metrics.output_rows,
        metrics.rows_removed,
        metrics.duplicates_removed,
    )
    return working, metrics
