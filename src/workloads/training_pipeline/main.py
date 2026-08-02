"""ACA Job entrypoint – run ELT, then training"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from elt.extract import read_parquet_from_blob, resolve_input_blob_name
from elt.load import (
    checkpoint_payload,
    clean_blob_name,
    read_checkpoint,
    write_checkpoint,
    write_clean_frame,
)
from elt.transform import clean_raw_frame
from elt.validate import validate_raw_frame
from train.orchestrator import run_training_pipeline
from utils.config import AppConfig
from utils.logging import configure_logging, get_logger
from utils.timing import utc_now

LOG = get_logger(__name__)


def _jsonify(value: Any) -> Any:
    """Convert common runtime objects into JSON-safe values for ELT checkpoints."""
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

    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass

    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}

    if isinstance(value, tuple):
        return [_jsonify(item) for item in value]

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonify(item) for item in value]

    return str(value)


def _run_elt(
    config: AppConfig,
    raw_blob_name: str,
    blob_service_client: Any = None,
    credential: object | None = None,
) -> str:
    """Execute the ELT phase if not already completed. Returns clean blob name."""
    existing = read_checkpoint(
        storage_account_name=config.storage.storage_account_name,
        checkpoint_container_name=config.storage.checkpoint_container_name,
        raw_blob_name=raw_blob_name,
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )
    if existing and existing.get("status") == "COMPLETED":
        clean_name = existing["clean_blob_name"]
        LOG.info("ELT checkpoint already completed, skipping ELT for %s", raw_blob_name)
        return clean_name

    started = utc_now()
    raw_frame = read_parquet_from_blob(
        storage_account_name=config.storage.storage_account_name,
        container_name=config.storage.raw_container_name,
        blob_name=raw_blob_name,
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )
    validation_report = validate_raw_frame(raw_frame)
    clean_frame, transform_metrics = clean_raw_frame(raw_frame, validation_report)

    clean_name = clean_blob_name(raw_blob_name)
    write_clean_frame(
        clean_frame,
        storage_account_name=config.storage.storage_account_name,
        clean_container_name=config.storage.clean_container_name,
        clean_blob_name_value=clean_name,
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )

    finished = utc_now()
    payload = checkpoint_payload(
        raw_blob_name=raw_blob_name,
        clean_blob_name=clean_name,
        validation_report={
            "row_count": validation_report.row_count,
            "column_names": list(validation_report.column_names),
            "duplicate_rows": validation_report.duplicate_rows,
            "invalid_state_rows": validation_report.invalid_state_rows,
            "warnings": list(validation_report.warnings),
            "null_counts": validation_report.null_counts,
        },
        transform_metrics={
            "input_rows": transform_metrics.input_rows,
            "output_rows": transform_metrics.output_rows,
            "duplicates_removed": transform_metrics.duplicates_removed,
            "null_rows_removed": transform_metrics.null_rows_removed,
            "invalid_state_rows_removed": transform_metrics.invalid_state_rows_removed,
            "invalid_age_rows_removed": transform_metrics.invalid_age_rows_removed,
            "invalid_hours_rows_removed": transform_metrics.invalid_hours_rows_removed,
            "invalid_year_rows_removed": transform_metrics.invalid_year_rows_removed,
            "warnings": list(transform_metrics.warnings),
        },
        started_at=started,
        finished_at=finished,
        status="COMPLETED",
    )
    write_checkpoint(
        storage_account_name=config.storage.storage_account_name,
        checkpoint_container_name=config.storage.checkpoint_container_name,
        raw_blob_name=raw_blob_name,
        payload=_jsonify(payload),
        blob_service_client=blob_service_client,
        credential=credential,  # type: ignore[arg-type]
    )

    LOG.info("ELT completed: clean blob = %s", clean_name)
    return clean_name


def main() -> int:
    configure_logging()
    config = AppConfig.from_env()
    raw_blob_name = resolve_input_blob_name()

    clean_blob_name_value = _run_elt(config, raw_blob_name)

    training_result = run_training_pipeline(
        config=config,
        raw_blob_name=raw_blob_name,
        clean_blob_name=clean_blob_name_value,
    )

    LOG.info(
        "Pipeline finished: raw=%s clean=%s run_id=%s onnx_sha256=%s",
        raw_blob_name,
        clean_blob_name_value,
        training_result.mlflow_run_id,
        training_result.onnx_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
