# ============================================================
# tests/conftest.py
# ============================================================
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# -- ensure the serving package is importable -------------------------------
_SERVE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVE_DIR not in sys.path:
    sys.path.insert(0, _SERVE_DIR)


# -- prevent real Azure / telemetry -----------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _block_azure_credential() -> Iterator[None]:
    with patch("azure.identity.DefaultAzureCredential", return_value=MagicMock()):
        yield


@pytest.fixture(autouse=True, scope="session")
def _set_required_env() -> Iterator[None]:
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


# -- reusable mocks for request‑time dependencies --------------------------
@pytest.fixture
def mock_model_loader() -> MagicMock:
    loader = MagicMock()
    loader.is_ready = True
    loader.model_version = "1"
    return loader


@pytest.fixture
def mock_predictor() -> MagicMock:
    return MagicMock()


# -- the actual app fixture -------------------------------------------------
@pytest.fixture
def test_app(
    mock_model_loader: MagicMock,
    mock_predictor: MagicMock,
) -> Iterator[TestClient]:
    """
    FastAPI TestClient with telemetry and model loading fully mocked.
    The lifespan runs instantly; all endpoints work.
    """
    # 1. Mock telemetry so that app.py's module‑level init_telemetry is a no‑op
    with (
        patch("app.init_telemetry", return_value=None),
        patch("app.shutdown_telemetry", return_value=None),
    ):
        # 2. Mock the model‑loading calls used in the lifespan
        with (
            patch(
                "model.registry.ModelRegistry.resolve",
                return_value=MagicMock(
                    model_name="test",
                    model_version="1",
                    run_id="run123",
                    artifact_uri="",
                ),
            ),
            patch(
                "model.loader.ModelLoader.load",
                return_value=MagicMock(),  # fake ONNX session
            ),
        ):
            from api.dependencies import get_model_loader, get_predictor
            from app import app

    # 3. Override FastAPI dependencies for request handlers
    app.dependency_overrides[get_model_loader] = lambda: mock_model_loader
    app.dependency_overrides[get_predictor] = lambda: mock_predictor

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
