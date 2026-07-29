# ============================================================
# api/dependencies.py
# ============================================================
from __future__ import annotations

from typing import cast

from fastapi import HTTPException, Request, status
from model.loader import ModelLoader
from model.predictor import Predictor


def get_model_loader(request: Request) -> ModelLoader:
    loader = getattr(request.app.state, "model_loader", None)
    if loader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model loader not initialised",
        )
    return cast(ModelLoader, loader)


def get_predictor(request: Request) -> Predictor:
    predictor = getattr(request.app.state, "predictor", None)
    if predictor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Predictor not ready",
        )
    return cast(Predictor, predictor)
