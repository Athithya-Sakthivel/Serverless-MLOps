"""
Resolve the active MLflow model version and the ONNX artifact URI.

Current MLflow registry docs support model version aliases, and the artifact
API accepts model URIs directly, so we resolve once and hand the loader a
stable artifact URI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mlflow import MlflowClient
from mlflow.entities.model_registry import ModelVersion
from mlflow.exceptions import MlflowException
from utils.config import ServingConfig

LOG = logging.getLogger(__name__)

_ONNX_ARTIFACT_PATH = "onnx/model.onnx"


@dataclass(frozen=True, slots=True)
class ModelResolution:
    """Resolved model metadata used by the serving stack."""

    model_name: str
    model_version: str
    run_id: str
    artifact_uri: str
    artifact_path: str = _ONNX_ARTIFACT_PATH


class ModelRegistry:
    """Encapsulates MLflow tracking and model resolution logic."""

    def __init__(self, config: ServingConfig) -> None:
        self._config = config
        self._tracking_uri = config.mlflow_tracking_uri
        self._registry_uri = config.mlflow_registry_uri
        self._default_alias = config.model_alias

        self._client = MlflowClient(
            tracking_uri=self._tracking_uri,
            registry_uri=self._registry_uri,
        )
        self._resolution: ModelResolution | None = None

    @property
    def tracking_uri(self) -> str:
        return self._tracking_uri

    @property
    def registry_uri(self) -> str | None:
        return self._registry_uri

    def resolve(self) -> ModelResolution:
        if self._resolution is not None:
            return self._resolution

        model_name = self._config.model_name

        model_version_entity: ModelVersion
        if self._config.model_version:
            version = str(self._config.model_version)
            LOG.info("Resolving model %s version %s", model_name, version)
            try:
                model_version_entity = self._client.get_model_version(model_name, version)
            except MlflowException as exc:
                raise RuntimeError(f"Failed to fetch model version {model_name}/{version}") from exc
        else:
            alias = self._default_alias
            LOG.info("Resolving model %s alias %s", model_name, alias)
            try:
                model_version_entity = self._client.get_model_version_by_alias(model_name, alias)
            except MlflowException as exc:
                raise RuntimeError(
                    f"No model version found for alias '{alias}' on model '{model_name}'. "
                    "Ensure a model has been registered and the alias assigned."
                ) from exc

        version = str(model_version_entity.version)
        run_id = model_version_entity.run_id
        if not run_id:
            raise RuntimeError(f"Model version {model_name}/{version} has no run ID")

        artifact_uri = f"models:/{model_name}/{version}/{_ONNX_ARTIFACT_PATH}"

        self._resolution = ModelResolution(
            model_name=model_name,
            model_version=version,
            run_id=run_id,
            artifact_uri=artifact_uri,
        )
        LOG.info(
            "Resolved model_name=%s version=%s run_id=%s artifact_uri=%s",
            model_name,
            version,
            run_id,
            artifact_uri,
        )
        return self._resolution

    @property
    def run_id(self) -> str | None:
        return self._resolution.run_id if self._resolution is not None else None
