"""
FastAPI application composition root.

Telemetry must be initialized before importing FastAPI so the Azure Monitor
distro can instrument the framework correctly. The app uses lifespan for
startup/shutdown because that is the recommended FastAPI pattern.

The SERVING_SKIP_MODEL_LOAD environment variable allows the test suite and
local development to start the app without downloading the ML model. When set,
the lifespan still runs but skips all network calls — no MLflow, no ONNX
download, no hang.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from utils.config import get_serving_config
from utils.telemetry import init_telemetry, shutdown_telemetry

# Module-level configuration and telemetry initialization.
# init_telemetry is safe to call here because it returns immediately when
# APPLICATIONINSIGHTS_CONNECTION_STRING is empty (see utils/telemetry.py).
config = get_serving_config()
init_telemetry(config)

# FastAPI and application imports. These must come after init_telemetry()
# so the Azure Monitor distro can instrument the framework.
import fastapi  # noqa: E402
from api import health_router, metrics_router, predict_router  # noqa: E402
from model.loader import ModelLoader  # noqa: E402
from model.predictor import Predictor  # noqa: E402
from model.registry import ModelRegistry  # noqa: E402
from utils.logging import get_logger  # noqa: E402

LOG = get_logger(__name__)


def create_app() -> fastapi.FastAPI:
    """
    Build the FastAPI application with lifespan-managed model loading.

    The lifespan is the single place where the model is downloaded and loaded.
    In production, it runs once at startup and the loaded model is shared across
    all requests via app.state.

    In tests and local development without a model, set the environment variable
    SERVING_SKIP_MODEL_LOAD=1. The lifespan will skip model loading entirely.
    Request handlers that depend on app.state.model_loader / app.state.predictor
    should use FastAPI dependency overrides (see tests/conftest.py for the
    test-time overrides).
    """

    @asynccontextmanager
    async def lifespan(application: fastapi.FastAPI) -> AsyncIterator[None]:
        # SERVING_SKIP_MODEL_LOAD is the escape hatch for environments where
        # no real MLflow server is reachable: CI, local dev, test suites.
        # When set, the lifespan still runs but does no network I/O.
        if not os.getenv("SERVING_SKIP_MODEL_LOAD"):
            # Production path: resolve the model version, download the ONNX
            # artifact, load it into an ONNX Runtime session, and create the
            # predictor. These objects are stored on app.state so FastAPI
            # dependency callables can retrieve them at request time.
            registry = ModelRegistry(config)
            resolution = registry.resolve()
            loader = ModelLoader(registry)
            session = loader.load()
            predictor = Predictor(session=session, model_version=resolution.model_version)

            application.state.model_loader = loader
            application.state.predictor = predictor

            LOG.info(
                "Serving app ready: model=%s version=%s run_id=%s",
                resolution.model_name,
                resolution.model_version,
                resolution.run_id,
            )
        else:
            LOG.info("Skipping model load (SERVING_SKIP_MODEL_LOAD set)")

        # The application runs while we yield. When the server shuts down,
        # execution resumes after the yield and we gracefully stop telemetry.
        try:
            yield
        finally:
            await shutdown_telemetry()

    application = fastapi.FastAPI(
        title="Serverless MLOps Serving",
        version=config.service_version,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(predict_router)
    application.include_router(metrics_router)
    return application


# Module-level app instance. Uvicorn and the test client both import this.
app = create_app()
__all__ = ["app", "create_app"]
