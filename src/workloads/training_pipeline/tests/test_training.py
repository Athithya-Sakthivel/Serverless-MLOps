"""Integration test for the full training pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from tests.fakes import FakeBlobServiceClient
from utils.config import AppConfig

_REPO_ROOT = _HERE.parents[4]
CI_SAMPLE_PATH = _REPO_ROOT / "src" / "ci-samples" / "data.parquet"


@pytest.fixture(scope="module")
def ci_sample_frame() -> pl.DataFrame:
    if not CI_SAMPLE_PATH.exists():
        pytest.fail(f"CI sample not found at {CI_SAMPLE_PATH}")
    return pl.read_parquet(CI_SAMPLE_PATH)


def _parquet_bytes(df: pl.DataFrame) -> bytes:
    import io

    buf = io.BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


def _fake_client_with_raw(raw_blob_name: str, raw_frame: pl.DataFrame) -> FakeBlobServiceClient:
    containers = {"raw": {raw_blob_name: _parquet_bytes(raw_frame)}}
    return FakeBlobServiceClient(containers)


def test_run_training_pipeline_integration(ci_sample_frame, monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setenv("INPUT_BLOB_NAME", "raw/monthly/batch.parquet")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "azureml://test")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "train_int_test")
    monkeypatch.setenv("MODEL_NAME", "test_model")
    monkeypatch.setenv("ENABLE_MODEL_REGISTRATION", "false")

    raw_blob_name = "raw/monthly/batch.parquet"
    fake_client = _fake_client_with_raw(raw_blob_name, ci_sample_frame)

    mock_run = MagicMock()
    mock_run.info.run_id = "test_run_id"

    with (
        patch("train.orchestrator.configure_mlflow"),
        patch("mlflow.start_run") as mock_start_run,
        patch("train.orchestrator.mlflow.log_metrics"),
        patch("train.orchestrator.mlflow.log_params"),
        patch("train.orchestrator.mlflow.set_tags"),
        patch("train.orchestrator.mlflow.log_artifact"),
        patch("train.orchestrator.mlflow.log_metric"),
        patch("train.orchestrator.mlflow.log_dict"),
        patch("train.orchestrator.mlflow.set_tag"),
        patch("train.orchestrator.mlflow.register_model"),
        patch("train.orchestrator.export_lightgbm_classifier_to_onnx") as mock_export_onnx,
        # ---------- ADDED: mock the benchmark to avoid real ONNX file access ----------
        patch("train.orchestrator.benchmark_onnx_model") as mock_benchmark,
    ):
        mock_start_run.return_value.__enter__.return_value = mock_run
        mock_export_onnx.return_value.sha256 = "abc123"
        mock_export_onnx.return_value.max_abs_probability_delta = 0.001
        mock_export_onnx.return_value.onnx_path = tmp_path / "model.onnx"

        # Fake benchmark results – passes all performance thresholds
        mock_benchmark.return_value = {
            "p50_latency_ms": 1.0,
            "p95_latency_ms": 1.0,
            "p99_latency_ms": 1.0,
            "throughput_rows_per_sec": 10000,
        }
        # -------------------------------------------------------------------------

        from train.orchestrator import run_training_pipeline

        config = AppConfig.from_env()

        clean_blob_name = "clean/monthly/batch.parquet"
        fake_client._containers.setdefault("clean", {})[clean_blob_name] = _parquet_bytes(
            ci_sample_frame
        )

        result = run_training_pipeline(
            config=config,
            raw_blob_name=raw_blob_name,
            clean_blob_name=clean_blob_name,
            pipeline_run_id="test-pipeline-run",
            blob_service_client=fake_client,
        )

        assert result.mlflow_run_id == "test_run_id"
        assert result.onnx_sha256 == "abc123"
        assert "test" in result.metrics

        checkpoint_key = "training/raw/monthly/batch.json"
        checkpoints = fake_client._containers.get("checkpoints", {})
        assert checkpoint_key in checkpoints, "Training checkpoint not written"
        cp = json.loads(checkpoints[checkpoint_key].decode("utf-8"))
        assert cp["status"] == "COMPLETED"
        assert cp["pipeline_run_id"] == "test-pipeline-run"
