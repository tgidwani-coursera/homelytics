# Scraping Fixes — apply before/when running the full scrape

Deferred fixes identified during the 52-project sample audit (2026-06). These do
not block dashboard work on the current sample, but **must** be applied before a
full-scale scrape so the complete dataset is clean.

## 1. Status bug — keep the list-level workflow status  ✅ RESOLVED (2026-06)

`scrapers/project_detail.py::_enrich_project` used to overwrite `projects.status`
with `GetProjectById.StatusOfProject`, which returns "Rejected" for ~all
registered projects (it is not the registration/workflow status). Result: 51/52
sample projects wrongly showed `status = "Rejected"`.

**Fixed:** removed the `status` overwrite in `_enrich_project` — `projects.status`
now keeps the list-level value from `GetProjects.AppStatus` (e.g. "Application
Approved" / "Objected"). `StatusOfProject` remains in `project_details.raw_json`.
The 52-project sample was backfilled from `AppStatus`. No further action needed
at full-scrape time.

## 2. Plot data — capture PlotArea / PlotType  ✅ RESOLVED (2026-06)

Plotted projects (~67% of the sample) have units with `PlotArea` / `PlotType`
set but `CarpetArea` / `Block` null, so plotted units had no size data.

**Fixed:** added `plot_area` / `plot_type` columns to `allottees`;
`scrapers/allottees.py` now captures both. `allottees` refresh is also now
delete-then-insert per project (plots have NULL block, so the unique key alone
couldn't dedupe them on re-scrape). Backfilled: all 5,045 plotted units now
carry `plot_area`/`plot_type`. (Note: the portal's `PlotType` often just repeats
the plot number rather than a category — that's the source data.)

## 3. Complaints date & stable key  ✅ RESOLVED (2026-06) — labels still open

- **Fixed:** `filed_date ← updatedon` (source `createdon` is null for ~99.97% of
  rows; `updatedon` is the only reliable date — it is "last updated", not strictly
  "filed"). All complaints now carry a date.
- **Fixed:** added `complaint_no` (portal's `ComplaintNumber`) and made it the
  unique/dedup key, so re-scrapes are idempotent regardless of date changes.
- **Still open (no public source):** `status` (numeric, e.g. 115/89/88) and
  `complaint_type` (1/2) have no label. The complaint records carry no status-name
  field, and no public master endpoint for complaint statuses was found (the
  `GetComponentsNew` master is districts, not statuses). Left as codes rather than
  fabricated labels — needs the portal's internal master or a manual mapping.

## 4. Derive aggregate counts after scraping  ✅ RESOLVED (2026-06)

**Fixed:** `scrapers/derive.py` (`derive_all`, run at the end of every scrape in
`main.py`) populates:
- `project_details.total_units / units_booked / units_unsold` from `allottees`
  counts (booked = `sold`, unsold = `unsold`; note other statuses like `mortgage`
  also exist, so booked + unsold may be < total).
- `promoters.past_projects_count / ongoing_projects_count` from
  `promoter_projects_history` per `promoter_id`.

---

_Source: DB audit of the 52-project Jaipur sample. #1–#4 applied to the sample
2026-06; the code fixes carry forward to the full scrape automatically._
