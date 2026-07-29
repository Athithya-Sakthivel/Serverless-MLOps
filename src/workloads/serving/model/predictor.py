# ============================================================
# model/predictor.py
# ============================================================
"""
Inference pipeline with thread‑safe ONNX execution.
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import onnxruntime as ort
from schemas.request import PredictRequest
from schemas.response import PredictResponse

LOG = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.5


class Predictor:
    """Runs ONNX inference on preprocessed input.  Safe for concurrent requests."""

    def __init__(self, session: ort.InferenceSession, model_version: str) -> None:
        self._session = session
        self._model_version = model_version
        self._lock = threading.Lock()

        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()

        if not inputs:
            raise RuntimeError("ONNX model has no inputs.")
        if not outputs:
            raise RuntimeError("ONNX model has no outputs.")

        self._input_name = inputs[0].name
        self._output_name = outputs[0].name

        LOG.info(
            "Predictor initialized with input=%s output=%s model_version=%s",
            self._input_name,
            self._output_name,
            self._model_version,
        )

    def predict(self, request: PredictRequest) -> PredictResponse:
        try:
            features_array = np.asarray(request.features, dtype=np.float32)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid feature data: {exc}") from exc

        if features_array.ndim != 2:
            raise ValueError("Feature array must be 2-dimensional")

        ort_inputs = {self._input_name: features_array}
        # InferenceSession is not thread-safe; acquire lock
        with self._lock:
            ort_outputs = self._session.run([self._output_name], ort_inputs)

        if not ort_outputs:
            raise RuntimeError("ONNX model produced no output")

        raw_output = np.asarray(ort_outputs[0])
        probabilities = self._extract_positive_class_probabilities(raw_output)
        predictions = (probabilities >= _DEFAULT_THRESHOLD).astype(np.int64)

        return PredictResponse(
            probabilities=probabilities.astype(float).tolist(),
            prediction=predictions.tolist(),
            model_version=self._model_version,
        )

    @staticmethod
    def _extract_positive_class_probabilities(raw_output: np.ndarray) -> np.ndarray:
        if raw_output.ndim == 1:
            return raw_output.astype(np.float32, copy=False)
        if raw_output.ndim == 2:
            if raw_output.shape[1] == 1:
                return raw_output[:, 0].astype(np.float32, copy=False)
            if raw_output.shape[1] >= 2:
                return raw_output[:, 1].astype(np.float32, copy=False)
        raise RuntimeError(f"Unexpected ONNX output shape: {raw_output.shape}")
