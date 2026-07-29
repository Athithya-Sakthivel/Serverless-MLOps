# ============================================================
# schemas/request.py
# ============================================================
"""
Pydantic model for the inference request.
Expects a list of feature vectors (each a list of floats).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PredictRequest(BaseModel):
    """Request body for POST /predict."""

    features: list[list[float]] = Field(
        ...,
        description="List of feature vectors. Each vector must contain exactly the number of features expected by the model.",
        min_length=1,
    )
    request_id: str | None = Field(None, description="Optional client-supplied correlation ID.")

    @field_validator("features")
    @classmethod
    def check_non_empty_vectors(cls, v: list[list[float]]) -> list[list[float]]:
        if not v:
            raise ValueError("features list must not be empty")
        # All rows must have the same length
        first_len = len(v[0])
        for i, row in enumerate(v):
            if len(row) != first_len:
                raise ValueError(f"Row {i} has {len(row)} features, expected {first_len}")
        return v
