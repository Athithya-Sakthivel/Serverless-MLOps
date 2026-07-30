# ============================================================
# tests/conftest.py
#
# Shared fixtures for the serving test suite.
#
# Design principles:
# - No real network calls ever. Every external dependency is mocked.
# - The FastAPI lifespan is skipped via SERVING_SKIP_MODEL_LOAD so that
#   TestClient startup is instantaneous and never hangs.
# - Request-handler dependencies (model loader, predictor) are overridden
#   with MagicMock instances so individual tests can control their behavior.
# - Environment variables are set once at the session scope and restored
#   after all tests finish.
# ============================================================
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# -- Ensure the serving package is importable -------------------------------
# When pytest runs from the serving directory, Python adds the working
# directory to sys.path automatically. When invoked from elsewhere
# (e.g., repo root), this insertion guarantees absolute imports resolve.
_SERVE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVE_DIR not in sys.path:
    sys.path.insert(0, _SERVE_DIR)


# -- Session-scoped environment setup ---------------------------------------
# autouse=True and scope="session" mean this fixture runs once before any
# test is collected and stays active for the entire test session.
# It sets the minimum environment variables required to construct
# ServingConfig and prevents the lifespan from making network calls.
@pytest.fixture(autouse=True, scope="session")
def _set_required_env() -> Iterator[None]:
    """Ensure minimum environment variables for app config."""
    vars_to_set = {
        # MLFLOW_TRACKING_URI is required by ServingConfig.from_env().
        # A dummy value prevents ValueError during config construction.
        "MLFLOW_TRACKING_URI": "http://dummy-tracking",
        # Empty connection string makes init_telemetry() return early
        # before importing Azure Monitor (see utils/telemetry.py).
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
        # SERVING_SKIP_MODEL_LOAD tells the FastAPI lifespan to skip
        # model download and loading. This is the key to preventing
        # the TestClient from hanging on a network call.
        "SERVING_SKIP_MODEL_LOAD": "1",
    }
    # Save original values so we can restore them after the session ends.
    original = {}
    for key, value in vars_to_set.items():
        original[key] = os.environ.get(key)
        os.environ[key] = value
    yield
    # Restore the environment to its pre-test state.
    for key, orig_value in original.items():
        if orig_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig_value


# -- Reusable mock fixtures -------------------------------------------------
# These fixtures are function-scoped (the default), so each test gets a
# fresh MagicMock. Tests that need to assert on calls or set return values
# can use these directly.


@pytest.fixture
def mock_model_loader() -> MagicMock:
    """A MagicMock that looks like ModelLoader (is_ready=True by default)."""
    loader = MagicMock()
    loader.is_ready = True
    loader.model_version = "1"
    return loader


@pytest.fixture
def mock_predictor() -> MagicMock:
    """A MagicMock that looks like Predictor."""
    return MagicMock()


# -- The test FastAPI application -------------------------------------------
# This fixture creates a TestClient with the real FastAPI app, but with
# request-handler dependencies overridden so that tests control the behavior
# of model loading and prediction without any real MLflow or ONNX Runtime.


@pytest.fixture
def test_app(mock_model_loader: MagicMock, mock_predictor: MagicMock) -> Iterator[TestClient]:
    """
    FastAPI TestClient with model-handler dependencies overridden.

    The lifespan is skipped (SERVING_SKIP_MODEL_LOAD=1), so TestClient
    startup is instantaneous. Request handlers that depend on ModelLoader
    or Predictor receive the mock objects defined above.
    """
    # Import inside the fixture so that the session-scoped environment
    # variables are already set when the app module is first loaded.
    from api.dependencies import get_model_loader, get_predictor
    from app import app

    # Override FastAPI's dependency injection for request handlers.
    # Any route that declares `loader: ModelLoader = Depends(get_model_loader)`
    # will receive mock_model_loader instead of the real one.
    app.dependency_overrides[get_model_loader] = lambda: mock_model_loader
    app.dependency_overrides[get_predictor] = lambda: mock_predictor

    with TestClient(app) as client:
        yield client

    # Clean up overrides so they don't leak between tests that may need
    # different mock configurations.
    app.dependency_overrides.clear()
