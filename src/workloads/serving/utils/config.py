"""
Environment-backed configuration with fail-fast semantics.
Adapted from the training pipeline (identical pattern).
All values are read once and the config object is immutable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required_env(name: str) -> str:
    value = _env_str(name)
    if value is None:
        raise ValueError(f"Environment variable {name} is required")
    return value


def _env_int(name: str, default: int) -> int:
    value = _env_str(name)
    return int(value) if value is not None else default


@dataclass(frozen=True, slots=True)
class ServingConfig:
    """Immutable configuration for the serving container."""

    port: int
    model_name: str
    model_version: str | None  # None -> use production alias
    model_alias: str  # alias to resolve when version is None
    mlflow_tracking_uri: str
    mlflow_registry_uri: str | None  # optional, for remote registries
    app_insights_connection_string: str | None
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServingConfig:
        port = _env_int("PORT", 80)
        model_name = _env_str("MODEL_NAME", "acs_income_classifier") or "acs_income_classifier"
        model_version = _env_str("MODEL_VERSION", None)
        model_alias = _env_str("MODEL_ALIAS", "production") or "production"
        mlflow_tracking_uri = _required_env("MLFLOW_TRACKING_URI")
        mlflow_registry_uri = _env_str("MLFLOW_REGISTRY_URI", None)
        app_insights_connection_string = _env_str("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        log_level = _env_str("LOG_LEVEL", "INFO") or "INFO"

        return cls(
            port=port,
            model_name=model_name,
            model_version=model_version,
            model_alias=model_alias,
            mlflow_tracking_uri=mlflow_tracking_uri,
            mlflow_registry_uri=mlflow_registry_uri,
            app_insights_connection_string=app_insights_connection_string,
            log_level=log_level,
        )
