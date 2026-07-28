"""Tests for LightGBM model construction and training."""

from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from train.model import TrainedModel, build_classifier, train_lightgbm_classifier
from utils.config import TrainingConfig


@pytest.fixture
def default_config() -> TrainingConfig:
    return TrainingConfig(random_seed=42, num_boost_round=10, early_stopping_rounds=3)


@pytest.fixture
def dummy_data() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    X = rng.standard_normal((100, 3)).astype(np.float32)
    y = (X[:, 0] > 0).astype(np.int64)
    X_val = rng.standard_normal((30, 3)).astype(np.float32)
    y_val = (X_val[:, 0] > 0).astype(np.int64)
    return X, y, X_val, y_val


def test_build_classifier_uses_config(default_config):
    clf = build_classifier(default_config)
    assert isinstance(clf, lgb.LGBMClassifier)
    assert clf.get_params()["random_state"] == 42
    assert clf.get_params()["n_estimators"] == 10


def test_train_lightgbm_classifier(default_config, dummy_data):
    X, y, X_val, y_val = dummy_data
    clf = build_classifier(default_config)
    trained = train_lightgbm_classifier(
        clf,
        X_train=X,
        y_train=y,
        X_validation=X_val,
        y_validation=y_val,
        early_stopping_rounds=3,
    )
    assert isinstance(trained, TrainedModel)
    assert isinstance(trained.model, lgb.LGBMClassifier)
    assert trained.best_iteration is not None


def test_train_requires_non_empty_splits(default_config):
    clf = build_classifier(default_config)
    empty = np.empty((0, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="must not be empty"):
        train_lightgbm_classifier(
            clf,
            X_train=empty,
            y_train=np.array([], dtype=np.int64),
            X_validation=empty,
            y_validation=np.array([], dtype=np.int64),
            early_stopping_rounds=3,
        )


def test_train_dimension_mismatch_raises(default_config, dummy_data):
    X, y, X_val, y_val = dummy_data
    clf = build_classifier(default_config)
    with pytest.raises(ValueError, match="same number of rows"):
        train_lightgbm_classifier(
            clf,
            X_train=X,
            y_train=y[:10],
            X_validation=X_val,
            y_validation=y_val,
            early_stopping_rounds=3,
        )


def test_train_early_stopping_rounds_positive(default_config, dummy_data):
    X, y, X_val, y_val = dummy_data
    clf = build_classifier(default_config)
    with pytest.raises(ValueError, match="at least 1"):
        train_lightgbm_classifier(
            clf,
            X_train=X,
            y_train=y,
            X_validation=X_val,
            y_validation=y_val,
            early_stopping_rounds=0,
        )
