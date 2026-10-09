"""Accumulation start of a parcel's crop cycle, from the platform (sync)."""
import logging
from datetime import date
from typing import Optional

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)


def accumulation_start(tenant_id: str, parcel_id: str) -> Optional[date]:
    """Degree-day accumulation start resolved by entity-manager; None if unavailable."""
    settings = get_settings()
    urn = parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
    try:
        resp = requests.get(
            f"{settings.entity_manager_url.rstrip('/')}/api/internal/parcels/{urn}/crop-cycles",
            params={"tenant_id": tenant_id},
            headers={"X-Internal-Service-Secret": settings.internal_service_secret},
            timeout=8,
        )
        if resp.status_code == 200:
            return date.fromisoformat(resp.json()["accumulation"]["start"])
        logger.warning("crop-cycle start for %s: HTTP %s", urn, resp.status_code)
    except (requests.RequestException, KeyError, TypeError, ValueError) as e:
        logger.warning("crop-cycle start unavailable for %s: %s", urn, e)
    return None
