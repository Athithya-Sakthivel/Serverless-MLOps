# ============================================================
# utils/telemetry.py
# ============================================================
"""
OpenTelemetry setup for traces, metrics, and log correlation.

Design:
- configure_azure_monitor is imported lazily – the import only happens when a
  connection string is present.  This keeps the module import fast and safe
  in environments without Application Insights (local dev, CI, tests).
- The log record factory is always installed so structured JSON logs carry
  trace_id / span_id fields, even when telemetry is disabled.
- Repeated calls to init_telemetry are no-ops.
- shutdown_telemetry restores the original log record factory.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from utils.config import ServingConfig

LOG = logging.getLogger(__name__)

_TRACE_ID_ZERO = "0" * 32
_SPAN_ID_ZERO = "0" * 16
_TRACE_FLAGS_ZERO = "00"

_LOCK = threading.RLock()


@dataclass(slots=True)
class _TelemetryState:
    initialized: bool = False
    connection_string: str | None = None
    previous_log_record_factory: Callable[..., logging.LogRecord] | None = None
    resource: Resource | None = None


_STATE = _TelemetryState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_resource(config: ServingConfig) -> Resource:
    service_name = getattr(config, "service_name", "serving-api")
    service_version = getattr(config, "service_version", "1.0.0")
    environment = getattr(config, "environment", "production")
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )


def _current_trace_fields() -> tuple[str, str, str]:
    current_span = trace.get_current_span()
    span_context = current_span.get_span_context()
    if span_context is None or not span_context.is_valid:
        return _TRACE_ID_ZERO, _SPAN_ID_ZERO, _TRACE_FLAGS_ZERO
    trace_id = f"{span_context.trace_id:032x}"
    span_id = f"{span_context.span_id:016x}"
    trace_flags = f"{int(span_context.trace_flags):02x}"
    return trace_id, span_id, trace_flags


def _install_log_record_factory() -> None:
    if _STATE.previous_log_record_factory is not None:
        return
    previous_factory = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        trace_id_val, span_id_val, trace_flags_val = _current_trace_fields()
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_val
        if not hasattr(record, "span_id"):
            record.span_id = span_id_val
        if not hasattr(record, "trace_flags"):
            record.trace_flags = trace_flags_val
        return record

    _STATE.previous_log_record_factory = previous_factory
    logging.setLogRecordFactory(record_factory)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_telemetry(config: ServingConfig) -> None:
    """
    Initialise OpenTelemetry for Azure Monitor.

    If no connection string is available the function returns immediately after
    installing the log-correlation record factory.  The heavy Azure import
    happens lazily so that environments without Application Insights (local
    dev, CI, tests) never pay the cost.
    """
    with _LOCK:
        if _STATE.initialized:
            return

        _install_log_record_factory()

        connection_string = (
            getattr(config, "app_insights_connection_string", None)
            or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
            or None
        )

        resource = _build_resource(config)
        _STATE.resource = resource
        _STATE.connection_string = connection_string

        if not connection_string:
            LOG.info(
                "Application Insights connection string not configured; telemetry is disabled."
            )
            _STATE.initialized = True
            return

        # Lazy import – only when telemetry is actually enabled
        from azure.monitor.opentelemetry import (  # noqa: E402
            configure_azure_monitor,
        )

        disable_offline_storage = getattr(config, "telemetry_disable_offline_storage", False)
        enable_live_metrics = getattr(config, "telemetry_enable_live_metrics", True)
        enable_performance_counters = getattr(config, "telemetry_enable_performance_counters", True)
        enable_trace_based_sampling = getattr(
            config, "telemetry_enable_trace_based_sampling_for_logs", False
        )

        configure_azure_monitor(
            connection_string=connection_string,
            disable_offline_storage=disable_offline_storage,
            resource=resource,
            enable_live_metrics=enable_live_metrics,
            enable_performance_counters=enable_performance_counters,
            enable_trace_based_sampling_for_logs=enable_trace_based_sampling,
        )

        _STATE.initialized = True
        LOG.info(
            "Telemetry initialised for service_name=%s environment=%s",
            resource.attributes.get("service.name"),
            resource.attributes.get("deployment.environment"),
        )


async def shutdown_telemetry() -> None:
    """
    Gracefully flush and shut down telemetry.

    Restores the original log record factory so that subsequent logs are not
    mutated by a stale record factory reference.
    """
    with _LOCK:
        if not _STATE.initialized:
            return

        if _STATE.previous_log_record_factory is not None:
            logging.setLogRecordFactory(_STATE.previous_log_record_factory)
            _STATE.previous_log_record_factory = None

        def _shutdown(provider: Any) -> None:
            shutdown_fn = getattr(provider, "shutdown", None)
            if callable(shutdown_fn):
                try:
                    shutdown_fn()
                except Exception:
                    LOG.exception("Telemetry provider shutdown failed")

        _shutdown(metrics.get_meter_provider())
        _shutdown(trace.get_tracer_provider())

        _STATE.initialized = False
        _STATE.connection_string = None
        _STATE.resource = None
