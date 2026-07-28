"""Tests for model evaluation utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from train.evaluate import (
    EvaluationSummary,
    SplitMetrics,
    evaluate_binary_classifier,
    evaluate_model,
    feature_importance_table,
)


def _model_that_returns(n_rows: int) -> MagicMock:
    """A model stub that returns consistent predictions for any input size."""
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.2, 0.8]] * n_rows, dtype=np.float64)
    return model


def test_evaluate_binary_classifier_basic():
    model = _model_that_returns(4)
    X = np.ones((4, 2), dtype=np.float32)
    y = np.array([1, 0, 1, 1], dtype=np.int64)
    metrics = evaluate_binary_classifier(model, X, y)
    assert isinstance(metrics, SplitMetrics)
    assert metrics.row_count == 4
    assert 0 <= metrics.roc_auc <= 1


def test_evaluate_binary_classifier_empty_split_raises():
    model = _model_that_returns(0)
    with pytest.raises(ValueError, match="empty split"):
        evaluate_binary_classifier(model, np.empty((0, 2)), np.empty(0, dtype=np.int64))


def test_evaluate_binary_classifier_shape_mismatch_raises():
    model = _model_that_returns(4)
    X = np.ones((4, 2))
    y = np.array([1, 0, 1], dtype=np.int64)
    with pytest.raises(ValueError, match="same number of rows"):
        evaluate_binary_classifier(model, X, y)


def test_evaluate_binary_classifier_single_class():
    model = MagicMock()
    model.predict_proba.return_value = np.array(
        [[0.9, 0.1], [0.9, 0.1], [0.9, 0.1]], dtype=np.float64
    )
    X = np.ones((3, 2))
    y = np.array([1, 1, 1], dtype=np.int64)
    metrics = evaluate_binary_classifier(model, X, y)
    assert metrics.roc_auc == 0.5
    assert metrics.average_precision == pytest.approx(1.0)


def test_evaluate_model():
    model = MagicMock()
    model.predict_proba.side_effect = lambda X: np.array(
        [[0.2, 0.8]] * X.shape[0], dtype=np.float64
    )

    X_val = np.ones((4, 2))
    y_val = np.array([1, 0, 1, 0], dtype=np.int64)
    X_test = np.ones((3, 2))
    y_test = np.array([0, 1, 1], dtype=np.int64)

    summary = evaluate_model(
        model,
        X_validation=X_val,
        y_validation=y_val,
        X_test=X_test,
        y_test=y_test,
    )
    assert isinstance(summary, EvaluationSummary)
    assert summary.validation.row_count == 4
    assert summary.test.row_count == 3


def test_feature_importance_table():
    class MockBooster:
        def feature_importance(self, importance_type="gain"):
            return np.array([0.1, 0.5, 0.2], dtype=np.float64)

    model = MagicMock()
    model.booster_ = MockBooster()
    feature_names = ("feat_a", "feat_b", "feat_c")
    result = feature_importance_table(model, feature_names)
    assert len(result) == 3
    assert result[0]["feature"] == "feat_b"
    assert result[0]["gain"] == 0.5


def test_feature_importance_table_missing_booster_raises():
    model = object()
    with pytest.raises(ValueError, match="booster_"):
        feature_importance_table(model, ["a"])


def test_feature_importance_table_count_mismatch_raises():
    class MockBooster:
        def feature_importance(self, importance_type="gain"):
            return np.array([0.1, 0.2])

    model = MagicMock()
    model.booster_ = MockBooster()
    with pytest.raises(ValueError, match="does not match"):
        feature_importance_table(model, ["a", "b", "c"])
