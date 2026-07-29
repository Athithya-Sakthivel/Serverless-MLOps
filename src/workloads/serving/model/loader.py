# ============================================================
# model/loader.py
# ============================================================
"""
Download the ONNX model from MLflow and load it into an ONNX Runtime session.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import onnxruntime as ort
from mlflow.artifacts import download_artifacts
from utils.logging import get_logger

from .registry import ModelRegistry

LOG = get_logger(__name__)


class ModelLoader:
    """Responsible for fetching and loading the ONNX model."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry
        self._session: ort.InferenceSession | None = None

    def load(self) -> ort.InferenceSession:
        """Download the ONNX file from MLflow and create an InferenceSession."""
        if self._session is not None:
            return self._session

        resolution = self._registry.resolve()
        LOG.info(
            "Downloading ONNX model run_id=%s artifact_uri=%s",
            resolution.run_id,
            resolution.artifact_uri,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = download_artifacts(
                artifact_uri=resolution.artifact_uri,
                dst_path=tmp_dir,
                tracking_uri=self._registry.tracking_uri,
                registry_uri=self._registry.registry_uri,
            )
            onnx_file = Path(local_path)

            if not onnx_file.exists():
                raise FileNotFoundError(f"ONNX artifact not found at {local_path} after download")
            if not onnx_file.is_file():
                raise RuntimeError(
                    f"Expected a file artifact, but MLflow returned a directory: {local_path}"
                )

            LOG.info("ONNX model downloaded to %s", onnx_file)

            self._session = ort.InferenceSession(
                str(onnx_file),
                providers=["CPUExecutionProvider"],
            )

            LOG.info(
                "ONNX model loaded successfully. Inputs: %s | Outputs: %s",
                [inp.name for inp in self._session.get_inputs()],
                [out.name for out in self._session.get_outputs()],
            )

        return self._session

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        return self._session

    @property
    def is_ready(self) -> bool:
        """True when the session has been loaded and is ready for inference."""
        return self._session is not None

    @property
    def model_version(self) -> str:
        """Return the resolved model version, e.g. '5'."""
        return self._registry.resolve().model_version
