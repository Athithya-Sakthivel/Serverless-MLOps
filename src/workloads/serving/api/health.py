# ============================================================
# api/health.py
# ============================================================
"""
Liveness and readiness probes.

- /health always returns 200 while the process is alive.
- /ready returns 200 only after the ML model has been loaded.
"""

from __future__ import annotations

from typing import Annotated

from api.dependencies import get_model_loader
from fastapi import APIRouter, Depends, HTTPException, status
from model.loader import ModelLoader
from schemas.response import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness probe: the server process is running."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
async def readiness_check(
    loader: Annotated[ModelLoader, Depends(get_model_loader)],
) -> ReadyResponse:
    """
    Readiness probe: the model is loaded and ready to serve requests.

    Returns 503 if the model is still loading or failed to load.
    """
    if not loader.is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded",
        )

    return ReadyResponse(status="ok", model_loaded=True)
