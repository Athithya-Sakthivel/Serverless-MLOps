"""MLflow helpers for the training pipeline.

Uses the low‑level MlflowClient for all operations so that the code
works reliably across MLflow versions (including 3.x on Python 3.14).
"""

from __future__ import annotations

import logging

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from .config import MlflowConfig

LOG = logging.getLogger(__name__)


def configure_mlflow(config: MlflowConfig) -> MlflowClient:
    """Validate the config and return a ready‑to‑use MlflowClient.

    The client is the only MLflow object the rest of the codebase needs.
    Explicit calls to ``mlflow.set_tracking_uri`` / ``mlflow.set_experiment``
    are avoided because their availability varies across MLflow versions.
    """
    if not config.tracking_uri:
        raise ValueError("MLflow tracking URI is empty")

    client = MlflowClient(tracking_uri=config.tracking_uri)

    # Ensure the experiment exists so that the tracking URI is validated
    # early (fail‑fast).  If it doesn't exist, create it.
    try:
        client.get_experiment_by_name(config.experiment_name)
    except MlflowException:
        LOG.info("Creating MLflow experiment: %s", config.experiment_name)
        client.create_experiment(config.experiment_name)

    LOG.info(
        "MLflow configured: uri=%s experiment=%s",
        config.tracking_uri,
        config.experiment_name,
    )
    return client


def promote_model(
    *,
    client: MlflowClient,
    model_name: str,
    model_version: str,
    alias: str = "production",
) -> None:
    """Point the production alias to a specific model version.

    This is called by the canary deployment script after a new model
    version has been safely rolled out.
    """
    client.set_registered_model_alias(
        name=model_name,
        alias=alias,
        version=model_version,
    )
    LOG.info(
        "Model %s version %s promoted to alias '%s'",
        model_name,
        model_version,
        alias,
    )
