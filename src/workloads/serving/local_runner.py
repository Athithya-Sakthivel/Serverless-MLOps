"""
Local development runner.

CLI flags override common environment variables before the config is read.
This keeps local debugging deterministic without editing env files.
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from utils.config import get_serving_config
from utils.logging import configure_logging


def _apply_environment_overrides(args: argparse.Namespace) -> None:
    overrides = {
        "PORT": str(args.port) if args.port is not None else None,
        "MODEL_VERSION": args.model_version,
        "MODEL_ALIAS": args.model_alias,
        "MODEL_NAME": args.model_name,
        "LOG_LEVEL": args.log_level.upper() if args.log_level is not None else None,
        "MLFLOW_TRACKING_URI": args.mlflow_tracking_uri,
        "MLFLOW_REGISTRY_URI": args.mlflow_registry_uri,
        "APPLICATIONINSIGHTS_CONNECTION_STRING": args.app_insights_connection_string,
        "SERVICE_NAME": args.service_name,
        "SERVICE_VERSION": args.service_version,
        "ENVIRONMENT": args.environment,
    }

    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the serving API locally",
        allow_abbrev=False,
    )
    parser.add_argument("--port", type=int, default=None, help="Listen port")
    parser.add_argument(
        "--model-version",
        type=str,
        default=None,
        help="Explicit model version",
    )
    parser.add_argument(
        "--model-alias",
        type=str,
        default=None,
        help="Model alias to resolve",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=None,
        help="Registered model name",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    parser.add_argument(
        "--mlflow-tracking-uri",
        type=str,
        default=None,
        help="MLflow tracking URI",
    )
    parser.add_argument(
        "--mlflow-registry-uri",
        type=str,
        default=None,
        help="MLflow registry URI",
    )
    parser.add_argument(
        "--app-insights-connection-string",
        type=str,
        default=None,
        help="Application Insights connection string",
    )
    parser.add_argument(
        "--service-name",
        type=str,
        default=None,
        help="OpenTelemetry service.name",
    )
    parser.add_argument(
        "--service-version",
        type=str,
        default=None,
        help="OpenTelemetry service.version",
    )
    parser.add_argument(
        "--environment",
        type=str,
        default=None,
        help="OpenTelemetry deployment.environment",
    )

    args = parser.parse_args()
    _apply_environment_overrides(args)

    config = get_serving_config()
    configure_logging(level=config.log_level)

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
