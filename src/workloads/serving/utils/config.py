"""
Environment-backed configuration with fail-fast semantics.

Every module in the serving stack reads its settings from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


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
    raw_value = _env_str(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = _env_str(name)
    if raw_value is None:
        return default
    return raw_value.lower() in {"1", "true", "yes", "y", "on"}


def _normalize_log_level(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _VALID_LOG_LEVELS:
        valid = ", ".join(sorted(_VALID_LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {valid}")
    return normalized


@dataclass(frozen=True, slots=True)
class ServingConfig:
    """Immutable configuration for the serving container."""

    # Server
    port: int = 80
    log_level: str = "INFO"

    # Model
    model_name: str = "acs_income_classifier"
    model_version: str | None = None
    model_alias: str = "production"

    # MLflow
    mlflow_tracking_uri: str = ""
    mlflow_registry_uri: str | None = None

    # Observability
    app_insights_connection_string: str | None = None

    # Telemetry metadata
    service_name: str = "serving-api"
    service_version: str = "1.0.0"
    environment: str = "production"

    # Telemetry toggles used by utils.telemetry
    telemetry_disable_offline_storage: bool = False
    telemetry_enable_live_metrics: bool = True
    telemetry_enable_performance_counters: bool = True
    telemetry_enable_trace_based_sampling_for_logs: bool = False

    @classmethod
    def from_env(cls) -> ServingConfig:
        return cls(
            port=_env_int("PORT", 80),
            log_level=_normalize_log_level(_env_str("LOG_LEVEL", "INFO") or "INFO"),
            model_name=_env_str("MODEL_NAME", "acs_income_classifier") or "acs_income_classifier",
            model_version=_env_str("MODEL_VERSION", None),
            model_alias=_env_str("MODEL_ALIAS", "production") or "production",
            mlflow_tracking_uri=_required_env("MLFLOW_TRACKING_URI"),
            mlflow_registry_uri=_env_str("MLFLOW_REGISTRY_URI", None),
            app_insights_connection_string=_env_str("APPLICATIONINSIGHTS_CONNECTION_STRING", None),
            service_name=_env_str("SERVICE_NAME", "serving-api") or "serving-api",
            service_version=_env_str("SERVICE_VERSION", "1.0.0") or "1.0.0",
            environment=_env_str("ENVIRONMENT", "production") or "production",
            telemetry_disable_offline_storage=_env_bool("TELEMETRY_DISABLE_OFFLINE_STORAGE", False),
            telemetry_enable_live_metrics=_env_bool("TELEMETRY_ENABLE_LIVE_METRICS", True),
            telemetry_enable_performance_counters=_env_bool(
                "TELEMETRY_ENABLE_PERFORMANCE_COUNTERS", True
            ),
            telemetry_enable_trace_based_sampling_for_logs=_env_bool(
                "TELEMETRY_ENABLE_TRACE_BASED_SAMPLING_FOR_LOGS", False
            ),
        )


def get_serving_config() -> ServingConfig:
    """Return the application-wide configuration."""
    return ServingConfig.from_env()
