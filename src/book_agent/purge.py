"""Deleting expired profiles while the app runs (R-011).

Reads already hide an expired profile, its covers and its cover jobs (the stores check
`expires_at`), so purging is about keeping the promise that the data is gone after a few
days (D-043), not about correctness. It runs once at startup and then every interval;
deleting a profile deletes its covers and jobs (ON DELETE CASCADE).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from book_agent.ports import ProfileStore

logger = logging.getLogger(__name__)

DEFAULT_PURGE_INTERVAL_S = 3600.0


async def purge_once(store: ProfileStore, now: datetime) -> int:
    """Delete expired profiles now; returns how many. Logged as a count only."""
    purged = await store.purge_expired(now)
    if purged:
        logger.info("purged %d expired profiles", purged)
    return purged


async def purge_periodically(
    store: ProfileStore,
    *,
    interval_s: float = DEFAULT_PURGE_INTERVAL_S,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Purge every `interval_s` until cancelled. A failed purge (the database briefly
    unreachable) is logged and tried again at the next interval."""
    while True:
        await sleep(interval_s)
        try:
            await purge_once(store, clock())
        except Exception as e:
            logger.warning("purging expired profiles failed: %s", type(e).__name__)
