"""MLflow model registration."""

from __future__ import annotations

from dataclasses import dataclass

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """Result of model registration."""

    registered_model_name: str
    model_version: str
    alias: str | None


def _is_already_exists_exception(exc: MlflowException) -> bool:
    """Return True when MLflow reports that the registered model already exists."""
    message = str(exc).lower()
    error_code = getattr(exc, "error_code", None)
    return "already exists" in message or str(error_code).lower().endswith("already_exists")


def register_model_version(
    *,
    client: MlflowClient,
    model_name: str,
    model_uri: str,
    run_id: str,
    alias: str | None = None,
) -> RegistrationResult:
    """Register a model version and optionally assign a model alias."""
    if not model_name:
        raise ValueError("model_name is required")
    if not model_uri:
        raise ValueError("model_uri is required")
    if not run_id:
        raise ValueError("run_id is required")

    try:
        client.create_registered_model(model_name)
    except MlflowException as exc:
        if not _is_already_exists_exception(exc):
            raise

    model_version = client.create_model_version(
        name=model_name,
        source=model_uri,
        run_id=run_id,
    )

    if alias:
        client.set_registered_model_alias(
            name=model_name,
            alias=alias,
            version=str(model_version.version),
        )

    return RegistrationResult(
        registered_model_name=model_name,
        model_version=str(model_version.version),
        alias=alias,
    )
