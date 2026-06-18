# Homelytics

A polite, idempotent scraper for the **Rajasthan RERA** portal
(`rera.rajasthan.gov.in`). It collects registered projects, their details,
promoters and project history, apartment/unit types, unit-level allottee data,
common areas, document links, and the public complaints register into
PostgreSQL.

> **Scope / ethics:** This collects *publicly published regulatory disclosures*
> from the portal's own JSON API. It rate-limits every request (2–3s), sends a
> descriptive User-Agent, and retries gently. Allottee names (PII) are
> deliberately **not** stored. Review the portal's terms of use before running
> at scale, and don't raise the request rate.

---

## How the portal actually works

The live "RERA 2.0" site is a React SPA backed by a JSON API — there is no HTML
table scraping. Two hosts are involved, both authenticated with a static
client-side key the site ships to every browser (`x-api-key`):

| Host | Endpoint | Provides |
|------|----------|----------|
| `reraapi.rajasthan.gov.in` | `Home/GetProjects` | all registered projects (one call) |
| `reraapi.rajasthan.gov.in` | `Home/GetProjectById` | project-level detail (areas, building counts, promoter, dates) |
| `reraapi.rajasthan.gov.in` | `Home/GetComplaintDetailsWebsite` | the complaints register |
| `reraapp.rajasthan.gov.in` *(legacy)* | `HomeWebsite/ProjectDtlsWebsite/{EncryptedProjectId}` | resolves the modern id → the legacy `view_id` |
| `reraapp.rajasthan.gov.in` *(legacy)* | `HomeWebsite/ViewProjectWebsite?id={view_id}` | **full record**: apartment types, unit-level allottees, common areas, full document list, promoter project history |

