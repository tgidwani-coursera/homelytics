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

## 2. Plot data — capture PlotArea / PlotType for plotted projects

Plotted projects (~67% of the sample) have units in
`GetApartmentAllotteeDetailsList` with `PlotArea` / `PlotType` / `PlotId` set but
`CarpetArea` / `Block` null. We currently store only `Units` + `BookingStatus`,
so ~72% of allottee rows have NULL `carpet_area` and no size data.

**Fix:** in `scrapers/allottees.py`, for plot entries fall back to `PlotArea`
(into `carpet_area`, or add a dedicated column) and record `PlotType`. Decide
whether to add `plot_area` / `plot_type` columns to the `allottees` schema vs.
reusing `carpet_area`.

## 3. Complaints date & status/type labels

- `filed_date` is empty: source `createdon` is null for ~99.97% of complaints;
  only `updatedon` is reliably populated. **Fix:** map `filed_date ← updatedon`
  (best-available date; note it is "last updated", not strictly "filed").
- `complaints.status` stores numeric codes (101 / 114 / 115 / 110) and
  `complaint_type` stores 1 / 2, with no labels. **Fix:** fetch the status-code →
  label master (and complaint-type master) from the portal and store readable
  labels (or a lookup table).
- Consider a more stable unique key for `complaints` (e.g. ComplaintNumber)
  since `filed_date` currently underpins the upsert constraint.

## 4. Derive aggregate counts after scraping

These columns have no API source but are derivable; populate them in a
post-scrape pass:

- `project_details.total_units`, `units_booked`, `units_unsold`
  ← counts from `allottees` per `registration_no`
  (`total = count(*)`, `booked = count where booking_status = 'sold'`,
  `unsold = count where booking_status = 'unsold'`; note other statuses like
  `mortgage` / `not yet approved for sale` exist).
- `promoters.past_projects_count`, `promoters.ongoing_projects_count`
  ← counts from `promoter_projects_history` per `promoter_id`
  (by `status = 'past'` / `status = 'ongoing'`).

---

_Source: DB audit of the 52-project Jaipur sample. See README "Granular data
(automatic)" for the scrape pipeline these fixes apply to._
