"""Enrich a project with the full legacy ViewProjectWebsite record.

The modern API (GetProjects / GetProjectById) covers project-level fields, but
the granular data — apartment types, unit-level allottees, common areas, the
full document list, and promoter project history — only exists on the legacy
``ViewProjectWebsite`` endpoint, keyed by a DES-encrypted ``view_id`` taken from
a ``/ViewProject?id=...&type=U`` URL.

Given a (registration_no, view_id), this fills all five of those tables.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from db.connection import get_cursor, upsert
from .allottees import scrape_allottees
from .apartment_types import scrape_apartment_types
from .api import ReraApiClient
from .common_areas import scrape_common_areas
from .documents import scrape_documents_full
from .promoter import scrape_promoter_history
from .utils import clean

logger = logging.getLogger("homelytics.view_project")


def _resolve_promoter_id(vp: dict[str, Any], registration_no: str) -> Optional[int]:
    """Find the promoter row for this project, creating one if needed."""
    with get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT promoter_id FROM promoters WHERE registration_no = %s LIMIT 1",
            (registration_no,),
        )
        row = cur.fetchone()
    if row:
        return row[0]

    # No promoter yet (rich flow run standalone) — create from the legacy record.
    pd = vp.get("PromoterDetails") or {}
    name = clean(pd.get("OrgName")) or clean(
        " ".join(filter(None, [pd.get("FirstName"), pd.get("LastName")]))
    )
    if not name:
        return None
    return upsert(
        "promoters",
        {
            "registration_no": registration_no,
            "name": name,
            "company_type": clean(pd.get("OrgType")),
            "state": "rajasthan",
        },
        conflict_cols=["registration_no", "name"],
        returning="promoter_id",
    )


def enrich_from_view(client: ReraApiClient, registration_no: str, view_id: str) -> bool:
    """Fetch the legacy record and populate the granular tables."""
    vp = client.get_project_full(view_id)
    if not vp:
        logger.warning("No legacy record for %s (view_id=%s)", registration_no, view_id)
        return False

    promoter_id = _resolve_promoter_id(vp, registration_no)

    steps = (
        ("apartment_types", lambda: scrape_apartment_types(vp, registration_no)),
        ("allottees", lambda: scrape_allottees(vp, registration_no)),
        ("common_areas", lambda: scrape_common_areas(vp, registration_no)),
        ("documents", lambda: scrape_documents_full(vp, registration_no)),
        ("promoter_history", lambda: scrape_promoter_history(vp, promoter_id)),
    )
    ok = True
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.exception("Enrich step '%s' failed for %s: %s", name, registration_no, exc)
    logger.info("Enriched %s from legacy record", registration_no)
    return ok