The modern endpoints cover project/promoter/document basics. The **granular**
tables (apartment types, allottees, common areas, promoter history) come from the
legacy `ViewProjectWebsite` endpoint — reached automatically via the id chain
described in [Granular data (automatic)](#granular-data-automatic) below.

## Tech stack

- **Python 3.10+**
- **Playwright** — used as an HTTP client (`APIRequestContext`) for TLS + the
  shared auth header; no browser/DOM scraping
- **PostgreSQL** — storage, with idempotent upserts
- `python-dotenv`, `tenacity` (retry/backoff)

## Project layout

```
homelytics/
  scrapers/
    api.py               # ReraApiClient: GetProjects / GetProjectById /
                         #   GetComplaints / ViewProjectWebsite (legacy)
    project_list.py      # GetProjects -> projects (district filter)
    project_detail.py    # GetProjectById -> project_details, promoters, documents
    view_project.py      # ViewProjectWebsite -> granular tables (needs view_id)
    promoter.py          # promoter row + project history
    apartment_types.py   # unit-type breakdown        (from ViewProjectWebsite)
    allottees.py         # unit-level sold/unsold      (from ViewProjectWebsite)
    common_areas.py      # parking + amenities         (from ViewProjectWebsite)
    documents.py         # certificate + full doc list
    complaints.py        # GetComplaintDetailsWebsite + respondent matching
    utils.py             # logging, rate-limit, retry, parsers
  db/
    schema.sql           # all tables (CREATE TABLE IF NOT EXISTS)
    connection.py        # pool + generic upsert helpers
  main.py                # CLI entry point
  requirements.txt
  .env.example
  README.md
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium          # Playwright bundles its own TLS stack

# 2. Configure credentials / endpoints
cp .env.example .env
# edit .env with your PostgreSQL host/user/password

# 3. Create the database (once), then the tables
createdb homelytics
python main.py --init-db             # applies db/schema.sql
```

## Usage

```bash
# Default: Jaipur projects + details + complaints
python main.py

# Small test batch (5 projects)
python main.py --district Jaipur --limit 5

# List only (skip per-project detail calls)
python main.py --district Jodhpur --skip-details

# Every district
python main.py --district all

# Refresh just the complaints register (projects already loaded)
python main.py --only-complaints

# One specific project (granular data is resolved & scraped automatically)
python main.py --registration "RAJ/P/2024/3341"

# Skip the granular enrichment (faster; project/promoter/docs basics only)
python main.py --district Jaipur --skip-enrich

# Re-scrape only the projects already in the DB (bi-weekly refresh scope)
python main.py --refresh-existing --no-complaints
```

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--district` | `Jaipur` | Filter by district; `all` for every district |
| `--limit` | _none_ | Cap projects/complaints processed (handy for testing) |
| `--registration` | _none_ | Scrape only this registration number; ignores `--district` |
| `--refresh-existing` | off | Re-scrape only projects already in the DB (bi-weekly refresh scope) |
| `--view-id` | _none_ | Override the auto-resolved legacy view_id (rarely needed) |
| `--init-db` | off | Apply schema and exit |
| `--skip-details` | off | Scrape the project list only, not detail calls |
| `--skip-enrich` | off | Skip granular enrichment (apartment types/allottees/etc.) |
| `--no-complaints` | off | Skip the complaints register |
| `--only-complaints` | off | Scrape only complaints |
| `--log-level` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

## How it works

1. **`project_list`** calls `GetProjects` (one request returns ~5k projects),
   filters by district client-side, and upserts each into `projects`.
2. **`project_detail`** calls `GetProjectById` per project and fills
   `project_details` (areas, building counts, completion date, status, with the
   full payload kept in `raw_json`), `promoters`, and the RC-certificate
   `documents` row.
3. **`view_project`** *(on by default)* resolves the legacy `view_id` and calls
   `ViewProjectWebsite`, filling `apartment_types`, `allottees`, `common_areas`,
   the full `documents` list, and `promoter_projects_history`.
4. **`complaints`** calls `GetComplaintDetailsWebsite` and links each row to a
   project by matching the respondent against `projects.promoter_name`
   (case-insensitive, substring fallback), then the complaint's project name.

### Granular data (automatic)

Apartment types, unit-level allottees, common areas, and promoter history are
**only** served by the legacy `ViewProjectWebsite` endpoint, keyed by a
DES-encrypted `view_id` (e.g. `yp8UrkL13Ys=`) that differs from the modern
`EncryptedProjectId`.

The scraper resolves that `view_id` automatically with no manual input:

```
GetProjects            -> EncryptedProjectId   (e.g. oPu-ybzdsXH27KJ_zKTngQ)
ProjectDtlsWebsite/{id} -> data.ProjectId       (the view_id, e.g. yp8UrkL13Ys=)
ViewProjectWebsite?id   -> full granular record
```

So a plain `python main.py --registration "RAJ/P/2024/3341"` (or any district
run) populates all nine tables. Enrichment adds ~2 API calls per project; use
`--skip-enrich` to turn it off. `--view-id` exists only to override the
auto-resolved id in the rare case it's needed.

Verified end-to-end for `RAJ/P/2024/3341` (Vardhman Eminara): 131 apartment
types, 592 allottee units (169 sold / 423 unsold), 8 amenities, 11 documents,
8 promoter-history rows. (Plotted projects legitimately have 0 apartment types —
they sell plots, captured as allottee units.)

### Reliability

- **Rate limiting:** random 2–3s between requests (`RATE_LIMIT_MIN/MAX`).
- **Retries:** 3 attempts with exponential backoff (`tenacity`) on every API call.
- **Logging:** structured logs to console **and** rotating `scraper.log`.
- **Idempotent:** every table has a natural-key unique constraint and all writes
  are `INSERT ... ON CONFLICT DO UPDATE`, so re-runs refresh rather than duplicate.
- **Fault isolation:** a failure in one sub-step (e.g. allottees) is logged and
  the rest of the project still gets saved.

## Bi-weekly refresh & trends

The scraper upserts in place, so most tables only hold the latest state. The one
exception is **`inventory_snapshots`** — an append-only table that records each
project's unit counts (total / booked / unsold / mortgage) per scrape date. That
is what powers the "flats booked over time" trend on the dashboard.

A scheduled refresh keeps it growing. On macOS, a launchd agent runs the scrape
on the **1st & 15th of each month at 02:00**, scoped to the projects already in
the DB (`--refresh-existing`), recording one snapshot per project per run:

```bash
# install (once)
cp scripts/com.homelytics.biweekly.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.homelytics.biweekly.plist
# run once now / uninstall
launchctl start  com.homelytics.biweekly
launchctl unload -w ~/Library/LaunchAgents/com.homelytics.biweekly.plist
```

The job runs [`scripts/biweekly_scrape.sh`](scripts/biweekly_scrape.sh) and logs
to `biweekly.log`. Grow the tracked set anytime by scraping more projects (e.g.
`--district Jaipur`); the next refresh will include them. Bi-weekly suits how
infrequently the RERA portal is updated.

## Dashboard

A read-only web dashboard (FastAPI + a single-file vanilla HTML/CSS/JS SPA) for
exploring the scraped data — built for home-buyer "trust and transparency".

```bash
pip install -r requirements.txt   # adds fastapi + uvicorn
./run.sh                          # -> http://127.0.0.1:8000  (reads .env)
```

Pages (hash-routed): a welcoming landing (`#/`) with a state/district picker that
leads into the explorer (`#/explore` — stats + search/filters + project cards),
project detail (inventory booked% bar, flats-booked-over-time trend, apartment
types, a must-have/good-to-have document checklist, amenities), and builder
profiles (in-DB projects + declared history, complaints). API under `/api/*`:

| Endpoint | Returns |
|----------|---------|
| `GET /api/stats` | totals + filter options |
| `GET /api/projects?district=&type=&status=&search=` | filtered project list |
| `GET /api/projects/{registration_no}` | full detail (all tables joined) |
| `GET /api/builders/{promoter_name}` | builder profile |
| `GET /api/complaints?respondent=` | complaints for a builder |

Code: [`dashboard/app.py`](dashboard/app.py) (API) and
[`dashboard/static/index.html`](dashboard/static/index.html) (SPA).

## Database schema

See [`db/schema.sql`](db/schema.sql). Tables: `projects`, `project_details`,
`promoters`, `promoter_projects_history`, `apartment_types`, `allottees`,
`common_areas`, `documents`, `complaints`. Foreign keys cascade from
`projects.registration_no`.

> Carpet areas are stored in **square metres** (the portal's unit), despite the
> `apartment_types.carpet_area_sqft` column name kept from the original spec.
