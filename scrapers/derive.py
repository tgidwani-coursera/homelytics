"""Post-scrape derivation of aggregate counts (SCRAPING_FIXES #4).

Some columns have no direct API source but are derivable from the granular
tables. This fills them in one pass after scraping:

- project_details.total_units / units_booked / units_unsold  <- allottees counts
- promoters.past_projects_count / ongoing_projects_count     <- history counts
"""

from __future__ import annotations

import logging

from db.connection import get_cursor

logger = logging.getLogger("homelytics.derive")

_PROJECT_COUNTS = """
UPDATE project_details d SET
    total_units  = a.total,
    units_booked = a.booked,
    units_unsold = a.unsold
FROM (
    SELECT registration_no,
           count(*)                                          AS total,
           count(*) FILTER (WHERE booking_status = 'sold')   AS booked,
           count(*) FILTER (WHERE booking_status = 'unsold') AS unsold
    FROM allottees
    GROUP BY registration_no
) a
WHERE d.registration_no = a.registration_no
"""

_PROMOTER_COUNTS = """
UPDATE promoters p SET
    past_projects_count    = h.past,
    ongoing_projects_count = h.ongoing
FROM (
    SELECT promoter_id,
           count(*) FILTER (WHERE status = 'past')    AS past,
           count(*) FILTER (WHERE status = 'ongoing') AS ongoing
    FROM promoter_projects_history
    GROUP BY promoter_id
) h
WHERE p.promoter_id = h.promoter_id
"""


def derive_all() -> tuple[int, int]:
    """Recompute derived counts. Returns (project_details rows, promoter rows)."""
    with get_cursor() as cur:
        cur.execute(_PROJECT_COUNTS)
        proj = cur.rowcount
        cur.execute(_PROMOTER_COUNTS)
        prom = cur.rowcount
    logger.info("Derived counts: %d project_details, %d promoters", proj, prom)
    return proj, prom
