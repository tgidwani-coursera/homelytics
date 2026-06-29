"""Fetch one project's detail via GetProjectById and populate the child tables.

The RERA 2.0 detail endpoint returns project-level data only. From it we fill:
  - project_details (areas, building counts, completion date, status, raw_json)
  - promoters       (name, type, address, contact — via promoter.py)
  - documents       (RC certificate / order doc — via documents.py)

Apartment types, allottees, and common areas have no public API on RERA 2.0, so
their scrapers are invoked but currently log that no source exists and write
nothing (see those modules). Promoter past/ongoing history is likewise
unavailable, so promoter_projects_history stays empty.
"""

from __future__ import annotations

import logging
from typing import Any

from db.connection import upsert
from .api import ReraApiClient
from .documents import scrape_documents
from .promoter import scrape_promoter
from .utils import clean, to_date, to_float


def _store_project_details(project: dict[str, Any], registration_no: str) -> None:
    upsert(
        "project_details",
        {
            "registration_no": registration_no,
            # No public unit/booking counts on RERA 2.0 — left NULL.
            "total_units": None,
            "units_booked": None,
            "units_unsold": None,
            "completion_percentage": None,
            "expected_completion_date": to_date(clean(project.get("RevisedDateOfComplation"))),
            "land_area": clean(project.get("PhaseArea"))
                          or clean(project.get("Rectified_PhaseArea")),
            "raw_json": project,  # preserve the full payload (building counts, etc.)
        },
        conflict_cols=["registration_no"],
    )


def _enrich_project(project: dict[str, Any], registration_no: str) -> None:
    """Backfill category/address/tehsil onto the projects row from detail data."""
    updates = {
        "project_category": clean(project.get("ProjectCategory")),
        "address": clean(project.get("ProjectLocation")),
        # NOTE: do NOT set status from GetProjectById.StatusOfProject — it returns
        # "Rejected" for ~all registered projects. The meaningful workflow status
        # (e.g. "Application Approved" / "Objected") comes from GetProjects.AppStatus,
        # set by project_list and intentionally left untouched here. (SCRAPING_FIXES #1)
        # "DateofRegistration" ("28-04-2026") is cleaner than the list's APPROVEDON.
        "date_of_registration": to_date(clean(project.get("DateofRegistration"))),
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return
    updates["registration_no"] = registration_no
    upsert(
        "projects", updates,
        conflict_cols=["registration_no"],
        update_cols=[k for k in updates if k != "registration_no"],
    )


logger = logging.getLogger("homelytics.project_detail")


def scrape_detail(client: ReraApiClient, stub: dict[str, Any]) -> bool:
    """Scrape one project end-to-end from the API. Returns True on success.

    A failure in one sub-step is logged but does not abort the others; writes
    stay idempotent so partial data can be safely re-run.
    """
    registration_no = stub["registration_no"]
    encrypted_id = stub.get("encrypted_id")
    if not encrypted_id:
        logger.warning("No EncryptedProjectId for %s — skipping detail", registration_no)
        return False

    project = client.get_project_by_id(encrypted_id)
    if not project:
        return False

    # Granular tables (apartment types, allottees, common areas, promoter
    # history, full document list) live on the legacy ViewProjectWebsite record
    # and are handled by scrapers.view_project.enrich_from_view when a view_id
    # is available — they are not derivable from GetProjectById alone.
    steps = (
        ("project_details", lambda: _store_project_details(project, registration_no)),
        ("enrich_project", lambda: _enrich_project(project, registration_no)),
        ("promoter", lambda: scrape_promoter(project, registration_no)),
        ("documents", lambda: scrape_documents(project, registration_no)),
    )

    ok = True
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.exception("Sub-step '%s' failed for %s: %s", name, registration_no, exc)
    logger.info("Detail scraped for %s", registration_no)
    return ok
