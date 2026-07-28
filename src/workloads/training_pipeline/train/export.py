"""Export LightGBM models to ONNX and validate inference parity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort
from onnxmltools.convert.common.data_types import FloatTensorType
from onnxmltools.convert.lightgbm import convert as convert_lightgbm


@dataclass(frozen=True, slots=True)
class OnnxExportResult:
    """ONNX artifact metadata."""

    onnx_path: Path
    sha256: str
    max_abs_probability_delta: float
    sample_count: int


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_probability_from_onnx(
    outputs: list[np.ndarray],
    output_names: list[str],
) -> np.ndarray:
    """Extract the positive-class probability tensor from ONNX outputs."""
    named_outputs = list(zip(output_names, outputs, strict=True))

    # Prefer an output whose name clearly identifies probabilities.
    for name, candidate in named_outputs:
        array = np.asarray(candidate)
        if "prob" in name.lower():
            if array.ndim == 2 and array.shape[1] >= 2:
                return array[:, 1].astype(np.float32, copy=False)
            if array.ndim == 1:
                return array.astype(np.float32, copy=False)

    # Fall back to the last plausible probability tensor.
    for candidate in reversed(outputs):
        array = np.asarray(candidate)
        if array.ndim == 2 and array.shape[1] >= 2:
            return array[:, 1].astype(np.float32, copy=False)
        if array.ndim == 1 and array.size > 0:
            return array.astype(np.float32, copy=False)

    raise RuntimeError("Unable to identify ONNX probability output")


def export_lightgbm_classifier_to_onnx(
    model: Any,
    *,
    feature_count: int,
    output_path: Path,
    sample_features: np.ndarray,
    target_opset: int | None = None,
) -> OnnxExportResult:
    """Export a trained LightGBM classifier and verify ONNX output matches."""
    if feature_count <= 0:
        raise ValueError("feature_count must be positive")

    sample_array = np.asarray(sample_features, dtype=np.float32, order="C")
    if sample_array.ndim != 2:
        raise ValueError("sample_features must be a two-dimensional array")
    if sample_array.size == 0:
        raise ValueError("sample_features must not be empty")
    if sample_array.shape[1] != feature_count:
        raise ValueError(
            "sample_features column count must match feature_count "
            f"({sample_array.shape[1]} != {feature_count})"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    onnx_model = convert_lightgbm(
        model,
        name="training_pipeline_lightgbm",
        initial_types=[("input", FloatTensorType([None, feature_count]))],
        target_opset=target_opset,
        zipmap=False,
    )
    onnx.save_model(onnx_model, str(output_path))

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_names = [output.name for output in session.get_outputs()]

    onnx_outputs = session.run(None, {input_name: sample_array})
    onnx_prob = _positive_probability_from_onnx(
        [np.asarray(output) for output in onnx_outputs],
        output_names,
    )

    native_prob = np.asarray(model.predict_proba(sample_array)[:, 1], dtype=np.float32)
    if native_prob.shape != onnx_prob.shape:
        raise RuntimeError(
            "Native and ONNX probability vectors have different shapes "
            f"({native_prob.shape} != {onnx_prob.shape})"
        )

    max_abs_delta = float(np.max(np.abs(native_prob - onnx_prob)))

    if not np.isfinite(max_abs_delta):
        raise RuntimeError("ONNX verification produced a non-finite error value")
    if max_abs_delta > 1e-3:
        raise RuntimeError(
            f"ONNX verification failed: max_abs_probability_delta={max_abs_delta:.6f}"
        )

    return OnnxExportResult(
        onnx_path=output_path,
        sha256=_sha256_file(output_path),
        max_abs_probability_delta=max_abs_delta,
        sample_count=int(sample_array.shape[0]),
    )
