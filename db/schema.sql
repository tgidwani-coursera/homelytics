-- Homelytics — Rajasthan RERA scraper schema
-- Idempotent: safe to run repeatedly. Uses IF NOT EXISTS throughout.

CREATE TABLE IF NOT EXISTS projects (
    registration_no      TEXT PRIMARY KEY,
    project_name         TEXT,
    promoter_name        TEXT,
    district             TEXT,
    tehsil               TEXT,
    project_type         TEXT,
    project_category     TEXT,
    address              TEXT,
    status               TEXT,
    date_of_registration DATE,
    state                TEXT NOT NULL DEFAULT 'rajasthan',
    scraped_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_district     ON projects (district);
CREATE INDEX IF NOT EXISTS idx_projects_promoter     ON projects (promoter_name);

CREATE TABLE IF NOT EXISTS project_details (
    registration_no         TEXT PRIMARY KEY
                              REFERENCES projects (registration_no) ON DELETE CASCADE,
    total_units             INTEGER,
    units_booked            INTEGER,
    units_unsold            INTEGER,
    completion_percentage   NUMERIC(5,2),
    expected_completion_date DATE,
    land_area               TEXT,
    raw_json                JSONB,
    scraped_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS promoters (
    promoter_id            BIGSERIAL PRIMARY KEY,
    registration_no        TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    name                   TEXT,
    company_type           TEXT,
    address                TEXT,
    past_projects_count    INTEGER,
    ongoing_projects_count INTEGER,
    state                  TEXT NOT NULL DEFAULT 'rajasthan',
    scraped_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One promoter row per project keeps re-runs idempotent.
    UNIQUE (registration_no, name)
);

CREATE INDEX IF NOT EXISTS idx_promoters_name ON promoters (name);

CREATE TABLE IF NOT EXISTS promoter_projects_history (
    id              BIGSERIAL PRIMARY KEY,
    promoter_id     BIGINT REFERENCES promoters (promoter_id) ON DELETE CASCADE,
    project_name    TEXT,
    registration_no TEXT,
    location        TEXT,
    status          TEXT,  -- past / ongoing / registered
    UNIQUE (promoter_id, project_name, registration_no)
);

CREATE TABLE IF NOT EXISTS apartment_types (
    id               BIGSERIAL PRIMARY KEY,
    registration_no  TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    type_name        TEXT,
    carpet_area_sqft NUMERIC(10,2),
    bathrooms        INTEGER,
    balconies        INTEGER,
    total_count      INTEGER,
    sold_count       INTEGER,
    unsold_count     INTEGER,
    UNIQUE (registration_no, type_name)
);

CREATE TABLE IF NOT EXISTS allottees (
    id              BIGSERIAL PRIMARY KEY,
    registration_no TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    floor_no        TEXT,
    unit_no         TEXT,
    carpet_area     NUMERIC(10,2),
    booking_status  TEXT,  -- sold / unsold
    booking_date    DATE,
    UNIQUE (registration_no, floor_no, unit_no)
);

CREATE TABLE IF NOT EXISTS common_areas (
    id              BIGSERIAL PRIMARY KEY,
    registration_no TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    parking_type    TEXT,
    covered_parking INTEGER,
    open_parking    INTEGER,
    amenities_json  JSONB,
    UNIQUE (registration_no, parking_type)
);

CREATE TABLE IF NOT EXISTS documents (
    id              BIGSERIAL PRIMARY KEY,
    registration_no TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    doc_type        TEXT,
    doc_name        TEXT,
    doc_url         TEXT,
    uploaded_at     TIMESTAMPTZ,
    UNIQUE (registration_no, doc_url)
);

CREATE TABLE IF NOT EXISTS complaints (
    id              BIGSERIAL PRIMARY KEY,
    registration_no TEXT REFERENCES projects (registration_no) ON DELETE SET NULL,
    complainant     TEXT,
    respondent      TEXT,
    complaint_type  TEXT,
    status          TEXT,
    filed_date      DATE,
    state           TEXT NOT NULL DEFAULT 'rajasthan',
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (complainant, respondent, filed_date)
);

CREATE INDEX IF NOT EXISTS idx_complaints_respondent ON complaints (respondent);

-- Append-only inventory snapshots for trend series (e.g. flats booked over time).
-- One row per project per scrape date; the rest of the schema upserts in place,
-- so this is the only table that retains history across scrapes.
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    registration_no TEXT REFERENCES projects (registration_no) ON DELETE CASCADE,
    snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    total_units     INTEGER,
    units_booked    INTEGER,
    units_unsold    INTEGER,
    units_mortgage  INTEGER,
    units_other     INTEGER,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (registration_no, snapshot_date)
);
