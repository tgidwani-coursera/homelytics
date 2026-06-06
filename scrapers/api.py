"""Client for the Rajasthan RERA 2.0 JSON API (reraapi.rajasthan.gov.in).

The public portal is a React SPA backed by this JSON API. Requests are
authenticated with a static client-side key shipped to every browser
(``x-api-key``) plus a Referer header. We call the API directly through
Playwright's APIRequestContext — far more robust than scraping rendered HTML.

Discovered endpoints (all POST, JSON body, no per-user auth):
  - Home/GetProjects                 -> every registered project
  - Home/GetProjectById              -> one project's detail
  - Home/GetComplaintDetailsWebsite  -> the complaints register

NOTE: ``RERA_API_KEY`` is a constant the site ships publicly; if the portal
rotates it, update the value in ``.env`` (capture it from the browser's network
tab on any list page).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from playwright.sync_api import APIResponse, sync_playwright

from .utils import rate_limit, with_retries

logger = logging.getLogger("homelytics.api")

GET_PROJECTS = "/api/web/Home/GetProjects"
GET_PROJECT_BY_ID = "/api/web/Home/GetProjectById"
GET_COMPLAINTS = "/api/web/Home/GetComplaintDetailsWebsite"
# Legacy "RERA 1.0" endpoints (different host).
# ProjectDtlsWebsite maps a modern EncryptedProjectId -> the legacy DES view_id.
# ViewProjectWebsite then returns the full record (allottees, apartment types,
# documents, common areas, promoter history) keyed by that view_id.
PROJECT_DTLS = "/HomeWebsite/ProjectDtlsWebsite"
VIEW_PROJECT = "/HomeWebsite/ViewProjectWebsite"


class ReraApiClient:
    """Thin wrapper around a Playwright APIRequestContext with retry + rate limit."""

    def __init__(self, request_context: Any, legacy_url: str = "") -> None:
        self._ctx = request_context
        self._legacy_url = legacy_url.rstrip("/")

    @with_retries
    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        logger.debug("POST %s %s", path, payload)
        resp: APIResponse = self._ctx.post(path, data=payload)
        if not resp.ok:
            raise RuntimeError(f"{path} -> HTTP {resp.status}")
        body = resp.json()
        rate_limit()
        # The API wraps results as {State, Status, Message, ErrorMessage, Data}.
        if isinstance(body, dict) and "Data" in body:
            return body["Data"]
        return body

    @with_retries
    def _get(self, url: str) -> Any:
        logger.debug("GET %s", url)
        resp: APIResponse = self._ctx.get(url)
        if not resp.ok:
            raise RuntimeError(f"{url} -> HTTP {resp.status}")
        body = resp.json()
        rate_limit()
        if isinstance(body, dict) and "Data" in body:
            return body["Data"]
        return body

    def get_projects(
        self,
        district_id: int = 0,
        application_status: str = "3",
        project_type: int = 0,
        registration_no: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return every project matching the filters (status 3 = registered).

        Passing ``registration_no`` filters server-side to a single project
        (handy for targeted testing).
        """
        payload = {
            "DistrictId": district_id,
            "TeshilId": 0,
            "ProjectName": None,
            "PromoterName": None,
            "RegistrationNo": registration_no,
            "ProjectType": project_type,
            "ApplicationStatus": application_status,
            "Year": 0,
        }
        data = self._post(GET_PROJECTS, payload) or []
        logger.info("GetProjects returned %d rows", len(data))
        return data

    def get_project_by_id(self, encrypted_id: str) -> Optional[dict[str, Any]]:
        """Return one project's detail object, or None if absent."""
        data = self._post(GET_PROJECT_BY_ID, {"ProjectId": encrypted_id})
        projects = (data or {}).get("Project") if isinstance(data, dict) else None
        if projects:
            return projects[0]
        logger.warning("GetProjectById returned no Project for %s", encrypted_id)
        return None

    def get_view_id(self, encrypted_id: str) -> Optional[str]:
        """Resolve a modern EncryptedProjectId to the legacy ViewProject view_id.

        Calls ``ProjectDtlsWebsite/<EncryptedProjectId>`` (which the SPA's detail
        page uses) and returns ``data.ProjectId`` — the DES-encrypted id that
        ``ViewProjectWebsite`` requires. This is what makes granular data
        scrapable at scale without any manual ids.
        """
        if not encrypted_id:
            return None
        from urllib.parse import quote

        url = f"{self._legacy_url}{PROJECT_DTLS}/{quote(encrypted_id, safe='')}"
        body = self._get(url)
        view_id = (body or {}).get("data", {}).get("ProjectId") if isinstance(body, dict) else None
        if not view_id:
            logger.warning("Could not resolve view_id for %s", encrypted_id)
        return view_id

    def get_project_full(self, view_id: str, type_: str = "U") -> Optional[dict[str, Any]]:
        """Return the full legacy project record (allottees, apartments, docs...).

        ``view_id`` is the legacy DES-encrypted project id (e.g. ``yp8UrkL13Ys=``)
        from a ``/ViewProject?id=...&type=U`` URL. The legacy host rejects the
        modern EncryptedProjectId, so this id must be supplied explicitly.
        """
        from urllib.parse import quote

        url = f"{self._legacy_url}{VIEW_PROJECT}?id={quote(view_id, safe='')}&type={type_}"
        data = self._get(url)
        if not data:
            logger.warning("ViewProjectWebsite returned empty for %s", view_id)
        return data

    def get_complaints(self, complaint_type_id: int = 0) -> list[dict[str, Any]]:
        """Return the complaints register (0 = all complaint types)."""
        payload = {
            "compalaint_no": "",        # spelling matches the API's own field name
            "complainant": "",
            "complaint_status": "",
            "respondent_name": "",
            "ComplaintTypeId": complaint_type_id,
        }
        data = self._post(GET_COMPLAINTS, payload) or []
        logger.info("GetComplaintDetailsWebsite returned %d rows", len(data))
        return data


@contextmanager
def api_client() -> Iterator[ReraApiClient]:
    """Yield a ready ReraApiClient; disposes the request context on exit."""
    api_url = os.getenv("RERA_API_URL", "https://reraapi.rajasthan.gov.in").rstrip("/")
    api_key = os.getenv("RERA_API_KEY", "MySuperSecretApiKey_123")
    legacy_url = os.getenv("RERA_LEGACY_URL", "https://reraapp.rajasthan.gov.in").rstrip("/")
    referer = os.getenv("RERA_BASE_URL", "https://rera.rajasthan.gov.in").rstrip("/") + "/"

    with sync_playwright() as p:
        ctx = p.request.new_context(
            base_url=api_url,
            extra_http_headers={
                "x-api-key": api_key,
                "Referer": referer,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            ignore_https_errors=True,
            # GetProjects returns ~5k rows; give the large payload room.
            timeout=120_000,
        )
        try:
            yield ReraApiClient(ctx, legacy_url=legacy_url)
        finally:
            ctx.dispose()
