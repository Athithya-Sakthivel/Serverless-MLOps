# ============================================================
# tests/test_registry.py
# ============================================================
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mlflow.exceptions import MlflowException
from model.registry import ModelRegistry
from utils.config import ServingConfig


@pytest.fixture
def config_with_version() -> ServingConfig:
    return ServingConfig(
        port=80,
        log_level="INFO",
        model_name="test_model",
        model_version="5",
        model_alias="production",
        mlflow_tracking_uri="http://localhost:5000",
        mlflow_registry_uri=None,
        app_insights_connection_string=None,
    )


@pytest.fixture
def config_with_alias() -> ServingConfig:
    return ServingConfig(
        port=80,
        log_level="INFO",
        model_name="test_model",
        model_version=None,
        model_alias="production",
        mlflow_tracking_uri="http://localhost:5000",
        mlflow_registry_uri=None,
        app_insights_connection_string=None,
    )


def create_mock_model_version(version: str, run_id: str) -> MagicMock:
    mv = MagicMock()
    mv.version = version
    mv.run_id = run_id
    return mv


def test_resolve_explicit_version(config_with_version: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version.return_value = create_mock_model_version(
            version="5", run_id="run5"
        )
        registry = ModelRegistry(config_with_version)
        resolution = registry.resolve()

        assert resolution.model_name == "test_model"
        assert resolution.model_version == "5"
        assert resolution.run_id == "run5"
        assert resolution.artifact_uri == "models:/test_model/5/onnx/model.onnx"
        mock_client.return_value.get_model_version.assert_called_once_with("test_model", "5")


def test_resolve_alias(config_with_alias: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version_by_alias.return_value = (
            create_mock_model_version(version="3", run_id="run3")
        )
        registry = ModelRegistry(config_with_alias)
        resolution = registry.resolve()

        assert resolution.model_version == "3"
        mock_client.return_value.get_model_version_by_alias.assert_called_once_with(
            "test_model", "production"
        )


def test_resolve_caches_result(config_with_version: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version.return_value = create_mock_model_version(
            version="5", run_id="run5"
        )
        registry = ModelRegistry(config_with_version)
        registry.resolve()
        registry.resolve()  # second call
        # Client should have been called only once
        mock_client.return_value.get_model_version.assert_called_once()


def test_resolve_version_not_found(config_with_version: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version.side_effect = MlflowException("not found")
        registry = ModelRegistry(config_with_version)
        with pytest.raises(RuntimeError, match="Failed to fetch model version"):
            registry.resolve()


def test_resolve_alias_not_found(config_with_alias: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version_by_alias.side_effect = MlflowException(
            "alias not assigned"
        )
        registry = ModelRegistry(config_with_alias)
        with pytest.raises(RuntimeError, match="No model version found for alias"):
            registry.resolve()


def test_resolve_missing_run_id(config_with_version: ServingConfig) -> None:
    with patch("model.registry.MlflowClient") as mock_client:
        mock_client.return_value.get_model_version.return_value = create_mock_model_version(
            version="5", run_id=""
        )
        registry = ModelRegistry(config_with_version)
        with pytest.raises(RuntimeError, match="has no run ID"):
            registry.resolve()
