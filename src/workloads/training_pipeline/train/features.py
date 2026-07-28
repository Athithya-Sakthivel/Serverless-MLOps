"""Feature engineering for the cleaned ACS dataset."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from train.dataset import TARGET_COLUMN

NUMERIC_FEATURE_COLUMNS: tuple[str, ...] = (
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
)

STATE_COLUMN = "STATE"
STATE_FEATURE_PREFIX = f"{STATE_COLUMN}__"


@dataclass(frozen=True, slots=True)
class FeatureTransformer:
    """Fitted encoder that knows the exact feature order."""

    state_categories: tuple[str, ...]

    @classmethod
    def fit(cls, frame: pl.DataFrame) -> FeatureTransformer:
        """Learn state category vocabulary from the training split."""
        if frame.height == 0:
            raise ValueError("Cannot fit FeatureTransformer on an empty frame")
        if STATE_COLUMN not in frame.columns:
            raise ValueError(f"{STATE_COLUMN} column is required")

        state_values = (
            frame.get_column(STATE_COLUMN)
            .cast(pl.Utf8, strict=False)
            .str.strip_chars()
            .str.to_uppercase()
            .drop_nulls()
            .unique()
            .sort()
            .to_list()
        )

        if not state_values:
            raise ValueError(f"No non-null {STATE_COLUMN} categories were found")

        return cls(state_categories=tuple(str(value) for value in state_values))

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Ordered list of feature names the model expects."""
        return NUMERIC_FEATURE_COLUMNS + tuple(
            f"{STATE_FEATURE_PREFIX}{category}" for category in self.state_categories
        )

    def transform(self, frame: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return (features, target) as float32 and int64 arrays."""
        required_columns = (*NUMERIC_FEATURE_COLUMNS, STATE_COLUMN, TARGET_COLUMN)
        missing_columns = [column for column in required_columns if column not in frame.columns]
        if missing_columns:
            raise ValueError("Missing required columns: " + ", ".join(missing_columns))

        numeric_frame = frame.select(
            [
                pl.col(column).cast(pl.Float64, strict=False).alias(column)
                for column in NUMERIC_FEATURE_COLUMNS
            ]
        )
        numeric_nulls = numeric_frame.null_count().row(0, named=True)
        invalid_numeric = [name for name, count in numeric_nulls.items() if count]
        if invalid_numeric:
            raise ValueError(
                "Numeric feature columns contain null values: " + ", ".join(invalid_numeric)
            )

        numeric_frame = numeric_frame.with_columns(
            [pl.col(column).cast(pl.Float32) for column in NUMERIC_FEATURE_COLUMNS]
        )

        state_series = (
            frame.get_column(STATE_COLUMN)
            .cast(pl.Utf8, strict=False)
            .str.strip_chars()
            .str.to_uppercase()
        )
        state_dummies = state_series.to_dummies(separator="__", drop_nulls=True)

        if state_dummies.width:
            state_dummies = state_dummies.with_columns(pl.all().cast(pl.Float32))

        expected_state_columns = [
            f"{STATE_FEATURE_PREFIX}{category}" for category in self.state_categories
        ]
        for column in expected_state_columns:
            if column not in state_dummies.columns:
                state_dummies = state_dummies.with_columns(
                    pl.lit(0.0).cast(pl.Float32).alias(column)
                )

        state_dummies = state_dummies.select(expected_state_columns)

        features_frame = pl.concat([numeric_frame, state_dummies], how="horizontal_extend")
        features = features_frame.to_numpy().astype(np.float32, copy=False)

        target_series = frame.get_column(TARGET_COLUMN).cast(pl.Int64, strict=False)
        if target_series.null_count() > 0:
            raise ValueError(f"{TARGET_COLUMN} contains null values")

        target = target_series.to_numpy().astype(np.int64, copy=False)
        unique_target_values = set(np.unique(target).tolist())
        if not unique_target_values.issubset({0, 1}):
            raise ValueError(
                f"{TARGET_COLUMN} must be binary with values in {{0, 1}}; "
                f"found {sorted(unique_target_values)}"
            )

        return features, target


@dataclass(frozen=True, slots=True)
class FeatureBundle:
    """Prepared train/val/test arrays and the fitted transformer."""

    transformer: FeatureTransformer
    X_train: np.ndarray
    y_train: np.ndarray
    X_validation: np.ndarray
    y_validation: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray


def build_feature_bundle(
    train_frame: pl.DataFrame,
    validation_frame: pl.DataFrame,
    test_frame: pl.DataFrame,
) -> FeatureBundle:
    """Fit transformer on train, then transform all splits."""
    transformer = FeatureTransformer.fit(train_frame)
    X_train, y_train = transformer.transform(train_frame)
    X_validation, y_validation = transformer.transform(validation_frame)
    X_test, y_test = transformer.transform(test_frame)

    return FeatureBundle(
        transformer=transformer,
        X_train=X_train,
        y_train=y_train,
        X_validation=X_validation,
        y_validation=y_validation,
        X_test=X_test,
        y_test=y_test,
    )
