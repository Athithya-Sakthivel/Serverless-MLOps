"""Timing utilities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def elapsed_seconds(start: float, end: float | None = None) -> float:
    stop = perf_counter() if end is None else end
    return max(0.0, stop - start)


@contextmanager
def timed_block() -> Iterator[float]:
    start = perf_counter()
    yield start
