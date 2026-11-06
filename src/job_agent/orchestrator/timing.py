"""Lightweight stage instrumentation for pipeline runs."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from job_agent.utils.logging import get_logger

log = get_logger(__name__)


class StageTimer:
    """Accumulate wall-clock time per named stage, in milliseconds.

    Usage::

        timer = StageTimer()
        with timer.stage("discovery"):
            ...
        stats["timings"] = timer.snapshot()
    """

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self._started = time.monotonic()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.timings[name] = round(self.timings.get(name, 0.0) + elapsed_ms, 1)
            log.debug("stage_timing", stage=name, elapsed_ms=round(elapsed_ms, 1))

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    def snapshot(self) -> dict[str, float]:
        return dict(self.timings)

    def total_ms(self) -> float:
        return round(self.elapsed_seconds() * 1000, 1)
