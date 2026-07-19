"""Integration test for the ELT pipeline without real Azure dependencies."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
from src.function_app import transform_new_data
from src.load import DEFAULT_CLEAN_BLOB_NAME, DEFAULT_CLEAN_CONTAINER, DEFAULT_QUEUE_NAME


def test_pipeline_with_mocks(mock_blob_input, monkeypatch):
    """Simulate a full blob trigger event and verify that clean data is uploaded."""

    monkeypatch.setenv("TRIGGER_ML_TRAINING", "true")

    blob_service_client = MagicMock()
    container_client = MagicMock()
    blob_service_client.get_container_client.return_value = container_client

    with (
        patch("src.function_app.get_blob_service_client", return_value=blob_service_client),
        patch("src.function_app.send_training_trigger") as mock_send_training_trigger,
    ):
        transform_new_data(mock_blob_input)

    blob_service_client.get_container_client.assert_called_once_with(DEFAULT_CLEAN_CONTAINER)
    container_client.create_container.assert_called_once()
    container_client.upload_blob.assert_called_once()

    upload_kwargs = container_client.upload_blob.call_args.kwargs
    assert upload_kwargs["name"] == DEFAULT_CLEAN_BLOB_NAME

    uploaded_bytes = upload_kwargs["data"]
    uploaded_df = pd.read_parquet(io.BytesIO(uploaded_bytes), engine="pyarrow")

    assert len(uploaded_df) == 3
    assert set(uploaded_df["income"].unique()) == {0, 1}
    assert "net_capital" in uploaded_df.columns
    assert "age_group" in uploaded_df.columns

    mock_send_training_trigger.assert_called_once()
    trigger_kwargs = mock_send_training_trigger.call_args.kwargs
    assert trigger_kwargs["queue_name"] == DEFAULT_QUEUE_NAME

    payload = json.loads(trigger_kwargs["message"])
    assert payload["event"] == "new_clean_data"
    assert payload["rows"] == 3
    assert payload["output_container"] == DEFAULT_CLEAN_CONTAINER
    assert payload["output_blob"] == DEFAULT_CLEAN_BLOB_NAME
