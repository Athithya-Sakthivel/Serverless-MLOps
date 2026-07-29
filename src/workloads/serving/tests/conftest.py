# ============================================================
# tests/conftest.py
# ============================================================
from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True, scope="session")
def _block_azure_credential() -> Iterator[None]:
    """Prevent any real Azure credential from being created."""
    with patch("azure.identity.DefaultAzureCredential", return_value=MagicMock()):
        yield


@pytest.fixture(autouse=True, scope="session")
def _set_required_env() -> Iterator[None]:
    """
    Ensure the minimum environment variables needed to construct the
    application configuration are present, so that importing ``app``
    does not fail.
    """
    vars_to_set = {
        "MLFLOW_TRACKING_URI": "http://dummy-tracking",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
    }
    original = {}
    for key, value in vars_to_set.items():
        original[key] = os.environ.get(key)
        os.environ[key] = value
    yield
    for key, orig_value in original.items():
        if orig_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig_value


@pytest.fixture(autouse=True)
def _block_telemetry() -> Iterator[None]:
    """Disable telemetry initialisation for every test."""
    with (
        patch("utils.telemetry.init_telemetry", return_value=None),
        patch("utils.telemetry.shutdown_telemetry", return_value=None),
    ):
        yield


@pytest.fixture
def mock_model_loader() -> MagicMock:
    loader = MagicMock()
    loader.is_ready = True
    loader.model_version = "1"
    return loader


@pytest.fixture
def mock_predictor() -> MagicMock:
    return MagicMock()


@pytest.fixture
def test_app(mock_model_loader: MagicMock, mock_predictor: MagicMock) -> Iterator[TestClient]:
    """FastAPI TestClient with mocked loader and predictor dependencies."""
    from api.dependencies import get_model_loader, get_predictor
    from app import app

    app.dependency_overrides[get_model_loader] = lambda: mock_model_loader
    app.dependency_overrides[get_predictor] = lambda: mock_predictor

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
