"""End‑to‑end ELT pipeline tests using the 10K‑row CI sample and mocked Azure."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elt.extract import resolve_input_blob_name
from elt.load import checkpoint_payload, clean_blob_name
from elt.transform import clean_raw_frame
from elt.validate import ValidationError, validate_raw_frame

# ---------------------------------------------------------------------------
# Path to the CI sample
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # back to repo root
CI_SAMPLE_PATH = _REPO_ROOT / "src" / "ci-samples" / "data.parquet"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_sample_frame() -> pl.DataFrame:
    """The 10K‑row CI sample – read once per test module."""
    if not CI_SAMPLE_PATH.exists():
        pytest.fail(f"CI sample not found at {CI_SAMPLE_PATH}")
    return pl.read_parquet(CI_SAMPLE_PATH)


# ---------------------------------------------------------------------------
# resolve_input_blob_name
# ---------------------------------------------------------------------------


def test_resolve_explicit():
    assert resolve_input_blob_name("raw/data.parquet") == "raw/data.parquet"


def test_resolve_leading_slash():
    assert resolve_input_blob_name("  /raw/data.parquet ") == "raw/data.parquet"


def test_resolve_from_env(monkeypatch):
    monkeypatch.setenv("INPUT_BLOB_NAME", "from_env.parquet")
    assert resolve_input_blob_name() == "from_env.parquet"


def test_resolve_no_value_raises():
    for var in ("INPUT_BLOB_NAME", "RAW_BLOB_NAME", "EVENT_GRID_BLOB_NAME"):
        os.environ.pop(var, None)
    with pytest.raises(ValueError, match="Input blob name is required"):
        resolve_input_blob_name()


# ---------------------------------------------------------------------------
# validate_raw_frame with CI sample
# ---------------------------------------------------------------------------


def test_validate_passes_ci_sample(ci_sample_frame):
    """CI sample must pass validation without errors."""
    report = validate_raw_frame(ci_sample_frame)
    assert report.row_count == ci_sample_frame.height
    assert report.row_count > 0
    # The sample should be clean enough that these rates are zero or very low
    assert report.duplicate_rate < 0.05  # generous upper bound


def test_validate_empty_frame():
    empty = pl.DataFrame({"AGEP": []}, schema={"AGEP": pl.Int64})
    with pytest.raises(ValidationError, match="Raw frame is empty"):
        validate_raw_frame(empty)


def test_validate_missing_column_raises(ci_sample_frame):
    df = ci_sample_frame.drop("PINCP")
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_raw_frame(df)


def test_validate_null_rate_exceeded(ci_sample_frame):
    """Artificially null out PINCP in all rows to trigger null‑rate failure."""
    df = ci_sample_frame.with_columns(pl.lit(None).cast(pl.Float64).alias("PINCP"))
    with pytest.raises(ValidationError, match="Null rate for PINCP"):
        validate_raw_frame(df)


def test_validate_report_has_expected_fields(ci_sample_frame):
    report = validate_raw_frame(ci_sample_frame)
    assert isinstance(report.row_count, int)
    assert isinstance(report.column_names, tuple)
    assert isinstance(report.duplicate_rows, int)
    assert isinstance(report.invalid_state_rows, int)
    assert isinstance(report.warnings, tuple)
    assert isinstance(report.null_counts, dict)
    # STATE should have zero nulls (max_null_rate=0.0)
    assert report.null_counts.get("STATE", 0) == 0


# ---------------------------------------------------------------------------
# clean_raw_frame with CI sample
# ---------------------------------------------------------------------------


def test_clean_preserves_reasonable_row_count(ci_sample_frame):
    """Cleaning should not drop more than a small fraction of the sample."""
    clean, metrics = clean_raw_frame(ci_sample_frame)
    retention = metrics.output_rows / metrics.input_rows
    assert retention > 0.90, f"Retention rate {retention:.2%} too low"
    assert clean.height == metrics.output_rows


def test_clean_output_schema_is_consistent(ci_sample_frame):
    """All required columns present with correct types after cleaning."""
    clean, _ = clean_raw_frame(ci_sample_frame)
    assert "AGEP" in clean.columns
    assert clean.schema["AGEP"] == pl.Float64
    assert clean.schema["STATE"] == pl.Utf8
    assert clean.schema["PINCP"] == pl.Float64
    assert clean.schema["YEAR"] == pl.Int64


def test_clean_with_validation_report(ci_sample_frame):
    """ValidationReport warnings propagate into TransformMetrics."""
    report = validate_raw_frame(ci_sample_frame)
    _, metrics = clean_raw_frame(ci_sample_frame, validation_report=report)
    assert metrics.warnings == report.warnings


def test_clean_removes_null_rows():
    """Manual frame with a null in PINCP – that row must be dropped."""
    df = pl.DataFrame(
        {
            "AGEP": [30, 40, 50],
            "COW": [1, 2, 1],
            "SCHL": [16, 21, 22],
            "MAR": [1, 3, 1],
            "OCCP": [1024, 2048, 3072],
            "POBP": [6, 12, 24],
            "RELP": [0, 1, 2],
            "WKHP": [40, 35, 45],
            "SEX": [1, 2, 1],
            "RAC1P": [1, 2, 1],
            "STATE": ["NY", "CA", "TX"],
            "YEAR": [2024, 2023, 2025],
            "PINCP": [60000, None, 120000],  # middle row has null
        }
    )
    clean, metrics = clean_raw_frame(df)
    assert clean.height == 2
    assert metrics.null_rows_removed == 1


def test_clean_deduplicates():
    df = pl.DataFrame(
        {
            "AGEP": [30, 30],
            "COW": [1, 1],
            "SCHL": [16, 16],
            "MAR": [1, 1],
            "OCCP": [1024, 1024],
            "POBP": [6, 6],
            "RELP": [0, 0],
            "WKHP": [40, 40],
            "SEX": [1, 1],
            "RAC1P": [1, 1],
            "STATE": ["NY", "NY"],
            "YEAR": [2024, 2024],
            "PINCP": [60000, 60000],
        }
    )
    clean, metrics = clean_raw_frame(df)
    assert clean.height == 1
    assert metrics.duplicates_removed == 1


# ---------------------------------------------------------------------------
# clean_blob_name
# ---------------------------------------------------------------------------


def test_clean_blob_name_simple():
    assert clean_blob_name("raw/monthly/batch.parquet") == "clean/monthly/batch.parquet"


def test_clean_blob_name_no_folder():
    assert clean_blob_name("batch.parquet") == "clean/batch.parquet"


def test_clean_blob_name_leading_slash():
    assert clean_blob_name("  /raw/data.parquet ") == "clean/data.parquet"


# ---------------------------------------------------------------------------
# checkpoint_payload
# ---------------------------------------------------------------------------


def test_checkpoint_payload_fields():
    started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    finished = datetime(2025, 1, 1, 12, 0, 10, tzinfo=UTC)
    payload = checkpoint_payload(
        raw_blob_name="raw/test.parquet",
        clean_blob_name="clean/test.parquet",
        validation_report={"ok": True},
        transform_metrics={"rows": 100},
        started_at=started,
        finished_at=finished,
        status="COMPLETED",
    )
    assert payload["status"] == "COMPLETED"
    assert payload["raw_blob_name"] == "raw/test.parquet"
    assert payload["clean_blob_name"] == "clean/test.parquet"
    assert payload["duration_seconds"] == 10.0


# ---------------------------------------------------------------------------
# Full ELT orchestration with mocked Azure
# ---------------------------------------------------------------------------


def test_full_elt_pipeline_with_mocks(ci_sample_frame, monkeypatch, tmp_path):
    """Run the ELT orchestration end‑to‑end using mocked Azure calls."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setenv("INPUT_BLOB_NAME", "raw/monthly/batch.parquet")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "azureml://test")

    # Write sample to a temp parquet so the mock download "returns" it
    test_file = tmp_path / "raw.parquet"
    ci_sample_frame.write_parquet(test_file)

    with (
        patch("elt.load.build_blob_service_client"),
        patch("elt.extract.build_blob_service_client"),
        patch("elt.load.upload_file_to_blob"),
        patch("elt.load.upload_bytes_to_blob"),
        patch("elt.extract.download_blob_to_tempfile") as mock_download,
        patch("elt.load.read_checkpoint", return_value=None) as mock_read_cp,
        patch("elt.load.write_checkpoint") as mock_write_cp,
    ):
        mock_download.return_value = test_file

        from main import _run_elt
        from utils.config import AppConfig

        config = AppConfig.from_env()
        clean_name = _run_elt(config, "raw/monthly/batch.parquet")

        assert clean_name == "clean/monthly/batch.parquet"
        mock_read_cp.assert_called_once()
        mock_write_cp.assert_called_once()

        # Inspect the checkpoint payload written
        payload = mock_write_cp.call_args[0][3]  # positional arg: payload
        assert payload["status"] == "COMPLETED"
        assert "validation_report" in payload
        assert "transform_metrics" in payload


def test_elt_skip_when_checkpoint_completed(monkeypatch):
    """ELT must skip entirely when a COMPLETED checkpoint exists."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setenv("INPUT_BLOB_NAME", "raw/skip.parquet")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "azureml://test")

    with patch("elt.load.read_checkpoint") as mock_read:
        mock_read.return_value = {
            "status": "COMPLETED",
            "clean_blob_name": "clean/skip.parquet",
        }
        with patch("elt.extract.read_parquet_from_blob") as mock_extract:
            from main import _run_elt
            from utils.config import AppConfig

            config = AppConfig.from_env()
            clean_name = _run_elt(config, "raw/skip.parquet")
            assert clean_name == "clean/skip.parquet"
            mock_extract.assert_not_called()
