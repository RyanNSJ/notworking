"""Background jobs, run in-process by the app (one process, so no scheduler library).

Every 5 minutes: the detector (M4) and salt pruning (D21).
"""

import asyncio
import datetime as dt
import logging

from agentdown import detector, store
from agentdown.service import AppState

INTERVAL = dt.timedelta(minutes=5)
log = logging.getLogger(__name__)


def tick(state: AppState) -> int:
    """One detector run. Returns the number of status transitions."""
    now = state.clock.now()
    with state.engine.begin() as conn:
        store.delete_old_salts(conn, now)
        return detector.run(conn, now)


async def run_forever(state: AppState) -> None:
    while True:
        try:
            await asyncio.to_thread(tick, state)
        except Exception:  # keep the loop alive; the next run retries
            log.exception("detector run failed")
        await asyncio.sleep(INTERVAL.total_seconds())
