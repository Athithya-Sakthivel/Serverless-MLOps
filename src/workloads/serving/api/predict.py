# ============================================================
# api/predict.py
# ============================================================
"""
POST /predict endpoint.

Validates input, runs inference, and returns predictions.
"""

from __future__ import annotations

import logging
from typing import Annotated

from api.dependencies import get_predictor
from fastapi import APIRouter, Depends, HTTPException, status
from model.predictor import Predictor
from schemas.request import PredictRequest
from schemas.response import ErrorResponse, PredictResponse
from starlette.concurrency import run_in_threadpool

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["predict"])


@router.post(
    "/predict",
    response_model=PredictResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        503: {"model": ErrorResponse, "description": "Predictor not ready"},
    },
)
async def predict(
    request: PredictRequest,
    predictor: Annotated[Predictor, Depends(get_predictor)],
) -> PredictResponse:
    """Run inference on the supplied feature vectors."""
    try:
        return await run_in_threadpool(predictor.predict, request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        LOG.exception("Inference failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference error",
        ) from exc
