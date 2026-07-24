"""MLflow configuration for Azure ML workspace."""

from __future__ import annotations

import mlflow
from mlflow.tracking import MlflowClient

from .config import MlflowConfig


def configure_mlflow(config: MlflowConfig) -> None:
    if not config.tracking_uri:
        raise ValueError("MLflow tracking URI is empty")
    mlflow.set_tracking_uri(config.tracking_uri)
    mlflow.set_experiment(config.experiment_name)


def build_mlflow_client(config: MlflowConfig) -> MlflowClient:
    return MlflowClient(tracking_uri=config.tracking_uri)
