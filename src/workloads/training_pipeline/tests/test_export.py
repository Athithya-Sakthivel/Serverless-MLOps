"""Tests for ONNX export and verification."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from train.export import OnnxExportResult, export_lightgbm_classifier_to_onnx


@pytest.fixture(scope="module")
def trained_model():
    """A real, tiny LightGBM model suitable for ONNX export tests."""
    X = np.random.default_rng(42).standard_normal((20, 3)).astype(np.float32)
    y = (X[:, 0] > 0).astype(np.int64)
    model = lgb.LGBMClassifier(n_estimators=3, random_state=42)
    model.fit(X, y)
    return model


def test_export_success(trained_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.onnx"
        sample = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        result = export_lightgbm_classifier_to_onnx(
            trained_model,
            feature_count=3,
            output_path=path,
            sample_features=sample,
        )
        assert isinstance(result, OnnxExportResult)
        assert path.exists()
        assert len(result.sha256) == 64
        assert result.sample_count == 2
        assert 0.0 <= result.max_abs_probability_delta < 1.0


def test_export_feature_count_mismatch_raises(trained_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.onnx"
        sample = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        with pytest.raises(ValueError, match="feature_count"):
            export_lightgbm_classifier_to_onnx(
                trained_model,
                feature_count=2,
                output_path=path,
                sample_features=sample,
            )


def test_export_empty_sample_raises(trained_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.onnx"
        with pytest.raises(ValueError, match="empty"):
            export_lightgbm_classifier_to_onnx(
                trained_model,
                feature_count=2,
                output_path=path,
                sample_features=np.empty((0, 2), dtype=np.float32),
            )


def test_export_non_2d_sample_raises(trained_model):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.onnx"
        with pytest.raises(ValueError, match="two-dimensional"):
            export_lightgbm_classifier_to_onnx(
                trained_model,
                feature_count=2,
                output_path=path,
                sample_features=np.array([1.0, 2.0], dtype=np.float32),
            )
