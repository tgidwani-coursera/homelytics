"""Unit-level allottee / booking data from the legacy ViewProjectWebsite record.

Source: ``GetApartmentAllotteeDetailsList`` — one entry per physical unit, with
its block, carpet area and Sold/Unsold status.

Privacy note: the source rows include allottee names (PII). The `allottees`
schema intentionally has no name column, so we store only the unit, block,
carpet area and booking status — not who booked it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from db.connection import upsert_many
from .utils import clean, to_date, to_float

logger = logging.getLogger("homelytics.allottees")


def _status(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    low = raw.strip().lower()
    if low in ("sold", "booked", "allotted", "alloted"):
        return "sold"
    if low in ("unsold", "available", "unbooked"):
        return "unsold"
    # Other statuses (mortgage, not-yet-approved, ...) kept but case-normalised
    # so trivial casing variants don't split into separate values.
    return low


def scrape_allottees(vp: dict[str, Any], registration_no: str) -> int:
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    for unit in vp.get("GetApartmentAllotteeDetailsList") or []:
        unit_no = clean(unit.get("Units"))
        block = clean(unit.get("Block"))
        if not unit_no:
            continue
        # floor_no isn't a discrete field on this portal; the block groups units.
        key = (block, unit_no)
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "registration_no": registration_no,
                "floor_no": block,
                "unit_no": unit_no,
                "carpet_area": to_float(unit.get("CarpetArea")),
                "booking_status": _status(unit.get("BookingStatus")),
                "booking_date": to_date(clean(unit.get("DateAFS"))),
            }
        )

    if rows:
        upsert_many(
            "allottees", rows,
            conflict_cols=["registration_no", "floor_no", "unit_no"],
        )
    sold = sum(1 for r in rows if r["booking_status"] == "sold")
    logger.info("%s: %d allottee units (%d sold)", registration_no, len(rows), sold)
    return len(rows)
