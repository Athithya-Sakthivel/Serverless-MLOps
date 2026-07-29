"""
FastAPI application composition root.

Telemetry must be initialized before importing FastAPI so the Azure Monitor
distro can instrument the framework correctly. The app uses lifespan for
startup/shutdown because that is the recommended FastAPI pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from utils.config import get_serving_config
from utils.telemetry import init_telemetry, shutdown_telemetry

config = get_serving_config()
init_telemetry(config)

import fastapi  # noqa: E402
from api import health_router, metrics_router, predict_router  # noqa: E402
from model.loader import ModelLoader  # noqa: E402
from model.predictor import Predictor  # noqa: E402
from model.registry import ModelRegistry  # noqa: E402
from utils.logging import get_logger  # noqa: E402

LOG = get_logger(__name__)


def create_app() -> fastapi.FastAPI:
    @asynccontextmanager
    async def lifespan(application: fastapi.FastAPI) -> AsyncIterator[None]:
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


app = create_app()
__all__ = ["app", "create_app"]
