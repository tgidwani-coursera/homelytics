"""Homelytics dashboard — FastAPI backend.

Read-only API over the existing PostgreSQL DB (same .env credentials as the
scraper). Serves the single-file SPA frontend as static files.

Run from the repo root so `db.connection` is importable:
    ./run.sh           (or)  uvicorn dashboard.app:app --reload
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()  # DB creds from .env (DB_HOST/DB_NAME/DB_USER/DB_PASSWORD)

from db.connection import get_cursor  # noqa: E402 — needs env loaded first

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="Homelytics", description="RERA project transparency for home buyers")


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_cursor(commit=False, dict_rows=True) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    with get_cursor(commit=False, dict_rows=True) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Document classification (for the home-buyer "checklist")
# --------------------------------------------------------------------------- #

# label -> keywords matched (case-insensitive) against doc_type + doc_name.
MUST_HAVE = {
    "Title Document": ["title"],
    "Registration Certificate": ["rc certificate", "registration certificate", "registration cert"],
    "Building Sanction Plan": ["sanction", "approved site plan", "approved building", "building plan"],
    "Commencement Certificate": ["commencement"],
    "Land Use Certificate": ["land use", "land-use", "change of land", "clu"],
}
GOOD_TO_HAVE = {
    "Amenities Plan": ["amenit"],
    "Water Supply": ["water supply", "water connection"],
    "Electrification Plan": ["electrif", "electric"],
    "Common Area Documents": ["common area"],
}
# Subset flagged as "critical" per the API spec.
CRITICAL_LABELS = {"Title Document", "Registration Certificate", "Building Sanction Plan"}


def _doc_text(doc: dict[str, Any]) -> str:
    return f"{doc.get('doc_type') or ''} {doc.get('doc_name') or ''}".lower()


def _matches(doc: dict[str, Any], keywords: list[str]) -> bool:
    text = _doc_text(doc)
    return any(kw in text for kw in keywords)


def classify_documents(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Tag each document and build a must-have / good-to-have checklist."""
    for doc in docs:
        label = next((lbl for lbl, kw in {**MUST_HAVE, **GOOD_TO_HAVE}.items()
                      if _matches(doc, kw)), None)
        doc["category"] = (
            "must-have" if label in MUST_HAVE
            else "good-to-have" if label in GOOD_TO_HAVE
            else "other"
        )
        doc["matched_label"] = label
        doc["is_critical"] = label in CRITICAL_LABELS

    def checklist(groups: dict[str, list[str]]) -> list[dict[str, Any]]:
        items = []
        for label, keywords in groups.items():
            match = next((d for d in docs if _matches(d, keywords)), None)
            items.append({
                "label": label,
                "present": match is not None,
                "doc_url": match.get("doc_url") if match else None,
            })
        return items

    return {
        "must_have": checklist(MUST_HAVE),
        "good_to_have": checklist(GOOD_TO_HAVE),
    }


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/stats")
def stats() -> dict[str, Any]:
    row = fetch_one("""
        SELECT
            (SELECT count(*) FROM projects)                       AS total_projects,
            (SELECT count(DISTINCT promoter_name) FROM projects
               WHERE promoter_name IS NOT NULL)                   AS total_builders,
            (SELECT count(DISTINCT district) FROM projects
               WHERE district IS NOT NULL)                        AS districts_covered,
            (SELECT count(*) FROM complaints)                     AS total_complaints
    """)
    # Distinct filter values for the homepage dropdowns.
    districts = [r["district"] for r in fetch_all(
        "SELECT DISTINCT district FROM projects WHERE district IS NOT NULL ORDER BY 1")]
    types = [r["project_type"] for r in fetch_all(
        "SELECT DISTINCT project_type FROM projects WHERE project_type IS NOT NULL ORDER BY 1")]
    statuses = [r["status"] for r in fetch_all(
        "SELECT DISTINCT status FROM projects WHERE status IS NOT NULL ORDER BY 1")]
    completion_years = [r["y"] for r in fetch_all(
        "SELECT DISTINCT EXTRACT(YEAR FROM expected_completion_date)::int AS y "
        "FROM project_details WHERE expected_completion_date IS NOT NULL ORDER BY y")]
    row["filters"] = {
        "districts": districts, "types": types,
        "statuses": statuses, "completion_years": completion_years,
    }
    return row


