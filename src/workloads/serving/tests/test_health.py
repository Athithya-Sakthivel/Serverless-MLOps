# ============================================================
# tests/test_health.py
# ============================================================
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_always_returns_ok(test_app: TestClient) -> None:
    response = test_app.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_model_loaded(test_app: TestClient) -> None:
    response = test_app.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_loaded": True}


def test_ready_returns_503_when_model_not_loaded(test_app: TestClient, mock_model_loader) -> None:
    mock_model_loader.is_ready = False
    response = test_app.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Model not loaded"
