"""Validate raw parquet data before transformation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import polars as pl

LOG = logging.getLogger(__name__)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "AGEP",
    "COW",
    "SCHL",
    "MAR",
    "OCCP",
    "POBP",
    "RELP",
    "WKHP",
    "SEX",
    "RAC1P",
    "STATE",
    "YEAR",
    "PINCP",
)

OPTIONAL_COLUMNS: tuple[str, ...] = ("__index_level_0__",)

NUMERIC_COLUMNS: tuple[str, ...] = (
    "AGEP",
    "COW",
    "SCHL",
    "MAR",
    "OCCP",
    "POBP",
    "RELP",
    "WKHP",
    "SEX",
    "RAC1P",
    "YEAR",
    "PINCP",
)

STATE_PATTERN = re.compile(r"^[A-Z]{2}$")


class ValidationError(ValueError):
    """Raised when the raw dataset is not acceptable for downstream processing."""


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Validation thresholds for the fixed ACS dataset."""

    max_null_rate_by_column: dict[str, float] = field(
        default_factory=lambda: {
            "AGEP": 0.005,
            "COW": 0.02,
            "SCHL": 0.02,
            "MAR": 0.02,
            "OCCP": 0.05,
            "POBP": 0.05,
            "RELP": 0.02,
            "WKHP": 0.01,
            "SEX": 0.01,
            "RAC1P": 0.01,
            "STATE": 0.0,
            "YEAR": 0.0,
            "PINCP": 0.0,
        }
    )
    min_rows: int = 1
    max_duplicate_rate: float = 0.20
    max_invalid_state_rate: float = 0.01
    min_year: int = 2010
    max_year: int = 2035
    min_age: int = 0
    max_age: int = 115
    min_work_hours: int = 0
    max_work_hours: int = 168


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Structured validation summary that can be written to checkpoints."""

    row_count: int
    column_names: tuple[str, ...]
    duplicate_rows: int
    invalid_state_rows: int
    warnings: tuple[str, ...] = ()
    null_counts: dict[str, int] = field(default_factory=dict)

    @property
    def duplicate_rate(self) -> float:
        if self.row_count == 0:
            return 0.0
        return self.duplicate_rows / self.row_count

    @property
    def invalid_state_rate(self) -> float:
        if self.row_count == 0:
            return 0.0
        return self.invalid_state_rows / self.row_count


def _dtype_name(dtype: pl.DataType) -> str:
    return str(dtype)


def _is_numeric_dtype(dtype: pl.DataType) -> bool:
    name = _dtype_name(dtype)
    return name.startswith(("Int", "UInt", "Float", "Decimal"))


def validate_raw_frame(
    frame: pl.DataFrame,
    config: ValidationConfig | None = None,
) -> ValidationReport:
    """Validate the raw frame and raise on hard failures."""

    config = config or ValidationConfig()

    if frame.height < config.min_rows:
        raise ValidationError("Raw frame is empty")

    missing_columns = [name for name in REQUIRED_COLUMNS if name not in frame.columns]
    if missing_columns:
        raise ValidationError("Missing required columns: " + ", ".join(sorted(missing_columns)))

    extra_columns = [
        name
        for name in frame.columns
        if name not in REQUIRED_COLUMNS and name not in OPTIONAL_COLUMNS
    ]
    if extra_columns:
        LOG.warning("Unexpected extra columns present: %s", ", ".join(extra_columns))

    invalid_type_columns: list[str] = []
    schema = frame.schema
    for column_name in REQUIRED_COLUMNS:
        dtype = schema[column_name]
        if column_name == "STATE":
            if _dtype_name(dtype) != "String":
                invalid_type_columns.append(f"{column_name}={dtype}")
            continue
        if not _is_numeric_dtype(dtype):
            invalid_type_columns.append(f"{column_name}={dtype}")

    if invalid_type_columns:
        raise ValidationError("Unexpected column types: " + ", ".join(invalid_type_columns))

    null_counts: dict[str, int] = {
        column_name: int(frame.select(pl.col(column_name).null_count()).item())
        for column_name in REQUIRED_COLUMNS
    }
    warnings: list[str] = []

    for column_name, max_null_rate in config.max_null_rate_by_column.items():
        null_rate = null_counts[column_name] / frame.height
        if null_rate > max_null_rate:
            raise ValidationError(
                f"Null rate for {column_name} is {null_rate:.4%}, above allowed {max_null_rate:.4%}"
            )
        if null_rate > 0:
            warnings.append(f"{column_name} null_rate={null_rate:.4%}")

    duplicate_rows = frame.height - frame.unique(maintain_order=True).height
    if duplicate_rows / frame.height > config.max_duplicate_rate:
        raise ValidationError(
            f"Duplicate rate is {duplicate_rows / frame.height:.4%}, above allowed {config.max_duplicate_rate:.4%}"
        )
    if duplicate_rows > 0:
        warnings.append(f"duplicate_rows={duplicate_rows}")

    invalid_state_rows = frame.filter(
        ~pl.col("STATE")
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .str.contains(STATE_PATTERN.pattern, literal=False)
    ).height
    if invalid_state_rows / frame.height > config.max_invalid_state_rate:
        raise ValidationError(
            f"Invalid STATE rate is {invalid_state_rows / frame.height:.4%}, above allowed {config.max_invalid_state_rate:.4%}"
        )
    if invalid_state_rows > 0:
        warnings.append(f"invalid_state_rows={invalid_state_rows}")

    bad_year_rows = frame.filter(
        (pl.col("YEAR").cast(pl.Int64, strict=False) < config.min_year)
        | (pl.col("YEAR").cast(pl.Int64, strict=False) > config.max_year)
    ).height
    if bad_year_rows > 0:
        warnings.append(f"year_out_of_range_rows={bad_year_rows}")

    bad_age_rows = frame.filter(
        (pl.col("AGEP").cast(pl.Float64, strict=False) < config.min_age)
        | (pl.col("AGEP").cast(pl.Float64, strict=False) > config.max_age)
    ).height
    if bad_age_rows > 0:
        warnings.append(f"age_out_of_range_rows={bad_age_rows}")

    bad_hours_rows = frame.filter(
        (pl.col("WKHP").cast(pl.Float64, strict=False) < config.min_work_hours)
        | (pl.col("WKHP").cast(pl.Float64, strict=False) > config.max_work_hours)
    ).height
    if bad_hours_rows > 0:
        warnings.append(f"work_hours_out_of_range_rows={bad_hours_rows}")

    report = ValidationReport(
        row_count=frame.height,
        column_names=tuple(frame.columns),
        duplicate_rows=duplicate_rows,
        invalid_state_rows=invalid_state_rows,
        warnings=tuple(warnings),
        null_counts=null_counts,
    )

    LOG.info(
        "Validation passed for %d rows (%d duplicates, %d invalid states)",
        report.row_count,
        report.duplicate_rows,
        report.invalid_state_rows,
    )
    for warning in report.warnings:
        LOG.warning("Validation warning: %s", warning)

    return report
