from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # Immutable configuration object loaded once from environment variables.
    # Keeping configuration immutable prevents accidental runtime mutation.
    subscription_id: str
    resource_group_name: str
    job_name: str
    job_api_version: str = "2026-01-01"
    request_timeout_seconds: int = 30


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(f"Missing required environment variable: {name}")

    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Azure Functions keeps Python worker processes alive between
    # invocations. Cache configuration once per worker to avoid repeatedly
    # parsing environment variables on every blob event.

    timeout_raw = os.getenv(
        "ACA_REQUEST_TIMEOUT_SECONDS",
        "30",
    ).strip()

    try:
        timeout_seconds = int(timeout_raw)
    except ValueError as exc:
        raise ValueError("ACA_REQUEST_TIMEOUT_SECONDS must be an integer") from exc

    if timeout_seconds <= 0:
        raise ValueError("ACA_REQUEST_TIMEOUT_SECONDS must be greater than zero")

    return Settings(
        subscription_id=_require_env("ACA_SUBSCRIPTION_ID"),
        resource_group_name=_require_env("ACA_RESOURCE_GROUP_NAME"),
        job_name=_require_env("ACA_JOB_NAME"),
        job_api_version=(os.getenv("ACA_JOB_API_VERSION", "2026-01-01").strip() or "2026-01-01"),
        request_timeout_seconds=timeout_seconds,
    )
