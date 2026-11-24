"""Automated scheduling: run a task every ``interval`` seconds."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


def run_forever(
    task: Callable[[], object],
    interval: float,
    max_cycles: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run ``task`` repeatedly. Errors inside a cycle are logged, not fatal.

    Returns the number of completed cycles. Stops after ``max_cycles`` if given;
    otherwise runs until interrupted (Ctrl+C is handled by the caller).
    """
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        started = time.monotonic()
        try:
            task()
        except Exception:
            log.exception("Cycle failed; will retry next interval")
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        elapsed = time.monotonic() - started
        sleep(max(0.0, interval - elapsed))
    return cycles
