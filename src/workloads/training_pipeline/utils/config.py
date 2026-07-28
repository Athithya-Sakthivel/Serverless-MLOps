"""Environment-backed configuration with fail-fast semantics."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required_env(name: str) -> str:
    value = _env_str(name)
    if value is None:
        raise ValueError(f"Environment variable {name} is required")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env_str(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = _env_str(name)
    return float(value) if value is not None else default


@dataclass(frozen=True, slots=True)
class StorageConfig:
    storage_account_name: str
    raw_container_name: str = "raw"
    clean_container_name: str = "clean"
    checkpoint_container_name: str = "checkpoints"


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    target_income_threshold: int = 50_000
    random_seed: int = 42
    train_fraction: float = 0.70
    validation_fraction: float = 0.15
    test_fraction: float = 0.15
    min_auc: float = 0.90
    min_f1: float = 0.85
    min_precision: float = 0.85
    min_recall: float = 0.85
    objective: str = "binary"
    metric: str = "auc"
    boosting_type: str = "gbdt"
    learning_rate: float = 0.05
    num_leaves: int = 63
    feature_fraction: float = 0.80
    bagging_fraction: float = 0.80
    bagging_freq: int = 1
    min_data_in_leaf: int = 50
    num_boost_round: int = 500
    early_stopping_rounds: int = 50
    verbosity: int = -1
    n_jobs: int = 0
    model_name: str = "acs_income_classifier"
    promotion_stage: str = "Staging"
    enable_model_registration: bool = False


@dataclass(frozen=True, slots=True)
class MlflowConfig:
    tracking_uri: str
    experiment_name: str = "training_pipeline"


@dataclass(frozen=True, slots=True)
class AppConfig:
    storage: StorageConfig
    training: TrainingConfig
    mlflow: MlflowConfig
    git_sha: str | None = None
    container_image_digest: str | None = None

    @classmethod
    def from_env(cls) -> AppConfig:
        storage = StorageConfig(
            storage_account_name=_required_env("AZURE_STORAGE_ACCOUNT_NAME"),
            raw_container_name=_env_str("RAW_CONTAINER_NAME", "raw") or "raw",
            clean_container_name=_env_str("CLEAN_CONTAINER_NAME", "clean") or "clean",
            checkpoint_container_name=_env_str("CHECKPOINT_CONTAINER_NAME", "checkpoints")
            or "checkpoints",
        )

        training = TrainingConfig(
            target_income_threshold=_env_int("TRAINING_TARGET_INCOME_THRESHOLD", 50_000),
            random_seed=_env_int("TRAIN_RANDOM_SEED", 42),
            train_fraction=_env_float("TRAIN_FRACTION", 0.70),
            validation_fraction=_env_float("VALIDATION_FRACTION", 0.15),
            test_fraction=_env_float("TEST_FRACTION", 0.15),
            min_auc=_env_float("MIN_AUC", 0.90),
            min_f1=_env_float("MIN_F1", 0.85),
            min_precision=_env_float("MIN_PRECISION", 0.85),
            min_recall=_env_float("MIN_RECALL", 0.85),
            objective=_env_str("LIGHTGBM_OBJECTIVE", "binary") or "binary",
            metric=_env_str("LIGHTGBM_METRIC", "auc") or "auc",
            boosting_type=_env_str("LIGHTGBM_BOOSTING_TYPE", "gbdt") or "gbdt",
            learning_rate=_env_float("LIGHTGBM_LEARNING_RATE", 0.05),
            num_leaves=_env_int("LIGHTGBM_NUM_LEAVES", 63),
            feature_fraction=_env_float("LIGHTGBM_FEATURE_FRACTION", 0.80),
            bagging_fraction=_env_float("LIGHTGBM_BAGGING_FRACTION", 0.80),
            bagging_freq=_env_int("LIGHTGBM_BAGGING_FREQ", 1),
            min_data_in_leaf=_env_int("LIGHTGBM_MIN_DATA_IN_LEAF", 50),
            num_boost_round=_env_int("LIGHTGBM_NUM_BOOST_ROUND", 500),
            early_stopping_rounds=_env_int("LIGHTGBM_EARLY_STOPPING_ROUNDS", 50),
            verbosity=_env_int("LIGHTGBM_VERBOSITY", -1),
            n_jobs=_env_int("LIGHTGBM_N_JOBS", 0),
            model_name=_env_str("MODEL_NAME", "acs_income_classifier") or "acs_income_classifier",
            promotion_stage=_env_str("MODEL_STAGE", "Staging") or "Staging",
            enable_model_registration=_env_bool("ENABLE_MODEL_REGISTRATION", False),
        )

        tracking_uri = _required_env("MLFLOW_TRACKING_URI")
        mlflow = MlflowConfig(
            tracking_uri=tracking_uri,
            experiment_name=_env_str("MLFLOW_EXPERIMENT_NAME", "training_pipeline")
            or "training_pipeline",
        )

        return cls(
            storage=storage,
            training=training,
            mlflow=mlflow,
            git_sha=_env_str("GIT_SHA"),
            container_image_digest=_env_str("CONTAINER_IMAGE_DIGEST"),
        )
