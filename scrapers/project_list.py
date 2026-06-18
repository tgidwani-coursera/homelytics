"""Fetch the project list from the GetProjects API and store it in `projects`.

One API call returns every registered project (~5k). We filter by district
client-side (the API exposes DistrictName on each row), upsert each project, and
yield a stub carrying the EncryptedProjectId the detail scraper needs.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

from db.connection import get_cursor, upsert
from .api import ReraApiClient
from .utils import clean, to_date

logger = logging.getLogger("homelytics.project_list")


def _existing_registration_nos() -> set[str]:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT registration_no FROM projects")
        return {r[0] for r in cur.fetchall()}


def _date(item: dict[str, Any], *keys: str):
    for k in keys:
        d = to_date(str(item.get(k))) if item.get(k) is not None else None
        if d:
            return d
    return None


def scrape_list(
    client: ReraApiClient,
    district: Optional[str] = None,
    limit: Optional[int] = None,
    registration_no: Optional[str] = None,
    refresh_existing: bool = False,
) -> Iterator[dict[str, Any]]:
    """Upsert projects (optionally filtered by district) and yield detail stubs.

    When ``registration_no`` is given, the district filter is ignored and only
    that project is fetched (server-side filter).

    When ``refresh_existing`` is True, only projects already in the local DB are
    processed — the bi-weekly refresh scope, so trend history accrues for exactly
    the projects being tracked.
    """
    projects = client.get_projects(application_status="3", registration_no=registration_no)

    if refresh_existing:
        existing = _existing_registration_nos()
        projects = [p for p in projects if clean(p.get("REGISTRATIONNO")) in existing]
        logger.info("Refreshing %d projects already in the DB", len(projects))
    elif district and not registration_no:
        projects = [
            p for p in projects
            if (p.get("DistrictName") or "").strip().lower() == district.strip().lower()
        ]
        logger.info("Filtered to %d projects in district '%s'", len(projects), district)

    yielded = 0
    for item in projects:
        reg_no = clean(item.get("REGISTRATIONNO")) or clean(item.get("ApplicationNo"))
        encrypted_id = item.get("EncryptedProjectId")
        if not reg_no:
            logger.warning("Skipping project with no registration/application no: %s",
                           item.get("ProjectName"))
            continue

        upsert(
            "projects",
            {
                "registration_no": reg_no,
                "project_name": clean(item.get("ProjectName")),
                "promoter_name": clean(item.get("PromoterName")) or clean(item.get("ORGNAME")),
                "district": clean(item.get("DistrictName")),
                "project_type": clean(item.get("ProjectTypeName")),
                "status": clean(item.get("AppStatus")) or "registered",
                "date_of_registration": _date(item, "APPROVEDON"),
                "state": "rajasthan",
            },
            conflict_cols=["registration_no"],
            # tehsil / project_category / address come from the detail call.
            update_cols=[
                "project_name", "promoter_name", "district",
                "project_type", "status", "date_of_registration", "scraped_at",
            ],
        )

        yielded += 1
        yield {
            "registration_no": reg_no,
            "encrypted_id": encrypted_id,
            "project_name": clean(item.get("ProjectName")),
            "promoter_name": clean(item.get("PromoterName")),
            "district": clean(item.get("DistrictName")),
        }

        if limit and yielded >= limit:
            logger.info("Reached --limit of %d projects", limit)
            return