@app.get("/api/projects")
def projects(
    district: Optional[str] = None,
    type: Optional[str] = Query(None),
    status: Optional[str] = None,
    completion_year: Optional[int] = None,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses, params = ["1=1"], []
    if district:
        clauses.append("p.district = %s"); params.append(district)
    if type:
        clauses.append("p.project_type = %s"); params.append(type)
    if status:
        clauses.append("p.status = %s"); params.append(status)
    if completion_year:
        clauses.append("EXTRACT(YEAR FROM d.expected_completion_date)::int = %s")
        params.append(completion_year)
    if search:
        clauses.append(
            "(p.project_name ILIKE %s OR p.promoter_name ILIKE %s OR p.registration_no ILIKE %s)")
        params += [f"%{search}%"] * 3

    sql = f"""
        SELECT p.registration_no, p.project_name, p.promoter_name, p.district,
               p.project_type, p.status, p.date_of_registration,
               d.expected_completion_date, d.land_area
        FROM projects p
        LEFT JOIN project_details d USING (registration_no)
        WHERE {' AND '.join(clauses)}
        ORDER BY p.date_of_registration DESC NULLS LAST, p.project_name
    """
    return fetch_all(sql, tuple(params))


@app.get("/api/projects/{registration_no:path}")
def project_detail(registration_no: str) -> dict[str, Any]:
    project = fetch_one("""
        SELECT p.*, d.total_units, d.units_booked, d.units_unsold,
               d.completion_percentage, d.expected_completion_date,
               d.land_area, d.raw_json
        FROM projects p
        LEFT JOIN project_details d USING (registration_no)
        WHERE p.registration_no = %s
    """, (registration_no,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    promoter = fetch_one(
        "SELECT name, company_type, address, past_projects_count, "
        "ongoing_projects_count FROM promoters WHERE registration_no = %s LIMIT 1",
        (registration_no,))

    apartment_types = fetch_all("""
        SELECT type_name, carpet_area_sqft, total_count, sold_count, unsold_count
        FROM apartment_types WHERE registration_no = %s
        ORDER BY total_count DESC NULLS LAST
    """, (registration_no,))

    # Allottee summary: counts per booking status.
    rows = fetch_all(
        "SELECT booking_status, count(*) AS n FROM allottees "
        "WHERE registration_no = %s GROUP BY booking_status", (registration_no,))
    by_status = {r["booking_status"]: r["n"] for r in rows}
    total = sum(by_status.values())
    sold = by_status.get("sold", 0)
    allottee_summary = {
        "total": total,
        "sold": sold,
        "unsold": by_status.get("unsold", 0),
        "mortgage": by_status.get("mortgage", 0),
        "other": total - sold - by_status.get("unsold", 0) - by_status.get("mortgage", 0),
        "sold_pct": round(100 * sold / total, 1) if total else 0,
    }

    documents = fetch_all("""
        SELECT doc_type, doc_name, doc_url, uploaded_at
        FROM documents WHERE registration_no = %s ORDER BY doc_type
    """, (registration_no,))
    checklist = classify_documents(documents)

    common = fetch_one(
        "SELECT parking_type, covered_parking, open_parking, amenities_json "
        "FROM common_areas WHERE registration_no = %s LIMIT 1", (registration_no,))

    return {
        "project": project,
        "promoter": promoter,
        "apartment_types": apartment_types,
        "allottee_summary": allottee_summary,
        "documents": documents,
        "document_checklist": checklist,
        "common_areas": common,
    }


@app.get("/api/builders/{promoter_name:path}")
def builder_profile(promoter_name: str) -> dict[str, Any]:
    proj = fetch_all("""
        SELECT registration_no, project_name, district, project_type, status,
               date_of_registration
        FROM projects WHERE promoter_name = %s
        ORDER BY date_of_registration DESC NULLS LAST
    """, (promoter_name,))
    if not proj:
        raise HTTPException(status_code=404, detail="Builder not found")

    # Past/ongoing counts from history (distinct projects across the builder's rows).
    counts = fetch_all("""
        SELECT h.status, count(DISTINCT coalesce(h.registration_no, h.project_name)) AS n
        FROM promoter_projects_history h
        JOIN promoters p USING (promoter_id)
        WHERE p.name = %s
        GROUP BY h.status
    """, (promoter_name,))
    by = {c["status"]: c["n"] for c in counts}

    # The builder's self-declared project history (independent of what we scraped).
    # Flag each with whether we have it in our own projects table (so it can link).
    declared_history = fetch_all("""
        SELECT DISTINCT h.project_name, h.registration_no, h.status,
               (pr.registration_no IS NOT NULL) AS in_db
        FROM promoter_projects_history h
        JOIN promoters p USING (promoter_id)
        LEFT JOIN projects pr ON pr.registration_no = h.registration_no
        WHERE p.name = %s
        ORDER BY h.status, h.project_name
    """, (promoter_name,))

    complaints = fetch_one("""
        SELECT count(*) AS n FROM complaints
        WHERE lower(respondent) = lower(%s)
           OR respondent ILIKE %s
           OR %s ILIKE ('%%' || respondent || '%%')
    """, (promoter_name, f"%{promoter_name}%", promoter_name))

    return {
        "name": promoter_name,
        "company_type": (fetch_one(
            "SELECT company_type FROM promoters WHERE name = %s LIMIT 1",
            (promoter_name,)) or {}).get("company_type"),
        "projects": proj,
        "project_count": len(proj),
        "declared_history": declared_history,
        "past_count": by.get("past", 0),
        "ongoing_count": by.get("ongoing", 0),
        "registered_count": by.get("registered", 0),
        "total_complaints": (complaints or {}).get("n", 0),
    }


@app.get("/api/complaints")
def complaints(respondent: Optional[str] = None) -> list[dict[str, Any]]:
    if respondent:
        return fetch_all("""
            SELECT complainant, respondent, complaint_type, status, filed_date
            FROM complaints WHERE respondent ILIKE %s ORDER BY id DESC
        """, (f"%{respondent}%",))
    return fetch_all("""
        SELECT complainant, respondent, complaint_type, status, filed_date
        FROM complaints ORDER BY id DESC
    """)


@app.get("/api/trend")
def trend(registration_no: str) -> list[dict[str, Any]]:
    """Inventory snapshot series for a project (oldest -> newest)."""
    return fetch_all("""
        SELECT snapshot_date, total_units, units_booked, units_unsold, units_mortgage
        FROM inventory_snapshots
        WHERE registration_no = %s
        ORDER BY snapshot_date
    """, (registration_no,))


# --------------------------------------------------------------------------- #
# Static frontend (mounted last so /api/* takes precedence)
# --------------------------------------------------------------------------- #

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
