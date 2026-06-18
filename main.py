"""Homelytics — Rajasthan RERA scraper entry point.

Examples
--------
    # Default: scrape Jaipur projects + details + complaints
    python main.py

    # Test run: 5 projects in Jodhpur, skip complaints
    python main.py --district Jodhpur --limit 5 --no-complaints

    # Only (re)create the DB schema and exit
    python main.py --init-db

Re-running is safe: every write upserts on a natural key, so projects are
updated in place rather than duplicated.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from db.connection import apply_schema, close_pool, init_pool
from scrapers.api import api_client
from scrapers.complaints import scrape_complaints
from scrapers.project_detail import scrape_detail
from scrapers.project_list import scrape_list
from scrapers.snapshots import record_snapshot
from scrapers.utils import configure_logging
from scrapers.view_project import enrich_from_view

logger = logging.getLogger("homelytics.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="homelytics",
        description="Scrape Rajasthan RERA project, promoter, and complaint data.",
    )
    parser.add_argument(
        "--district", default="Jaipur",
        help="Filter projects by district (default: Jaipur). Use 'all' for every district.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max number of projects to process (for testing small batches).",
    )
    parser.add_argument(
        "--registration", default=None,
        help="Scrape only this RERA registration number (e.g. RAJ/P/2025/4508). "
             "Ignores --district. Implies --no-complaints unless overridden.",
    )
    parser.add_argument(
        "--view-id", default=None,
        help="Legacy ViewProject id (from /ViewProject?id=...&type=U) to pull the "
             "full record: apartment types, allottees, common areas, full document "
             "list, promoter history. Use with --registration.",
    )
    parser.add_argument(
        "--init-db", action="store_true",
        help="Apply db/schema.sql and exit without scraping.",
    )
    parser.add_argument(
        "--refresh-existing", action="store_true",
        help="Re-scrape only the projects already in the DB (the bi-weekly "
             "refresh scope). Ignores --district. Records a trend snapshot per project.",
    )
    parser.add_argument(
        "--skip-details", action="store_true",
        help="Only scrape the project list, not each project's detail page.",
    )
    parser.add_argument(
        "--skip-enrich", action="store_true",
        help="Skip the legacy ViewProjectWebsite enrichment (apartment types, "
             "allottees, common areas, full documents, promoter history). "
             "Enrichment is on by default and adds ~2 API calls per project.",
    )
    parser.add_argument(
        "--no-complaints", action="store_true",
        help="Skip the complaints register.",
    )
    parser.add_argument(
        "--only-complaints", action="store_true",
        help="Scrape only the complaints register (assumes projects already loaded).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    # --refresh-existing scopes by the local DB, so the district filter is moot.
    district = None if (args.district.lower() == "all" or args.refresh_existing) else args.district

    init_pool()
    apply_schema()  # idempotent — ensures tables exist before we write

    if args.init_db:
        logger.info("Schema applied. Exiting (--init-db).")
        return 0

    projects_done = 0
    details_ok = 0
    complaints_done = 0

    # Targeting one registration number is a focused test: skip district and
    # complaints by default (complaints can still be forced off/on explicitly).
    only_complaints = args.only_complaints and not args.registration
    do_complaints = not args.no_complaints and not args.registration

    with api_client() as client:
        if not only_complaints:
            logger.info(
                "Scraping project list (district=%s, registration=%s, limit=%s)",
                district or "all", args.registration, args.limit,
            )
            for stub in scrape_list(
                client, district=district, limit=args.limit,
                registration_no=args.registration,
                refresh_existing=args.refresh_existing,
            ):
                projects_done += 1
                if not args.skip_details:
                    if scrape_detail(client, stub):
                        details_ok += 1
                if not args.skip_enrich:
                    # Resolve the legacy view_id automatically (or use an override),
                    # then pull the granular tables from ViewProjectWebsite.
                    view_id = args.view_id or client.get_view_id(stub.get("encrypted_id"))
                    if view_id:
                        enrich_from_view(client, stub["registration_no"], view_id)
                        # Append a dated inventory snapshot for trend series.
                        record_snapshot(stub["registration_no"])

        if do_complaints:
            logger.info("Scraping complaints register")
            complaints_done = scrape_complaints(client, limit=args.limit)

    logger.info(
        "Done. projects=%d details_ok=%d complaints=%d",
        projects_done, details_ok, complaints_done,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    configure_logging(level=getattr(logging, args.log_level))
    try:
        return run(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception:  # noqa: BLE001
        logger.exception("Fatal error during scrape")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
