"""Fetch the complaints register via GetComplaintDetailsWebsite into `complaints`.

One API call returns the whole register. Each complaint is linked to a project
by matching the respondent against projects.promoter_name (case-insensitive,
with a substring fallback), and secondarily by the complaint's ProjectName
against projects.project_name.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from db.connection import get_cursor, upsert_many
from .api import ReraApiClient
from .utils import clean, to_date

logger = logging.getLogger("homelytics.complaints")


def _load_indexes() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (promoter_index, project_name_index) as [(lowercased, reg_no)]."""
    with get_cursor(commit=False, dict_rows=True) as cur:
        cur.execute(
            "SELECT registration_no, promoter_name, project_name FROM projects"
        )
        rows = cur.fetchall()
    promoters = [
        (r["promoter_name"].lower(), r["registration_no"])
        for r in rows if r["promoter_name"]
    ]
    project_names = [
        (r["project_name"].lower(), r["registration_no"])
        for r in rows if r["project_name"]
    ]
    return promoters, project_names


def _match(value: Optional[str], index: list[tuple[str, str]]) -> Optional[str]:
    if not value:
        return None
    target = value.lower().strip()
    for name, reg_no in index:
        if name == target:
            return reg_no
    for name, reg_no in index:
        if name and (name in target or target in name):
            return reg_no
    return None


def scrape_complaints(
    client: ReraApiClient,
    limit: Optional[int] = None,
    complaint_type_id: int = 0,
) -> int:
    complaints = client.get_complaints(complaint_type_id=complaint_type_id)
    if limit:
        complaints = complaints[:limit]

    promoter_index, project_index = _load_indexes()
    logger.info(
        "Matching against %d promoters / %d project names",
        len(promoter_index), len(project_index),
    )

    batch: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for item in complaints:
        complainant = clean(item.get("CitizenName"))
        respondent = clean(item.get("RespondentName"))
        filed = to_date(str(item.get("createdon"))) if item.get("createdon") else None
        if not (complainant or respondent):
            continue

        key = (complainant, respondent, filed)
        if key in seen:  # satisfy the (complainant, respondent, filed_date) unique key
            continue
        seen.add(key)

        reg_no = _match(respondent, promoter_index) or _match(
            clean(item.get("ProjectName")), project_index
        )

        batch.append(
            {
                "registration_no": reg_no,
                "complainant": complainant,
                "respondent": respondent,
                "complaint_type": str(item.get("complaintTypeID") or ""),
                "status": str(item.get("StatusId") or "") or None,
                "filed_date": filed,
                "state": "rajasthan",
            }
        )

    if batch:
        upsert_many(
            "complaints", batch,
            conflict_cols=["complainant", "respondent", "filed_date"],
        )
    linked = sum(1 for b in batch if b["registration_no"])
    logger.info("Stored %d complaints (%d linked to a project)", len(batch), linked)
    return len(batch)
