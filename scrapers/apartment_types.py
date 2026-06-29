"""Apartment-type breakdown from the legacy ViewProjectWebsite record.

Source: each building's ``GetAppartmentDetails`` list. One row per
apartment type (per block), with carpet area and total/booked/unsold counts.

Carpet areas on this portal are in **square metres** (despite the column name
``carpet_area_sqft`` kept from the original spec); the raw value is stored as-is.
"""

from __future__ import annotations

import logging
from typing import Any

from db.connection import upsert_many
from .utils import clean, to_float, to_int

logger = logging.getLogger("homelytics.apartment_types")


def scrape_apartment_types(vp: dict[str, Any], registration_no: str) -> int:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for building in vp.get("GetBuildingDetails") or []:
        for ap in building.get("GetAppartmentDetails") or []:
            ap_type = clean(ap.get("ApartmentType"))
            if not ap_type:
                continue
            block = clean(ap.get("BulidingBlockText"))
            # Disambiguate identical type names across blocks for the unique key.
            type_name = f"{ap_type} / {block}" if block else ap_type
            if type_name in seen:
                continue
            seen.add(type_name)

            total = to_int(ap.get("NumberOfApartments"))
            booked = to_int(ap.get("NumberOfApartmentsBooked"))
            unsold = (total - booked) if (total is not None and booked is not None) else None

            rows.append(
                {
                    "registration_no": registration_no,
                    "type_name": type_name,
                    "carpet_area_sqft": to_float(ap.get("CarpetArea")),
                    "bathrooms": None,   # not provided by the portal
                    "balconies": None,   # only balcony *area* is provided, not count
                    "total_count": total,
                    "sold_count": booked,
                    "unsold_count": unsold,
                }
            )

    if rows:
        upsert_many("apartment_types", rows, conflict_cols=["registration_no", "type_name"])
    logger.info("%s: %d apartment types", registration_no, len(rows))
    return len(rows)
