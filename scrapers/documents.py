"""Store document links from a GetProjectById payload into `documents`.

RERA 2.0's detail object exposes the registration (RC) certificate path and an
optional order document — there is no broader public document-list endpoint, so
those are the documents we capture. Relative server paths (``~/...``, ``../...``)
are resolved against RERA_BASE_URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from urllib.parse import urljoin

from db.connection import upsert_many
from .utils import clean, to_date

logger = logging.getLogger("homelytics.documents")

# (payload field, doc_type) pairs we know how to extract.
DOC_FIELDS = [
    ("RcCertificatePath", "RC Certificate"),
    ("UploadedCertificatePath", "RC Certificate"),
    ("OrderDoc", "Order"),
]


def _absolute(path: str, base_url: str) -> str:
    cleaned = path.lstrip("~").lstrip()
    # "~/Content/..." and "../Content/..." both map to the site root.
    cleaned = cleaned.replace("../", "/").lstrip("/")
    return urljoin(base_url + "/", cleaned)


def scrape_documents(
    project: dict[str, Any],
    registration_no: str,
    base_url: Optional[str] = None,
) -> int:
    base_url = (base_url or os.getenv("RERA_BASE_URL", "https://rera.rajasthan.gov.in")).rstrip("/")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field, doc_type in DOC_FIELDS:
        raw = clean(project.get(field))
        if not raw:
            continue
        doc_url = _absolute(raw, base_url)
        if doc_url in seen:
            continue
        seen.add(doc_url)
        rows.append(
            {
                "registration_no": registration_no,
                "doc_type": doc_type,
                "doc_name": doc_url.rsplit("/", 1)[-1],
                "doc_url": doc_url,
                "uploaded_at": None,
            }
        )

    if rows:
        upsert_many("documents", rows, conflict_cols=["registration_no", "doc_url"])
    logger.info("%s: %d documents", registration_no, len(rows))
    return len(rows)


def _doc_date(item: dict[str, Any]):
    """Best-effort upload date: top-level CreatedOn, else a nested copy."""
    d = to_date(clean(item.get("CreatedOn")))
    if d:
        return d
    for nested in item.get("DocLstForMultipleUpdationsFromSpecialMod") or []:
        d = to_date(clean(nested.get("CreatedOn")))
        if d:
            return d
    return None


def scrape_documents_full(
    vp: dict[str, Any],
    registration_no: str,
    legacy_base: Optional[str] = None,
) -> int:
    """Store the full document list from a ViewProjectWebsite record.

    Document URLs are relative to the legacy host (``reraapp``); empty URLs
    (required-but-not-uploaded docs) are skipped.
    """
    legacy_base = (legacy_base or os.getenv("RERA_LEGACY_URL",
                   "https://reraapp.rajasthan.gov.in")).rstrip("/")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in vp.get("GetDocumentsList") or []:
        raw = clean(item.get("DocumentUrl"))
        if not raw:
            continue
        doc_url = _absolute(raw, legacy_base)
        if doc_url in seen:
            continue
        seen.add(doc_url)
        rows.append(
            {
                "registration_no": registration_no,
                "doc_type": clean(item.get("ApplicationDocumentName"))
                             or clean(item.get("MasterType")),
                "doc_name": clean(item.get("DocumentName")) or doc_url.rsplit("/", 1)[-1],
                "doc_url": doc_url,
                "uploaded_at": _doc_date(item),
            }
        )

    if rows:
        upsert_many("documents", rows, conflict_cols=["registration_no", "doc_url"])
    logger.info("%s: %d documents (full list)", registration_no, len(rows))
    return len(rows)
