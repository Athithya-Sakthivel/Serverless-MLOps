# ============================================================
# api/metrics.py
# ============================================================
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        content="Not Implemented\n",
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
    )
