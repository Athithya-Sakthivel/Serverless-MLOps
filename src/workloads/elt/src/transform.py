"""Pure transformation functions for the Adult Census Income dataset."""

from __future__ import annotations

import logging
import re
from collections import Counter

import pandas as pd
from pandas.api.types import CategoricalDtype, is_integer_dtype

logger = logging.getLogger(__name__)

TARGET_COLUMN = "income"
AGE_GROUP_LABELS = ("young", "adult", "middle_age", "senior")
RAW_REQUIRED_COLUMNS = frozenset(
    {
        "income",
        "age",
        "workclass",
        "fnlwgt",
        "education",
        "education_num",
        "marital_status",
        "occupation",
        "relationship",
        "race",
        "sex",
        "capital_gain",
        "capital_loss",
        "hours_per_week",
        "native_country",
    }
)
NUMERIC_COLUMNS = (
    "age",
    "fnlwgt",
    "education_num",
    "capital_gain",
    "capital_loss",
    "hours_per_week",
)
FEATURE_COLUMNS = ("net_capital", "age_group")
PLACEHOLDER_TOKENS: frozenset[str] = frozenset({"", "?", "nan", "none", "null"})


def normalize_column_names(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Standardize column names to snake_case."""
    normalized_columns = [
        re.sub(r"[^0-9a-zA-Z]+", "_", str(column).strip().lower()).strip("_")
        for column in dataframe.columns
    ]

    duplicate_columns = sorted(
        column for column, count in Counter(normalized_columns).items() if count > 1
    )
    if duplicate_columns:
        raise ValueError(f"Column normalization created duplicates: {duplicate_columns}")

    normalized = dataframe.copy()
    normalized.columns = pd.Index(normalized_columns)
    return normalized


def standardize_string_values(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Trim text fields and convert placeholder tokens to missing values."""
    cleaned = dataframe.copy()
    text_columns = cleaned.select_dtypes(include=["object", "string", "category"]).columns

    for column in text_columns:
        series = cleaned[column].astype("string").str.strip()
        placeholder_mask = series.str.lower().isin(PLACEHOLDER_TOKENS)
        cleaned[column] = series.where(~placeholder_mask, pd.NA)

    return cleaned


def ensure_required_columns(dataframe: pd.DataFrame) -> None:
    """Fail fast when a required source column is missing."""
    missing = RAW_REQUIRED_COLUMNS.difference(dataframe.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")


def drop_rows_with_missing_required_values(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Remove rows that still contain missing values in required columns."""
    ensure_required_columns(dataframe)
    before_count = len(dataframe)
    cleaned = dataframe.dropna(subset=sorted(RAW_REQUIRED_COLUMNS)).copy()
    cleaned = cleaned.reset_index(drop=True)
    dropped_count = before_count - len(cleaned)

    if dropped_count:
        logger.info("Dropped %d rows with missing required values", dropped_count)

    return cleaned


def coerce_numeric_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Convert expected numeric columns to numeric dtype."""
    coerced = dataframe.copy()

    for column in NUMERIC_COLUMNS:
        if column not in coerced.columns:
            raise ValueError(f"Missing required numeric column: {column}")

        coerced[column] = pd.to_numeric(coerced[column], errors="raise")

        if not is_integer_dtype(coerced[column]):
            raise ValueError(f"{column} must be integer typed after coercion")

    return coerced


def encode_target(dataframe: pd.DataFrame, target_col: str = TARGET_COLUMN) -> pd.DataFrame:
    """Convert income labels to 0/1 integers."""
    if target_col not in dataframe.columns:
        raise ValueError(f"Missing target column: {target_col}")

    encoded = dataframe.copy()
    normalized_target = (
        encoded[target_col]
        .astype("string")
        .str.strip()
        .str.lower()
        .str.replace(r"\.$", "", regex=True)
    )

    mapping = {"<=50k": 0, ">50k": 1}
    encoded_target = normalized_target.map(mapping)

    invalid_mask = encoded_target.isna() & normalized_target.notna()
    if invalid_mask.any():
        bad_values = sorted(normalized_target[invalid_mask].dropna().unique().tolist())
        raise ValueError(f"Unexpected target values in {target_col}: {bad_values}")

    encoded[target_col] = encoded_target.astype("int64")
    return encoded


def engineer_features(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic derived features."""
    engineered = dataframe.copy()

    missing_capital_columns = {"capital_gain", "capital_loss"}.difference(engineered.columns)
    if missing_capital_columns:
        raise ValueError("capital_gain and capital_loss are required for feature engineering")

    engineered["net_capital"] = engineered["capital_gain"] - engineered["capital_loss"]

    if "age" not in engineered.columns:
        raise ValueError("age is required for feature engineering")

    engineered["age_group"] = pd.cut(
        engineered["age"],
        bins=[float("-inf"), 25, 45, 65, float("inf")],
        labels=list(AGE_GROUP_LABELS),
        right=False,
        include_lowest=True,
        ordered=True,
    )

    return engineered


def validate_transformed(dataframe: pd.DataFrame) -> None:
    """Assert the final dataframe is fit for downstream training."""
    if dataframe.empty:
        raise AssertionError("Transformed dataframe is empty")

    required_columns = set(RAW_REQUIRED_COLUMNS) | set(FEATURE_COLUMNS)
    missing_columns = required_columns.difference(dataframe.columns)
    if missing_columns:
        raise AssertionError(f"Missing required columns: {sorted(missing_columns)}")

    null_counts = dataframe.isna().sum()
    null_series = null_counts[null_counts > 0]
    if len(null_series) > 0:
        null_columns = sorted(dataframe.columns[null_counts > 0].tolist())
        raise AssertionError(f"Null values found in columns: {null_columns}")

    if not is_integer_dtype(dataframe[TARGET_COLUMN]):
        raise AssertionError(f"{TARGET_COLUMN} must be an integer column")

    invalid_targets = set(dataframe[TARGET_COLUMN].unique()).difference({0, 1})
    if invalid_targets:
        raise AssertionError(f"Target column must contain only 0 and 1: {sorted(invalid_targets)}")

    expected_net_capital = dataframe["capital_gain"] - dataframe["capital_loss"]
    if not expected_net_capital.equals(dataframe["net_capital"]):
        raise AssertionError("net_capital does not match capital_gain - capital_loss")

    age_group_dtype = dataframe["age_group"].dtype
    if not isinstance(age_group_dtype, CategoricalDtype):
        raise AssertionError("age_group must be a categorical column")

    if not age_group_dtype.ordered:
        raise AssertionError("age_group must be ordered")

    if list(age_group_dtype.categories) != list(AGE_GROUP_LABELS):
        raise AssertionError("age_group categories do not match the expected labels")

    if bool(dataframe["age_group"].isna().any()):
        raise AssertionError("age_group contains null values")

    logger.info(
        "Data validation passed: %d rows, %d columns", len(dataframe), len(dataframe.columns)
    )


def transform(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Run the full transformation pipeline."""
    logger.info("Starting transformation")
    transformed = (
        dataframe.pipe(normalize_column_names)
        .pipe(standardize_string_values)
        .pipe(drop_rows_with_missing_required_values)
        .pipe(coerce_numeric_columns)
        .pipe(encode_target)
        .pipe(engineer_features)
    )
    validate_transformed(transformed)
    logger.info(
        "Transformation complete: %d rows, %d columns",
        len(transformed),
        len(transformed.columns),
    )
    return transformed
