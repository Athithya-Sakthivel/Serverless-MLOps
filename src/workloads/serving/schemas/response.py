# ============================================================
# schemas/response.py
# ============================================================
"""
Pydantic models for inference responses and health checks.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PredictResponse(BaseModel):
    """Response for a successful prediction."""

    probabilities: list[float] = Field(
        ..., description="Predicted probability of the positive class (one per input row)."
    )
    prediction: list[int] = Field(..., description="Binary prediction (threshold 0.5).")
    model_version: str = Field(..., description="Model version used for inference.")


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    status: str = "ok"
    model_loaded: bool = Field(..., description="Whether the ML model is loaded in memory.")


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
