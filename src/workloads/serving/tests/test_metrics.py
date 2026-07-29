# ============================================================
# tests/test_metrics.py
# ============================================================
from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_returns_not_implemented(test_app: TestClient) -> None:
    response = test_app.get("/metrics")
    assert response.status_code == 501
    assert response.text == "Not Implemented\n"
