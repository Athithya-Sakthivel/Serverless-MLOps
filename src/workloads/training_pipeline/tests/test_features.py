"""Tests for feature engineering."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from train.dataset import TARGET_COLUMN
from train.features import (
    NUMERIC_FEATURE_COLUMNS,
    STATE_COLUMN,
    FeatureTransformer,
    build_feature_bundle,
)


def _sample_train_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "AGEP": [30, 45],
            "COW": [1, 2],
            "SCHL": [16, 21],
            "MAR": [1, 3],
            "OCCP": [1024, 2048],
            "POBP": [6, 12],
            "RELP": [0, 1],
            "WKHP": [40, 35],
            "SEX": [1, 2],
            "RAC1P": [1, 2],
            "YEAR": [2023, 2024],
            "STATE": ["NY", "CA"],
            "PINCP": [60000, 45000],
            TARGET_COLUMN: [1, 0],
        }
    )


def test_fit_feature_transformer():
    df = _sample_train_frame()
    trans = FeatureTransformer.fit(df)
    # states are sorted
    assert trans.state_categories == ("CA", "NY")
    expected_names = list(NUMERIC_FEATURE_COLUMNS) + ["STATE__CA", "STATE__NY"]
    assert list(trans.feature_names) == expected_names


def test_fit_empty_frame_raises():
    empty = pl.DataFrame()
    with pytest.raises(ValueError, match="empty frame"):
        FeatureTransformer.fit(empty)


def test_fit_missing_state_column():
    df = _sample_train_frame().drop(STATE_COLUMN)
    with pytest.raises(ValueError, match=STATE_COLUMN):
        FeatureTransformer.fit(df)


def test_transform_returns_correct_shapes():
    df = _sample_train_frame()
    trans = FeatureTransformer.fit(df)
    X, y = trans.transform(df)
    assert X.shape == (2, len(trans.feature_names))
    assert y.shape == (2,)
    assert X.dtype == np.float32
    assert y.dtype == np.int64
    np.testing.assert_array_equal(y, np.array([1, 0], dtype=np.int64))


def test_transform_missing_required_columns():
    df = _sample_train_frame().drop("AGEP")
    trans = FeatureTransformer.fit(_sample_train_frame())  # fit on full
    with pytest.raises(ValueError, match="Missing required columns"):
        trans.transform(df)


def test_transform_nulls_in_numeric_features_raises():
    df = _sample_train_frame().with_columns(pl.lit(None).cast(pl.Float64).alias("AGEP"))
    trans = FeatureTransformer.fit(_sample_train_frame())
    with pytest.raises(ValueError, match="null values"):
        trans.transform(df)


def test_transform_nulls_in_target_raises():
    df = _sample_train_frame().with_columns(pl.lit(None).cast(pl.Int64).alias(TARGET_COLUMN))
    trans = FeatureTransformer.fit(_sample_train_frame())
    with pytest.raises(ValueError, match="null values"):
        trans.transform(df)


def test_transform_binary_target_fails_for_non_binary():
    df = _sample_train_frame().with_columns(pl.lit(2).cast(pl.Int64).alias(TARGET_COLUMN))
    trans = FeatureTransformer.fit(_sample_train_frame())
    with pytest.raises(ValueError, match="binary"):
        trans.transform(df)


def test_build_feature_bundle():
    train = _sample_train_frame()
    val = train.clone()
    test = train.clone()
    bundle = build_feature_bundle(train, val, test)
    assert bundle.transformer.state_categories == ("CA", "NY")
    assert bundle.X_train.shape == (2, len(bundle.transformer.feature_names))
    assert bundle.X_validation.shape == bundle.X_train.shape
    assert bundle.X_test.shape == bundle.X_train.shape
    assert bundle.y_train.shape == (2,)
    assert bundle.y_validation.shape == (2,)
    assert bundle.y_test.shape == (2,)
