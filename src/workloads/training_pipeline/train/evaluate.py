"""Evaluation utilities for binary classification."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


@dataclass(frozen=True, slots=True)
class SplitMetrics:
    """Metrics for one dataset split."""

    roc_auc: float
    average_precision: float
    f1: float
    precision: float
    recall: float
    accuracy: float
    tn: int
    fp: int
    fn: int
    tp: int
    prediction_latency_ms_per_row: float
    row_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "roc_auc": self.roc_auc,
            "average_precision": self.average_precision,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "accuracy": self.accuracy,
            "tn": self.tn,
            "fp": self.fp,
            "fn": self.fn,
            "tp": self.tp,
            "prediction_latency_ms_per_row": self.prediction_latency_ms_per_row,
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Validation and test metrics."""

    validation: SplitMetrics
    test: SplitMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "validation": self.validation.as_dict(),
            "test": self.test.as_dict(),
        }


def _validate_binary_targets(y: np.ndarray) -> np.ndarray:
    y_array = np.asarray(y)

    if y_array.ndim != 1:
        raise ValueError("Target array must be one-dimensional")
    if y_array.size == 0:
        raise ValueError("Target array must not be empty")

    unique_values = set(np.unique(y_array).tolist())
    if not unique_values.issubset({0, 1}):
        raise ValueError(
            "Binary classification targets must contain only 0 and 1; "
            f"found {sorted(unique_values)}"
        )

    return y_array.astype(np.int64, copy=False)


def _positive_class_probabilities(
    model: Any,
    X: np.ndarray,
) -> np.ndarray:
    raw = np.asarray(model.predict_proba(X))

    if raw.ndim == 1:
        if raw.shape[0] != X.shape[0]:
            raise ValueError("predict_proba returned an unexpected shape")
        return raw.astype(np.float64, copy=False)

    if raw.ndim != 2:
        raise ValueError("predict_proba must return a 1D or 2D array")
    if raw.shape[0] != X.shape[0]:
        raise ValueError("predict_proba returned an unexpected number of rows")
    if raw.shape[1] == 0:
        raise ValueError("predict_proba returned an empty probability matrix")
    if raw.shape[1] == 1:
        return raw[:, 0].astype(np.float64, copy=False)
    return raw[:, 1].astype(np.float64, copy=False)


def evaluate_binary_classifier(
    model: Any,
    X: np.ndarray,
    y: np.ndarray,
    *,
    threshold: float = 0.5,
) -> SplitMetrics:
    X_array = np.asarray(X)

    if X_array.ndim != 2:
        raise ValueError("Feature matrix must be two-dimensional")
    if X_array.shape[0] == 0:
        raise ValueError("Cannot evaluate an empty split")

    y_array = _validate_binary_targets(y)
    if X_array.shape[0] != y_array.shape[0]:
        raise ValueError(
            "Feature matrix and target vector must have the same number of rows "
            f"({X_array.shape[0]} != {y_array.shape[0]})"
        )

    start = perf_counter()
    probabilities = _positive_class_probabilities(model, X_array)
    elapsed = perf_counter() - start

    predictions = (probabilities >= threshold).astype(np.int64, copy=False)

    if np.unique(y_array).size < 2:
        roc_auc = 0.5
        avg_precision = float(np.mean(y_array))
    else:
        roc_auc = float(roc_auc_score(y_array, probabilities))
        avg_precision = float(average_precision_score(y_array, probabilities))

    precision = float(precision_score(y_array, predictions, zero_division=0))  # type: ignore[arg-type]
    recall = float(recall_score(y_array, predictions, zero_division=0))  # type: ignore[arg-type]
    f1 = float(f1_score(y_array, predictions, zero_division=0))  # type: ignore[arg-type]
    accuracy = float(accuracy_score(y_array, predictions))
    tn, fp, fn, tp = (
        int(value) for value in confusion_matrix(y_array, predictions, labels=[0, 1]).ravel()
    )

    latency_ms_per_row = elapsed * 1000.0 / float(X_array.shape[0])

    return SplitMetrics(
        roc_auc=roc_auc,
        average_precision=avg_precision,
        f1=f1,
        precision=precision,
        recall=recall,
        accuracy=accuracy,
        tn=tn,
        fp=fp,
        fn=fn,
        tp=tp,
        prediction_latency_ms_per_row=latency_ms_per_row,
        row_count=int(X_array.shape[0]),
    )


def evaluate_model(
    model: Any,
    *,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    threshold: float = 0.5,
) -> EvaluationSummary:
    return EvaluationSummary(
        validation=evaluate_binary_classifier(
            model, X_validation, y_validation, threshold=threshold
        ),
        test=evaluate_binary_classifier(model, X_test, y_test, threshold=threshold),
    )


def feature_importance_table(
    model: Any,
    feature_names: Sequence[str],
) -> list[dict[str, Any]]:
    if not hasattr(model, "booster_"):
        raise ValueError("Model must expose a fitted booster_ attribute")

    importance = np.asarray(
        model.booster_.feature_importance(importance_type="gain"),
        dtype=np.float64,
    )

    if importance.shape[0] != len(feature_names):
        raise ValueError(
            "Feature name count does not match feature importance length "
            f"({len(feature_names)} != {importance.shape[0]})"
        )

    rows = [
        {"feature": name, "gain": float(gain)}
        for name, gain in zip(feature_names, importance, strict=True)
    ]
    rows.sort(key=lambda item: item["gain"], reverse=True)
    return rows
