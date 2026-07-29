"""
FastAPI middleware that injects a request ID and logs every request.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import get_logger

LOG = get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Add a unique request ID to each request and log access details."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate or propagate request ID
        request_id = request.headers.get(_REQUEST_ID_HEADER, str(uuid.uuid4()))
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 3)

        # Structured access log
        LOG.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else None,
            },
        )

        # Echo back the request ID so clients can correlate
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
