"""Tests for dataset loading, target creation, and splitting."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

_HERE = Path(__file__).resolve()
_PACKAGE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

from train.dataset import (
    TARGET_COLUMN,
    create_target_column,
    load_clean_frame,
    split_dataset,
)

_REPO_ROOT = _HERE.parents[4]
CI_SAMPLE_PATH = _REPO_ROOT / "src" / "ci-samples" / "data.parquet"


def _sample_frame(n: int = 5) -> pl.DataFrame:
    """A minimal valid clean DataFrame with a TARGET column."""
    df = pl.DataFrame(
        {
            "AGEP": [30, 45, 22, 38, 55][:n],
            "COW": [1, 2, 1, 3, 2][:n],
            "SCHL": [16, 21, 22, 20, 19][:n],
            "MAR": [1, 3, 1, 2, 1][:n],
            "OCCP": [1024, 2048, 3072, 4096, 5120][:n],
            "POBP": [6, 12, 24, 36, 48][:n],
            "RELP": [0, 1, 2, 0, 1][:n],
            "WKHP": [40, 35, 45, 38, 50][:n],
            "SEX": [1, 2, 1, 2, 1][:n],
            "RAC1P": [1, 2, 1, 3, 1][:n],
            "STATE": ["NY", "CA", "TX", "FL", "NY"][:n],
            "YEAR": [2023, 2024, 2022, 2025, 2021][:n],
            "PINCP": [60000, 45000, 120000, 32000, 75000][:n],
        }
    )
    return create_target_column(df, income_threshold=50000)


@pytest.fixture(scope="module")
def ci_sample_frame() -> pl.DataFrame:
    if not CI_SAMPLE_PATH.exists():
        pytest.fail(f"CI sample not found at {CI_SAMPLE_PATH}")
    return pl.read_parquet(CI_SAMPLE_PATH)


def test_load_clean_frame_success(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    expected = _sample_frame(5)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        expected.write_parquet(tmp.name)
        with patch("train.dataset.download_blob_to_tempfile", return_value=Path(tmp.name)):
            result = load_clean_frame(
                storage_account_name="testaccount",
                container_name="clean",
                blob_name="test.parquet",
            )
            assert result.equals(expected)


def test_load_clean_frame_empty_blob_raises(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    empty = _sample_frame(0)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        empty.write_parquet(tmp.name)
        with patch("train.dataset.download_blob_to_tempfile", return_value=Path(tmp.name)):
            with pytest.raises(ValueError, match="is empty"):
                load_clean_frame(
                    storage_account_name="testaccount",
                    container_name="clean",
                    blob_name="test.parquet",
                )


def test_create_target_column():
    df = pl.DataFrame({"AGEP": [30], "PINCP": [60000]})
    result = create_target_column(df, income_threshold=50000)
    assert TARGET_COLUMN in result.columns
    assert result.get_column(TARGET_COLUMN).item() == 1


def test_create_target_column_missing_pincp():
    df = pl.DataFrame({"AGEP": [30]})
    with pytest.raises(ValueError, match="PINCP"):
        create_target_column(df)


def test_split_dataset_fractions_sum_not_one():
    df = _sample_frame(100)
    with pytest.raises(ValueError, match="must sum to 1.0"):
        split_dataset(
            df,
            seed=42,
            train_fraction=0.5,
            validation_fraction=0.5,
            test_fraction=0.5,
        )


def test_split_dataset_target_column_missing():
    df = _sample_frame(10).drop(TARGET_COLUMN)
    with pytest.raises(ValueError, match="TARGET column is required"):
        split_dataset(
            df,
            seed=42,
            train_fraction=0.7,
            validation_fraction=0.15,
            test_fraction=0.15,
            target_column=TARGET_COLUMN,
        )


def test_split_dataset_empty_frame():
    df = _sample_frame(0)
    with pytest.raises(ValueError, match="empty frame"):
        split_dataset(
            df,
            seed=42,
            train_fraction=0.7,
            validation_fraction=0.15,
            test_fraction=0.15,
        )


def test_split_dataset_stratified_sizes(ci_sample_frame):
    n = ci_sample_frame.height
    df = create_target_column(ci_sample_frame)
    splits = split_dataset(
        df,
        seed=42,
        train_fraction=0.70,
        validation_fraction=0.15,
        test_fraction=0.15,
    )
    total = splits.train_frame.height + splits.validation_frame.height + splits.test_frame.height
    assert total == n
    assert abs(splits.train_frame.height - int(n * 0.70)) <= 2
    assert abs(splits.validation_frame.height - int(n * 0.15)) <= 2
    assert abs(splits.test_frame.height - int(n * 0.15)) <= 2


def test_split_dataset_no_overlap(ci_sample_frame):
    df = create_target_column(ci_sample_frame.with_row_index("__orig_idx__"))
    splits = split_dataset(
        df,
        seed=42,
        train_fraction=0.70,
        validation_fraction=0.15,
        test_fraction=0.15,
    )
    train_idx = set(splits.train_frame.get_column("__orig_idx__").to_list())
    val_idx = set(splits.validation_frame.get_column("__orig_idx__").to_list())
    test_idx = set(splits.test_frame.get_column("__orig_idx__").to_list())
    assert len(train_idx & val_idx) == 0
    assert len(train_idx & test_idx) == 0
    assert len(val_idx & test_idx) == 0
    assert len(train_idx | val_idx | test_idx) == df.height
