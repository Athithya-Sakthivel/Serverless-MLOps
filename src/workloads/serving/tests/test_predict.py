# ============================================================
# tests/test_predict.py
# ============================================================
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from schemas.response import PredictResponse


def test_predict_success(test_app: TestClient, mock_predictor: MagicMock) -> None:
    # Setup mock predictor to return a valid response
    mock_predictor.predict.return_value = PredictResponse(
        probabilities=[0.8, 0.2],
        prediction=[1, 0],
        model_version="1",
    )

    payload = {"features": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]}
    response = test_app.post("/predict", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["probabilities"] == [0.8, 0.2]
    assert data["prediction"] == [1, 0]
    assert data["model_version"] == "1"


def test_predict_invalid_input_shape(test_app: TestClient, mock_predictor: MagicMock) -> None:
    # The Pydantic schema requires at least one feature vector, and all rows same length.
    # Sending an empty list is caught by the request validator.
    response = test_app.post("/predict", json={"features": []})
    assert response.status_code == 422  # Validation error


def test_predict_raises_value_error(test_app: TestClient, mock_predictor: MagicMock) -> None:
    mock_predictor.predict.side_effect = ValueError("invalid input")
    payload = {"features": [[1.0, 2.0]]}
    response = test_app.post("/predict", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid input"


def test_predict_raises_runtime_error(test_app: TestClient, mock_predictor: MagicMock) -> None:
    mock_predictor.predict.side_effect = RuntimeError("something went wrong")
    payload = {"features": [[1.0, 2.0]]}
    response = test_app.post("/predict", json=payload)
    assert response.status_code == 500
    assert response.json()["detail"] == "Inference error"
