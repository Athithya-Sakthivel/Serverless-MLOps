"""Unit tests for extract.py."""

from __future__ import annotations

import pytest
from src.extract import read_parquet_from_blob


class TestReadParquetFromBlob:
    def test_reads_valid_parquet(self, mock_blob_input, sample_raw_df):
        dataframe = read_parquet_from_blob(mock_blob_input.read(), mock_blob_input.name)
        assert len(dataframe) == len(sample_raw_df)
        assert list(dataframe.columns) == list(sample_raw_df.columns)

    def test_raises_on_invalid_bytes(self):
        with pytest.raises(ValueError, match="Invalid Parquet data"):
            read_parquet_from_blob(b"not a parquet file", "bad_file.parquet")

    def test_raises_on_empty_blob(self):
        with pytest.raises(ValueError, match="is empty"):
            read_parquet_from_blob(b"", "empty.parquet")

    def test_logs_row_count(self, caplog, mock_blob_input):
        with caplog.at_level("INFO"):
            read_parquet_from_blob(mock_blob_input.read(), mock_blob_input.name)

        assert "rows and" in caplog.text
