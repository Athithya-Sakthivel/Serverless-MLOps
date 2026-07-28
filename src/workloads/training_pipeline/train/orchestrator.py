"""Training workflow orchestration – idempotent, checkpointed.

Compatible with MLflow 3.x, Python 3.14, and Azure Machine Learning.
Uses artifact-based model logging + manual registration to avoid
the LoggedModels endpoint that Azure ML does not yet support.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
import mlflow
import numpy as np
from train.checkpoint import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    build_training_checkpoint,
    read_training_checkpoint,
    write_training_checkpoint,
)
from train.dataset import create_target_column, load_clean_frame, split_dataset
from train.evaluate import evaluate_model, feature_importance_table
from train.export import export_lightgbm_classifier_to_onnx
from train.features import build_feature_bundle
from train.model import build_classifier, train_lightgbm_classifier
from utils.config import AppConfig
from utils.mlflow import configure_mlflow
from utils.timing import utc_now

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TrainingRunResult:
    """Final training output."""

    checkpoint: dict[str, Any]
    mlflow_run_id: str
    model_uri: str
    onnx_sha256: str
    metrics: dict[str, Any]
    registration: dict[str, Any] | None


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if hasattr(value, "item") and callable(value.item):
        try:
            return _jsonify(value.item())
        except Exception:
            pass

    if hasattr(value, "tolist") and callable(value.tolist):
        try:
            return _jsonify(value.tolist())
        except Exception:
            pass

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _jsonify(value.to_dict())
        except Exception:
            pass

    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}

    if isinstance(value, tuple):
        return [_jsonify(item) for item in value]

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonify(item) for item in value]

    return str(value)


def _float_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for split_name, split_metrics in metrics.items():
        if not isinstance(split_metrics, Mapping):
            continue
        for metric_name, value in split_metrics.items():
            if isinstance(value, Real) and not isinstance(value, bool):
                flat[f"{split_name}_{metric_name}"] = float(value)
    return flat


def _promotion_passed(metrics: dict[str, Any], config: AppConfig) -> bool:
    test = metrics["test"]
    training = config.training
    return (
        test["roc_auc"] >= training.min_auc
        and test["f1"] >= training.min_f1
        and test["precision"] >= training.min_precision
        and test["recall"] >= training.min_recall
    )


def _model_artifact_uri(run_id: str, artifact_path: str) -> str:
    return f"runs:/{run_id}/{artifact_path}"


def _first_rows(array: Any, limit: int) -> Any:
    try:
        size = int(array.shape[0])
    except Exception as exc:
        raise ValueError("Training data is not array-like") from exc
    if size <= 0:
        raise ValueError("Training data is empty")
    return array[: min(limit, size)]


def _feature_importance_payload(feature_importance: Any, limit: int = 50) -> Any:
    if hasattr(feature_importance, "head") and callable(feature_importance.head):
        try:
            return feature_importance.head(limit)
        except Exception:
            pass
    try:
        return feature_importance[:limit]
    except Exception:
        return feature_importance


def run_training_pipeline(
    *,
    config: AppConfig,
    raw_blob_name: str,
    clean_blob_name: str,
    pipeline_run_id: str | None = None,
    blob_service_client: Any = None,
    credential: object | None = None,
) -> TrainingRunResult:
    """Run the full training pipeline, respecting existing checkpoints.

    If *blob_service_client* or *credential* are omitted, real Azure clients
    are created.
    """
    pipeline_run_id = pipeline_run_id or uuid4().hex
    started_at = utc_now()

    existing = read_training_checkpoint(
        storage_account_name=config.storage.storage_account_name,
        checkpoint_container_name=config.storage.checkpoint_container_name,
        raw_blob_name=raw_blob_name,
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )
    if existing and existing.get("status") == STATUS_COMPLETED:
        LOG.info("Training checkpoint already completed for %s", raw_blob_name)
        existing_run_id = str(existing.get("mlflow_run_id", ""))
        result = TrainingRunResult(
            checkpoint=existing,
            mlflow_run_id=existing_run_id,
            model_uri=_model_artifact_uri(existing_run_id, "model") if existing_run_id else "",
            onnx_sha256=str(existing.get("onnx_sha256", "")),
            metrics=dict(existing.get("metrics", {})),
            registration=None,
        )
        return result

    running_cp = build_training_checkpoint(
        status=STATUS_RUNNING,
        pipeline_run_id=pipeline_run_id,
        raw_blob_name=raw_blob_name,
        clean_blob_name=clean_blob_name,
        started_at=started_at,
        git_sha=config.git_sha,
        container_image_digest=config.container_image_digest,
        seed=config.training.random_seed,
        target_threshold=config.training.target_income_threshold,
        message="training started",
    )
    write_training_checkpoint(
        storage_account_name=config.storage.storage_account_name,
        checkpoint_container_name=config.storage.checkpoint_container_name,
        raw_blob_name=raw_blob_name,
        payload=running_cp,
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )

    registration_payload: dict[str, Any] | None = None

    try:
        configure_mlflow(config.mlflow)

        with mlflow.start_run(run_name=f"training-{raw_blob_name.replace('/', '_')}") as run:
            clean_frame = load_clean_frame(
                storage_account_name=config.storage.storage_account_name,
                container_name=config.storage.clean_container_name,
                blob_name=clean_blob_name,
                blob_service_client=blob_service_client,
                credential=credential,  # type: ignore[arg-type]
            )
            training_frame = create_target_column(
                clean_frame,
                income_threshold=config.training.target_income_threshold,
            )
            splits = split_dataset(
                training_frame,
                seed=config.training.random_seed,
                train_fraction=config.training.train_fraction,
                validation_fraction=config.training.validation_fraction,
                test_fraction=config.training.test_fraction,
            )
            del clean_frame, training_frame

            features = build_feature_bundle(
                splits.train_frame,
                splits.validation_frame,
                splits.test_frame,
            )
            del splits

            unique_train, counts_train = np.unique(features.y_train, return_counts=True)
            if len(unique_train) < 2:
                msg = (
                    f"Training labels contain only one class. "
                    f"Threshold: {config.training.target_income_threshold}, "
                    f"class counts: {dict(zip(unique_train.tolist(), counts_train.tolist(), strict=True))}"
                )
                raise RuntimeError(msg)

            classifier = build_classifier(config.training)
            trained = train_lightgbm_classifier(
                classifier,
                X_train=features.X_train,
                y_train=features.y_train,
                X_validation=features.X_validation,
                y_validation=features.y_validation,
                early_stopping_rounds=config.training.early_stopping_rounds,
            )

            evaluation = evaluate_model(
                trained.model,
                X_validation=features.X_validation,
                y_validation=features.y_validation,
                X_test=features.X_test,
                y_test=features.y_test,
            )
            eval_metrics = evaluation.as_dict()

            mlflow.log_metrics(_float_metrics(eval_metrics))
            mlflow.log_params(
                {
                    "target_income_threshold": config.training.target_income_threshold,
                    "random_seed": config.training.random_seed,
                    "train_fraction": config.training.train_fraction,
                    "validation_fraction": config.training.validation_fraction,
                    "test_fraction": config.training.test_fraction,
                    "objective": config.training.objective,
                    "boosting_type": config.training.boosting_type,
                    "learning_rate": config.training.learning_rate,
                    "num_leaves": config.training.num_leaves,
                    "feature_fraction": config.training.feature_fraction,
                    "bagging_fraction": config.training.bagging_fraction,
                    "bagging_freq": config.training.bagging_freq,
                    "min_data_in_leaf": config.training.min_data_in_leaf,
                    "num_boost_round": config.training.num_boost_round,
                    "early_stopping_rounds": config.training.early_stopping_rounds,
                    "n_jobs": config.training.n_jobs,
                }
            )

            run_tags: dict[str, str] = {
                "raw_blob_name": raw_blob_name,
                "clean_blob_name": clean_blob_name,
                "pipeline_run_id": pipeline_run_id,
            }
            if config.git_sha:
                run_tags["git_sha"] = config.git_sha
            if config.container_image_digest:
                run_tags["container_image_digest"] = config.container_image_digest
            mlflow.set_tags(run_tags)

            feature_importance = feature_importance_table(
                trained.model, features.transformer.feature_names
            )

            with tempfile.TemporaryDirectory() as tmp_dir:
                model_file = Path(tmp_dir) / "model.pkl"
                joblib.dump(trained.model, model_file)
                mlflow.log_artifact(str(model_file), artifact_path="model")

            model_uri = _model_artifact_uri(run.info.run_id, "model")

            if config.training.enable_model_registration:
                try:
                    registered = mlflow.register_model(
                        model_uri,
                        config.training.model_name,
                    )
                    registration_payload = {
                        "registered_model_name": registered.name,
                        "model_version": str(registered.version),
                    }
                    mlflow.set_tag("model_registered", "true")
                except Exception as reg_exc:
                    LOG.warning("Model registration failed: %s", reg_exc)
                    registration_payload = None
                    mlflow.set_tag("model_registered", "false")
            else:
                mlflow.set_tag("model_registered", "false")

            try:
                sample_validation = _first_rows(features.X_validation, 512)
            except ValueError:
                sample_validation = _first_rows(features.X_train, 100)

            with tempfile.TemporaryDirectory() as tmp_dir:
                onnx_path = Path(tmp_dir) / "model.onnx"
                onnx_result = export_lightgbm_classifier_to_onnx(
                    trained.model,
                    feature_count=features.X_train.shape[1],
                    output_path=onnx_path,
                    sample_features=sample_validation,
                )
                mlflow.log_artifact(str(onnx_result.onnx_path), artifact_path="onnx")
                mlflow.log_metric(
                    "onnx_max_abs_probability_delta",
                    float(onnx_result.max_abs_probability_delta),
                )

            metrics_payload = {
                "evaluation": _jsonify(eval_metrics),
                "feature_importance": _jsonify(
                    _feature_importance_payload(feature_importance, limit=50)
                ),
                "best_iteration": _jsonify(trained.best_iteration),
                "onnx_sha256": onnx_result.sha256,
            }
            mlflow.log_dict(metrics_payload, "reports/metrics.json")

            finished_at = utc_now()
            completed_cp = build_training_checkpoint(
                status=STATUS_COMPLETED,
                pipeline_run_id=pipeline_run_id,
                raw_blob_name=raw_blob_name,
                clean_blob_name=clean_blob_name,
                started_at=started_at,
                finished_at=finished_at,
                git_sha=config.git_sha,
                container_image_digest=config.container_image_digest,
                mlflow_run_id=run.info.run_id,
                model_name=config.training.model_name,
                model_version=registration_payload["model_version"]
                if registration_payload
                else None,
                onnx_sha256=onnx_result.sha256,
                seed=config.training.random_seed,
                target_threshold=config.training.target_income_threshold,
                metrics=eval_metrics,
                message="training completed",
            )
            write_training_checkpoint(
                storage_account_name=config.storage.storage_account_name,
                checkpoint_container_name=config.storage.checkpoint_container_name,
                raw_blob_name=raw_blob_name,
                payload=completed_cp,
                blob_service_client=blob_service_client,
                credential=credential,  # type: ignore[arg-type]
            )

            LOG.info(
                "Training complete: raw=%s clean=%s run_id=%s model_uri=%s",
                raw_blob_name,
                clean_blob_name,
                run.info.run_id,
                model_uri,
            )

            return TrainingRunResult(
                checkpoint=completed_cp,
                mlflow_run_id=run.info.run_id,
                model_uri=model_uri,
                onnx_sha256=onnx_result.sha256,
                metrics=eval_metrics,
                registration=registration_payload,
            )

        raise RuntimeError("Training completed without returning a result")

    except Exception as exc:
        LOG.exception("Training failed for %s", raw_blob_name)
        _write_failed_checkpoint(
            config=config,
            pipeline_run_id=pipeline_run_id,
            raw_blob_name=raw_blob_name,
            clean_blob_name=clean_blob_name,
            started_at=started_at,
            exc=exc,
            blob_service_client=blob_service_client,
            credential=credential,
        )
        raise


def _write_failed_checkpoint(
    *,
    config: AppConfig,
    pipeline_run_id: str,
    raw_blob_name: str,
    clean_blob_name: str,
    started_at: Any,
    exc: Exception,
    blob_service_client: Any,
    credential: object | None,
) -> None:
    """Best‑effort write of a FAILED training checkpoint."""
    try:
        failed_cp = build_training_checkpoint(
            status=STATUS_FAILED,
            pipeline_run_id=pipeline_run_id,
            raw_blob_name=raw_blob_name,
            clean_blob_name=clean_blob_name,
            started_at=started_at,
            finished_at=utc_now(),
            git_sha=config.git_sha,
            container_image_digest=config.container_image_digest,
            seed=config.training.random_seed,
            target_threshold=config.training.target_income_threshold,
            metrics={"error": str(exc)},
            message="training failed",
        )
        write_training_checkpoint(
            storage_account_name=config.storage.storage_account_name,
            checkpoint_container_name=config.storage.checkpoint_container_name,
            raw_blob_name=raw_blob_name,
            payload=failed_cp,
            blob_service_client=blob_service_client,
            credential=credential,  # type: ignore[arg-type]
        )
    except Exception as cp_exc:
        LOG.error("Failed to write failure checkpoint: %s", cp_exc)
