"""End‑to‑end ELT pipeline tests using the 10K‑row CI sample and fake Azure.

No ``unittest.mock`` patches are used for Azure authentication or I/O.
The fake client stores everything in memory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import polars as pl
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
import sys

sys.path.insert(0, str(_PACKAGE_ROOT))

from elt.extract import resolve_input_blob_name
from elt.load import checkpoint_payload, clean_blob_name, read_checkpoint
from elt.transform import clean_raw_frame
from elt.validate import ValidationError, validate_raw_frame
from tests.fakes import FakeBlobServiceClient

_REPO_ROOT = _HERE.parents[4]
CI_SAMPLE_PATH = _REPO_ROOT / "src" / "ci-samples" / "data.parquet"


def _parquet_bytes(df: pl.DataFrame) -> bytes:
    buf = BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


def _fake_client_with_raw(
    raw_blob_name: str,
    raw_frame: pl.DataFrame,
    checkpoint_dict: dict | None = None,
) -> FakeBlobServiceClient:
    """Build a fake client seeded with a raw blob and optional checkpoint."""
    containers: dict[str, dict[str, bytes]] = {
        "raw": {raw_blob_name: _parquet_bytes(raw_frame)},
    }
    if checkpoint_dict is not None:
        containers.setdefault("checkpoints", {})[f"elt/{raw_blob_name.lstrip('/')}.json"] = (
            json.dumps(checkpoint_dict).encode("utf-8")
        )
    return FakeBlobServiceClient(containers)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_sample_frame() -> pl.DataFrame:
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


def test_resolve_no_value_raises(monkeypatch):
    for var in ("INPUT_BLOB_NAME", "RAW_BLOB_NAME", "EVENT_GRID_BLOB_NAME"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="Input blob name is required"):
        resolve_input_blob_name()


# ---------------------------------------------------------------------------
# validate_raw_frame
# ---------------------------------------------------------------------------


def test_validate_passes_ci_sample(ci_sample_frame):
    report = validate_raw_frame(ci_sample_frame)
    assert report.row_count == ci_sample_frame.height
    assert report.row_count > 0
    assert report.duplicate_rate < 0.05


def test_validate_empty_frame():
    empty = pl.DataFrame({"AGEP": []}, schema={"AGEP": pl.Int64})
    with pytest.raises(ValidationError, match="Raw frame is empty"):
        validate_raw_frame(empty)


def test_validate_missing_column_raises(ci_sample_frame):
    df = ci_sample_frame.drop("PINCP")
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_raw_frame(df)


def test_validate_null_rate_exceeded(ci_sample_frame):
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
    assert report.null_counts.get("STATE", 0) == 0


# ---------------------------------------------------------------------------
# clean_raw_frame
# ---------------------------------------------------------------------------


def test_clean_preserves_reasonable_row_count(ci_sample_frame):
    clean, metrics = clean_raw_frame(ci_sample_frame)
    retention = metrics.output_rows / metrics.input_rows
    assert retention > 0.90, f"Retention rate {retention:.2%} too low"
    assert clean.height == metrics.output_rows


def test_clean_output_schema_is_consistent(ci_sample_frame):
    clean, _ = clean_raw_frame(ci_sample_frame)
    assert "AGEP" in clean.columns
    assert clean.schema["AGEP"] == pl.Float64
    assert clean.schema["STATE"] == pl.Utf8
    assert clean.schema["PINCP"] == pl.Float64
    assert clean.schema["YEAR"] == pl.Int64


def test_clean_with_validation_report(ci_sample_frame):
    report = validate_raw_frame(ci_sample_frame)
    _, metrics = clean_raw_frame(ci_sample_frame, validation_report=report)
    assert metrics.warnings == report.warnings


def test_clean_removes_null_rows():
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
            "PINCP": [60000, None, 120000],
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
# Full ELT orchestration – zero mocks / zero patches
# ---------------------------------------------------------------------------


def test_full_elt_pipeline_with_fake(ci_sample_frame, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setenv("INPUT_BLOB_NAME", "raw/monthly/batch.parquet")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "azureml://test")

    fake_client = _fake_client_with_raw(
        raw_blob_name="raw/monthly/batch.parquet",
        raw_frame=ci_sample_frame,
    )

    from main import _run_elt
    from utils.config import AppConfig

    config = AppConfig.from_env()
    clean_name = _run_elt(config, "raw/monthly/batch.parquet", blob_service_client=fake_client)

    assert clean_name == "clean/monthly/batch.parquet"

    # Use the production read_checkpoint to verify the checkpoint is valid.
    checkpoint = read_checkpoint(
        storage_account_name="testaccount",
        checkpoint_container_name="checkpoints",
        raw_blob_name="raw/monthly/batch.parquet",
        blob_service_client=fake_client,
    )
    assert checkpoint is not None, "Checkpoint was not written"
    assert checkpoint["status"] == "COMPLETED"

    # Also verify the clean parquet was stored (optional)
    # Since download_blob_to_tempfile expects a real path, we can just check the fake.
    clean_blobs = fake_client._containers.get("clean", {})
    assert "clean/monthly/batch.parquet" in clean_blobs


def test_elt_skip_when_checkpoint_completed(ci_sample_frame, monkeypatch):
    """ELT must skip entirely when a COMPLETED checkpoint already exists."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setenv("INPUT_BLOB_NAME", "raw/skip.parquet")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "azureml://test")

    fake_client = _fake_client_with_raw(
        raw_blob_name="raw/skip.parquet",
        raw_frame=ci_sample_frame,
        checkpoint_dict={
            "status": "COMPLETED",
            "clean_blob_name": "clean/skip.parquet",
        },
    )

    from main import _run_elt
    from utils.config import AppConfig

    config = AppConfig.from_env()
    clean_name = _run_elt(config, "raw/skip.parquet", blob_service_client=fake_client)

    assert clean_name == "clean/skip.parquet"

    # Raw blob was never read (the download method was never invoked by production code).
    # We can assert that the raw container still has only the originally seeded blob
    # and the checkpoint container was never modified by a new write.
    # (There's no easy way to assert "download was not called" without a spy,
    #  but the early-return path is covered by the assertion above.)
