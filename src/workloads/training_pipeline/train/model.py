"""LightGBM model training with early stopping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
from utils.config import TrainingConfig


@dataclass(frozen=True, slots=True)
class TrainedModel:
    """Trained classifier and metadata."""

    model: lgb.LGBMClassifier
    best_iteration: int | None


def _config_value(config: TrainingConfig, *names: str, default: Any) -> Any:
    """Return the first non-None config attribute among several aliases."""
    for name in names:
        if hasattr(config, name):
            value = getattr(config, name)
            if value is not None:
                return value
    return default


def build_classifier(config: TrainingConfig) -> lgb.LGBMClassifier:
    """Create a deterministic LightGBM classifier from config."""
    return lgb.LGBMClassifier(
        boosting_type=_config_value(config, "boosting_type", default="gbdt"),
        num_leaves=_config_value(config, "num_leaves", default=31),
        max_depth=_config_value(config, "max_depth", default=-1),
        learning_rate=_config_value(config, "learning_rate", default=0.1),
        n_estimators=_config_value(config, "num_boost_round", "n_estimators", default=100),
        subsample_for_bin=_config_value(config, "subsample_for_bin", default=200_000),
        objective=_config_value(config, "objective", default=None),
        class_weight=_config_value(config, "class_weight", default=None),
        min_split_gain=_config_value(config, "min_split_gain", default=0.0),
        min_child_weight=_config_value(config, "min_child_weight", default=0.001),
        min_child_samples=_config_value(
            config,
            "min_child_samples",
            "min_data_in_leaf",
            default=20,
        ),
        subsample=_config_value(config, "subsample", "bagging_fraction", default=1.0),
        subsample_freq=_config_value(config, "subsample_freq", "bagging_freq", default=0),
        colsample_bytree=_config_value(
            config,
            "colsample_bytree",
            "feature_fraction",
            default=1.0,
        ),
        reg_alpha=_config_value(config, "reg_alpha", default=0.0),
        reg_lambda=_config_value(config, "reg_lambda", default=0.0),
        random_state=_config_value(config, "random_seed", "random_state", default=None),
        n_jobs=_config_value(config, "n_jobs", default=None),
        importance_type=_config_value(config, "importance_type", default="split"),
    )


def train_lightgbm_classifier(
    classifier: lgb.LGBMClassifier,
    *,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    early_stopping_rounds: int,
) -> TrainedModel:
    """Fit with early stopping on validation AUC."""
    if early_stopping_rounds < 1:
        raise ValueError("early_stopping_rounds must be at least 1")

    for name, features, target in (
        ("train", X_train, y_train),
        ("validation", X_validation, y_validation),
    ):
        if features.ndim != 2:
            raise ValueError(f"{name} features must be a 2D array")
        if target.ndim != 1:
            raise ValueError(f"{name} target must be a 1D array")
        if features.shape[0] != target.shape[0]:
            raise ValueError(
                f"{name} features and target must have the same number of rows "
                f"({features.shape[0]} != {target.shape[0]})"
            )
        if features.shape[0] == 0:
            raise ValueError(f"{name} split must not be empty")

    classifier.fit(
        X_train,
        y_train,
        eval_set=[(X_validation, y_validation)],
        eval_metric="auc",
        callbacks=[
            lgb.early_stopping(
                stopping_rounds=early_stopping_rounds,
                first_metric_only=True,
                verbose=False,
            )
        ],
    )

    return TrainedModel(
        model=classifier,
        best_iteration=getattr(classifier, "best_iteration_", None),
    )
