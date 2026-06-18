"""Record point-in-time inventory snapshots for trend series.

Every scrape that refreshes a project's allottees also appends one row to
`inventory_snapshots`, keyed by (registration_no, CURRENT_DATE). Because the
rest of the schema upserts in place, this table is what makes "flats booked over
time" possible: each run leaves a dated data point behind instead of
overwriting the last.

Keyed per calendar day (re-running the same day overwrites that day's point),
which suits the bi-weekly scrape cadence — each run is its own trend point.
"""

from __future__ import annotations

import logging

from db.connection import get_cursor

logger = logging.getLogger("homelytics.snapshots")

# Aggregate the unit-level allottee data into one snapshot row. HAVING guards
# against writing an all-zero row for projects that have no allottee data.
_SNAPSHOT_SQL = """
INSERT INTO inventory_snapshots
    (registration_no, snapshot_date, total_units, units_booked,
     units_unsold, units_mortgage, units_other)
SELECT %(reg)s, CURRENT_DATE,
       count(*),
       count(*) FILTER (WHERE booking_status = 'sold'),
       count(*) FILTER (WHERE booking_status = 'unsold'),
       count(*) FILTER (WHERE booking_status = 'mortgage'),
       count(*) FILTER (WHERE booking_status NOT IN ('sold', 'unsold', 'mortgage'))
FROM allottees
WHERE registration_no = %(reg)s
HAVING count(*) > 0
ON CONFLICT (registration_no, snapshot_date) DO UPDATE SET
    total_units    = EXCLUDED.total_units,
    units_booked   = EXCLUDED.units_booked,
    units_unsold   = EXCLUDED.units_unsold,
    units_mortgage = EXCLUDED.units_mortgage,
    units_other    = EXCLUDED.units_other,
    captured_at    = now()
"""


def record_snapshot(registration_no: str) -> bool:
    """Append/refresh today's inventory snapshot for one project.

    Returns True if a row was written (i.e. the project had allottee data).
    """
    with get_cursor() as cur:
        cur.execute(_SNAPSHOT_SQL, {"reg": registration_no})
        written = cur.rowcount > 0
    if written:
        logger.info("Snapshot recorded for %s", registration_no)
    return written


def backfill_all() -> int:
    """Record today's snapshot for every project that has allottee data.

    Used to seed a baseline point from data already in the DB (without
    re-scraping). Returns the number of snapshot rows written.
    """
    sql = """
    INSERT INTO inventory_snapshots
        (registration_no, snapshot_date, total_units, units_booked,
         units_unsold, units_mortgage, units_other)
    SELECT registration_no, CURRENT_DATE,
           count(*),
           count(*) FILTER (WHERE booking_status = 'sold'),
           count(*) FILTER (WHERE booking_status = 'unsold'),
           count(*) FILTER (WHERE booking_status = 'mortgage'),
           count(*) FILTER (WHERE booking_status NOT IN ('sold','unsold','mortgage'))
    FROM allottees
    GROUP BY registration_no
    ON CONFLICT (registration_no, snapshot_date) DO UPDATE SET
        total_units    = EXCLUDED.total_units,
        units_booked   = EXCLUDED.units_booked,
        units_unsold   = EXCLUDED.units_unsold,
        units_mortgage = EXCLUDED.units_mortgage,
        units_other    = EXCLUDED.units_other,
        captured_at    = now()
    """
    with get_cursor() as cur:
        cur.execute(sql)
        n = cur.rowcount
    logger.info("Backfilled %d inventory snapshots", n)
    return n
