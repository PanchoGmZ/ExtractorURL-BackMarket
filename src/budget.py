"""Shared wall-clock deadlines. No browser, SDK or network imports here."""
import asyncio
import os
import threading
import time
from datetime import datetime

TOTAL_SECONDS = 55
ACQUISITION_SECONDS = 48
WORK_SECONDS = 52
PERSIST_SECONDS = 53
BROWSER_CLOSE_SECONDS = 2
DIAGNOSTIC_DEFAULT_SECONDS = 180
DIAGNOSTIC_RESERVE_SECONDS = 20
_process_start = None
_watchdog_timer = None
_watchdog_lock = threading.Lock()


class RunBudget:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started

    def remaining(self, cutoff=TOTAL_SECONDS):
        return max(0.0, self.started + cutoff - time.monotonic())

    def elapsed(self):
        return time.monotonic() - self.started


def _arm_watchdog(seconds):
    global _watchdog_timer
    if _process_start is None:
        return

    def expire():
        os.write(2, f'[TOPE_PROCESO] RUN_TIME_LIMIT | limite={seconds:g} s | salida=1\n'.encode())
        os._exit(1)

    with _watchdog_lock:
        if _watchdog_timer is not None:
            _watchdog_timer.cancel()
        timer = threading.Timer(RunBudget(_process_start).remaining(seconds), expire)
        timer.daemon = True
        _watchdog_timer = timer
        timer.start()


def configure_process_watchdog(seconds):
    """Set the process-age safety cap after the Actor input selects its mode."""
    _arm_watchdog(seconds)


def arm_process_watchdog():
    """Arm only from a CLI entry point, before importing the SDK/crawler.

    The final guard also covers synchronous parsing and stuck SDK cleanup. Normal
    cancellation/persistence runs earlier; this guard cannot promise final logs.
    """
    global _process_start
    if _process_start is not None:
        return
    import psutil
    monotonic_now, wall_now = time.monotonic(), time.time()
    _process_start = monotonic_now - max(0, wall_now - psutil.Process().create_time())
    if os.environ.get('APIFY_IS_AT_HOME') == '1':
        try:
            platform_start = datetime.fromisoformat(os.environ.get('ACTOR_STARTED_AT', '').replace('Z', '+00:00'))
            if platform_start.tzinfo is not None:
                _process_start = min(_process_start, monotonic_now - max(0, wall_now - platform_start.timestamp()))
        except ValueError:
            pass

    # Input is fetched only after Actor initialization. Keep a bounded bootstrap
    # guard, then configure the exact 55s/diagnostic cap when that input is read.
    _arm_watchdog(DIAGNOSTIC_DEFAULT_SECONDS)


def process_budget():
    return RunBudget(_process_start)


async def bounded(awaitable, seconds):
    """Bound cooperative SDK/browser awaits; process watchdog is the final guard."""
    return await asyncio.wait_for(awaitable, max(0.001, seconds))
