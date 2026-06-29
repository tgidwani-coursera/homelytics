"""Store promoter details from a GetProjectById payload into `promoters`.

The RERA 2.0 detail object carries the promoter's name, type, address, and
contact details. Past/ongoing project history is NOT exposed by the public API,
so `promoter_projects_history` is left empty (and past/ongoing counts are NULL).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from db.connection import upsert, upsert_many
from .utils import clean

logger = logging.getLogger("homelytics.promoter")


def _history_status(pstatus: str | None) -> str:
    """Map the portal's PStatus to the schema's past/ongoing/registered."""
    s = (pstatus or "").strip().lower()
    if "complet" in s:
        return "past"
    if "progress" in s or "ongoing" in s:
        return "ongoing"
    return "registered"


def scrape_promoter_history(vp: dict[str, Any], promoter_id: int) -> int:
    """Store the promoter's other projects from a ViewProjectWebsite record."""
    if promoter_id is None:
        return 0
    project_list = (vp.get("PromoterDetails") or {}).get("_ProjectList") or []

    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for proj in project_list:
        name = clean(proj.get("Name"))
        if not name:
            continue
        reg = clean(proj.get("RegistrationNo"))
        key = (name, reg)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "promoter_id": promoter_id,
                "project_name": name,
                "registration_no": reg,
                "location": clean(proj.get("DistrictName")),
                "status": _history_status(proj.get("PStatus")),
            }
        )

    if rows:
        upsert_many(
            "promoter_projects_history", rows,
            conflict_cols=["promoter_id", "project_name", "registration_no"],
        )
    logger.info("promoter_id=%s: %d history rows", promoter_id, len(rows))
    return len(rows)


def scrape_promoter(project: dict[str, Any], registration_no: str) -> Optional[int]:
    """Upsert the promoter row. Returns promoter_id (or None if no name)."""
    name = clean(project.get("PromoterName")) or clean(project.get("ORGNAME"))
    if not name:
        logger.warning("No promoter name in detail for %s", registration_no)
        return None

    # Address comes through as "DetailsofPromoter" on the detail payload.
    address = clean(project.get("DetailsofPromoter")) or clean(project.get("PromoterAddress"))

    promoter_id = upsert(
        "promoters",
        {
            "registration_no": registration_no,
            "name": name,
            "company_type": clean(project.get("PromoterType")),
            "address": address,
            "past_projects_count": None,     # not exposed by the public API
            "ongoing_projects_count": None,  # not exposed by the public API
            "state": "rajasthan",
        },
        conflict_cols=["registration_no", "name"],
        returning="promoter_id",
    )
    logger.info("Promoter '%s' (id=%s) for %s", name, promoter_id, registration_no)
    return promoter_id
