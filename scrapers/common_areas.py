"""Common-area / parking / amenity data from the legacy ViewProjectWebsite record.

Parking counts come from the building details; amenities come from
``CommonAreaItemsCharged`` (each item flagged whether it is charged to buyers),
stored as a JSON list in `amenities_json`.
"""

from __future__ import annotations

import logging
from typing import Any

from db.connection import upsert
from .utils import clean, to_int

logger = logging.getLogger("homelytics.common_areas")


def scrape_common_areas(vp: dict[str, Any], registration_no: str) -> int:
    buildings = vp.get("GetBuildingDetails") or []
    # Sum parking across buildings (most projects report it on the first one).
    covered = sum(to_int(b.get("NumberOfcloseparking")) or 0 for b in buildings) or None
    open_p = sum(to_int(b.get("NumberOfOpeningParking")) or 0 for b in buildings) or None

    amenities = [
        {"item": clean(item.get("Items")), "charged": bool(item.get("Checked"))}
        for item in (vp.get("CommonAreaItemsCharged") or [])
        if clean(item.get("Items"))
    ]

    if covered is None and open_p is None and not amenities:
        logger.info("%s: no common-area data", registration_no)
        return 0

    upsert(
        "common_areas",
        {
            "registration_no": registration_no,
            "parking_type": "general",
            "covered_parking": covered,
            "open_parking": open_p,
            "amenities_json": amenities or None,
        },
        conflict_cols=["registration_no", "parking_type"],
    )
    logger.info(
        "%s: common areas (covered=%s open=%s amenities=%d)",
        registration_no, covered, open_p, len(amenities),
    )
    return 1
