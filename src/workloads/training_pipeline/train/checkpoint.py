"""Training checkpoint handling – idempotent restart."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from utils.storage import build_blob_service_client, ensure_container, upload_bytes_to_blob

LOG = logging.getLogger(__name__)

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime):
        return _utc(value).isoformat()

    # Guard against builtin immutable types before checking hasattr
    if not isinstance(value, (str, bytes, bytearray)):
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


def training_checkpoint_blob_name(raw_blob_name: str) -> str:
    name = raw_blob_name.strip().lstrip("/")
    if not name:
        raise ValueError("raw_blob_name is required")
    if name.endswith(".parquet"):
        name = name[: -len(".parquet")]
    return f"training/{name}.json"


def read_training_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
    blob_service_client: Any = None,
    credential: object | None = None,
) -> dict[str, Any] | None:
    if blob_service_client is None:
        blob_service_client = build_blob_service_client(
            storage_account_name,
            credential=credential,  # type: ignore[arg-type]
        )

    blob_client = blob_service_client.get_container_client(
        checkpoint_container_name
    ).get_blob_client(training_checkpoint_blob_name(raw_blob_name))

    try:
        downloader = blob_client.download_blob()
        payload = downloader.readall()
    except ResourceNotFoundError:
        return None

    if not payload:
        LOG.warning("Training checkpoint blob is empty for %s", raw_blob_name)
        return None

    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        LOG.warning("Training checkpoint blob is invalid JSON for %s", raw_blob_name)
        return None


def write_training_checkpoint(
    *,
    storage_account_name: str,
    checkpoint_container_name: str,
    raw_blob_name: str,
    payload: dict[str, Any],
    blob_service_client: Any = None,
    credential: object | None = None,
) -> str:
    if blob_service_client is None:
        blob_service_client = build_blob_service_client(
            storage_account_name,
            credential=credential,  # type: ignore[arg-type]
        )

    ensure_container(blob_service_client, checkpoint_container_name)
    blob_name = training_checkpoint_blob_name(raw_blob_name)
    serialized = json.dumps(
        _jsonify(payload),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=_jsonify,
    ).encode("utf-8")
    upload_bytes_to_blob(
        blob_service_client,
        container_name=checkpoint_container_name,
        blob_name=blob_name,
        data=serialized,
        content_type="application/json",
        overwrite=True,
    )
    LOG.info("Training checkpoint written: %s", blob_name)
    return blob_name


def build_training_checkpoint(
    *,
    status: str,
    pipeline_run_id: str,
    raw_blob_name: str,
    clean_blob_name: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    duration_seconds: float | None = None,
    git_sha: str | None = None,
    container_image_digest: str | None = None,
    mlflow_run_id: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    onnx_sha256: str | None = None,
    seed: int | None = None,
    target_threshold: int | None = None,
    metrics: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    started_utc = _utc(started_at)

    if finished_at is not None:
        finished_utc = _utc(finished_at)
        finished_iso = finished_utc.isoformat()
        if duration_seconds is None:
            duration_seconds = round((finished_utc - started_utc).total_seconds(), 3)
    else:
        finished_iso = None

    return {
        "status": status,
        "pipeline_run_id": pipeline_run_id,
        "raw_blob_name": raw_blob_name,
        "clean_blob_name": clean_blob_name,
        "started_at": started_utc.isoformat(),
        "finished_at": finished_iso,
        "duration_seconds": duration_seconds,
        "git_sha": git_sha,
        "container_image_digest": container_image_digest,
        "mlflow_run_id": mlflow_run_id,
        "model_name": model_name,
        "model_version": model_version,
        "onnx_sha256": onnx_sha256,
        "seed": seed,
        "target_threshold": target_threshold,
        "metrics": _jsonify(metrics or {}),
        "message": message,
    }
