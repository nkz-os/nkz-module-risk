"""Accumulation start of a parcel's crop cycle, from the platform rule (sync).

The SDK reads the parcel's crops and operations from the broker and resolves
the cycles here (``fetch_crop_timeline``); no core service in between.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from nkz_platform_sdk.crop_timeline import fetch_crop_timeline

logger = logging.getLogger(__name__)


def _run(coro):
    """Run a coroutine from sync code. The worker and the evaluate route (through
    run_in_executor) call this outside any event loop; if a caller ever does so
    inside a running loop, the coroutine runs in a helper thread instead of
    failing, blocking that caller until it finishes."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def accumulation_start(tenant_id: str, parcel_id: str) -> Optional[date]:
    """Degree-day accumulation start of the parcel's crop cycle; None if unavailable."""
    urn = parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
    try:
        timeline = _run(fetch_crop_timeline(tenant_id, urn))
        if timeline is None:
            return None
        return date.fromisoformat(timeline["accumulation"]["start"])
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("crop-cycle start unavailable for %s: %s", urn, e)
    return None
