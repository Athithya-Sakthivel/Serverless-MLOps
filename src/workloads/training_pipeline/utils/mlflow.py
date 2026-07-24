"""MLflow configuration for Azure ML workspace.

MLflow is imported lazily because this module is shared with the ELT phase,
which does not require MLflow at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlflow.tracking import MlflowClient

from .config import MlflowConfig


def configure_mlflow(config: MlflowConfig) -> None:
    """Set MLflow tracking URI and experiment.

    Only call this from the training phase; MLflow must be installed.
    """
    if not config.tracking_uri:
        raise ValueError("MLflow tracking URI is empty")

    import mlflow  # pyright: ignore[reportUnknownVariableType]

    mlflow.set_tracking_uri(config.tracking_uri)  # pyright: ignore[reportUnknownMemberType]
    mlflow.set_experiment(config.experiment_name)  # pyright: ignore[reportUnknownMemberType]


def build_mlflow_client(config: MlflowConfig) -> MlflowClient:
    """Build an MlflowClient pointing to the configured tracking server.

    Only call this from the training phase; MLflow must be installed.
    """
    from mlflow.tracking import MlflowClient  # pyright: ignore[reportUnknownVariableType]

    return MlflowClient(tracking_uri=config.tracking_uri)
