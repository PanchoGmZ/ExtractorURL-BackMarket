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
_process_start = None


class RunBudget:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started

    def remaining(self, cutoff=TOTAL_SECONDS):
        return max(0.0, self.started + cutoff - time.monotonic())

    def elapsed(self):
        return time.monotonic() - self.started


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

    def expire():
        os.write(2, b'[TOPE_PROCESO] RUN_TIME_LIMIT | limite=55 s | salida=1\n')
        os._exit(1)

    timer = threading.Timer(RunBudget(_process_start).remaining(), expire)
    timer.daemon = True
    timer.start()


def process_budget():
    return RunBudget(_process_start)


async def bounded(awaitable, seconds):
    """Bound cooperative SDK/browser awaits; process watchdog is the final guard."""
    return await asyncio.wait_for(awaitable, max(0.001, seconds))
