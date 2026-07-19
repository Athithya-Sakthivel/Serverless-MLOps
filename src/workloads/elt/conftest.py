"""Shared pytest fixtures for ELT tests."""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.transform import AGE_GROUP_LABELS  # noqa: E402


@pytest.fixture(autouse=True)
def reset_cached_storage_clients() -> Generator[None, Any]:
    """Keep cached storage clients isolated between tests."""
    from src.load import get_blob_service_client, get_queue_service_client

    get_blob_service_client.cache_clear()
    get_queue_service_client.cache_clear()
    yield
    get_blob_service_client.cache_clear()
    get_queue_service_client.cache_clear()


@pytest.fixture
def sample_raw_df() -> pd.DataFrame:
    """A small DataFrame that resembles raw Adult Census data."""
    return pd.DataFrame(
        {
            "age": [24, 35, 50, 65],
            "workclass": [" Private ", " Self-emp ", " Private ", " Private "],
            "fnlwgt": [100000, 200000, 300000, 400000],
            "education": [" Bachelors ", " Masters ", " HS-grad ", " HS-grad "],
            "education.num": [13, 14, 9, 9],
            "marital.status": [" Never-married ", " Married ", " Divorced ", " Widowed "],
            "occupation": [" Tech-support ", " Exec-managerial ", " Craft-repair ", " Sales "],
            "relationship": [" Not-in-family ", " Husband ", " Unmarried ", " Not-in-family "],
            "race": [" White ", " White ", " Black ", " White "],
            "sex": [" Male ", " Female ", " Male ", " Female "],
            "capital.gain": [0, 5000, 0, 2000],
            "capital.loss": [0, 0, 1000, 0],
            "hours.per.week": [40, 50, 35, 45],
            "native.country": [" United-States ", " United-States ", " Mexico ", " United-States "],
            "income": [" <=50K. ", " >50K ", " <=50K ", None],
        }
    )


@pytest.fixture
def transformed_clean_df() -> pd.DataFrame:
    """A dataframe that has already passed through the transformation pipeline."""
    return pd.DataFrame(
        {
            "age": [24, 35, 50],
            "workclass": ["Private", "Self-emp", "Private"],
            "fnlwgt": [100000, 200000, 300000],
            "education": ["Bachelors", "Masters", "HS-grad"],
            "education_num": [13, 14, 9],
            "marital_status": ["Never-married", "Married", "Divorced"],
            "occupation": ["Tech-support", "Exec-managerial", "Craft-repair"],
            "relationship": ["Not-in-family", "Husband", "Unmarried"],
            "race": ["White", "White", "Black"],
            "sex": ["Male", "Female", "Male"],
            "capital_gain": [0, 5000, 0],
            "capital_loss": [0, 0, 1000],
            "hours_per_week": [40, 50, 35],
            "native_country": ["United-States", "United-States", "Mexico"],
            "income": [0, 1, 0],
            "net_capital": [0, 5000, -1000],
            "age_group": pd.Categorical(
                ["young", "adult", "middle_age"],
                categories=list(AGE_GROUP_LABELS),
                ordered=True,
            ),
        }
    )


@pytest.fixture
def parquet_bytes(sample_raw_df: pd.DataFrame) -> bytes:
    """Raw bytes of a parquet file containing the sample_raw_df."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(sample_raw_df, preserve_index=False)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


@pytest.fixture
def mock_blob_input(parquet_bytes: bytes) -> Any:
    """A simple mock for an Azure Function InputStream."""

    class MockBlob:
        def __init__(
            self, data: bytes, name: str = "test/monthly.parquet", length: int | None = None
        ) -> None:
            self.data = data
            self.name = name
            self.length = length or len(data)

        def read(self, size: int = -1) -> bytes:
            return self.data if size == -1 else self.data[:size]

    return MockBlob(data=parquet_bytes, name="raw/monthly/test.parquet", length=len(parquet_bytes))
