-- S1-05: registry fact tables. Populated only from data/registry_snapshots/
-- (the frozen archive written by S1-04) — never from a live API call.
--
-- Date columns are TEXT, not DATE: ~3% of startDateStruct/primaryCompletionDateStruct
-- values in the frozen cohort are year-month only ("2017-01", no day), which a DATE
-- column can't represent without silently inventing a day. TEXT preserves exactly
-- what the registry reported.

CREATE TABLE IF NOT EXISTS trials (
    nct_id TEXT PRIMARY KEY,
    brief_title TEXT NOT NULL,
    condition TEXT,
    phase TEXT,
    sponsor_class TEXT,
    sponsor_name TEXT,
    overall_status TEXT,
    start_date TEXT,
    primary_completion_date TEXT,
    has_protocol BOOLEAN NOT NULL DEFAULT FALSE,
    has_sap BOOLEAN NOT NULL DEFAULT FALSE
);

-- S3-03: T1's enrollment-target question needs this and it wasn't captured
-- at S1-05 time. `IF NOT EXISTS` (not a separate migrations/ setup) so
-- re-running this file against the already-populated dev database is safe,
-- matching every CREATE TABLE above -- run db/extract.py --apply-schema
-- again to both add the column and backfill it for the existing cohort.
ALTER TABLE trials ADD COLUMN IF NOT EXISTS enrollment_count INTEGER;

-- source: 'registered_first' (from versions/0.json), 'registered_current'
-- (from current.json), 'results_reported' (from current.json's resultsSection,
-- filtered to PRIMARY). `version` is 0 for registered_first; NULL for the
-- other two sources -- correlating "current" or "results" to a specific
-- revision number is Sprint 4's amendment-tagging job (S4-01), not this one's.
CREATE TABLE IF NOT EXISTS outcomes (
    id SERIAL PRIMARY KEY,
    nct_id TEXT NOT NULL REFERENCES trials (nct_id),
    kind TEXT NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN ('registered_first', 'registered_current', 'results_reported')
    ),
    measure TEXT NOT NULL,
    timeframe TEXT,
    description TEXT,
    version INTEGER
);

CREATE INDEX IF NOT EXISTS idx_outcomes_nct_id ON outcomes (nct_id);

CREATE TABLE IF NOT EXISTS arms (
    id SERIAL PRIMARY KEY,
    nct_id TEXT NOT NULL REFERENCES trials (nct_id),
    arm_label TEXT NOT NULL,
    arm_type TEXT,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_arms_nct_id ON arms (nct_id);

CREATE TABLE IF NOT EXISTS eligibility (
    nct_id TEXT PRIMARY KEY REFERENCES trials (nct_id),
    min_age TEXT,
    max_age TEXT,
    sex TEXT,
    criteria_text TEXT
);

-- modules_changed comes from history.json's changes[].moduleLabels.
CREATE TABLE IF NOT EXISTS amendments (
    id SERIAL PRIMARY KEY,
    nct_id TEXT NOT NULL REFERENCES trials (nct_id),
    version INTEGER NOT NULL,
    date TEXT,
    modules_changed TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_amendments_nct_id ON amendments (nct_id);
