"""Background jobs, run in-process by the app (one process, so no scheduler library).

Every 5 minutes: the detector (M4), salt pruning (D21) and hourly check pruning.
"""

import asyncio
import datetime as dt
import logging

from agentdown import detector, store
from agentdown.service import AppState

INTERVAL = dt.timedelta(minutes=5)
STALE_AFTER = 3 * INTERVAL  # /healthz fails after this long without a successful run
log = logging.getLogger(__name__)

# When the loop started and last succeeded (one process, so module state is enough).
started_at: dt.datetime | None = None
last_success: dt.datetime | None = None


def stale(now: dt.datetime) -> bool:
    """True if the loop is running but hasn't succeeded for STALE_AFTER."""
    since = last_success or started_at
    return since is not None and now - since > STALE_AFTER


def tick(state: AppState) -> int:
    """One detector run. Returns the number of status transitions."""
    now = state.clock.now()
    with state.engine.begin() as conn:
        store.delete_old_salts(conn, now)
        store.delete_old_checks(conn, now)
        return detector.run(conn, now)


async def run_forever(state: AppState) -> None:
    global started_at, last_success
    started_at = state.clock.now()
    while True:
        try:
            await asyncio.to_thread(tick, state)
            last_success = state.clock.now()
        except Exception:  # keep the loop alive; the next run retries
            log.exception("detector run failed")
        await asyncio.sleep(INTERVAL.total_seconds())
