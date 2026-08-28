-- S1-08: trace store. Same database (protocol_drift_dev) and default schema
-- as the registry fact tables (trials/outcomes/...) -- there is no
-- multi-schema convention established yet in this project, and introducing
-- one for a single local dev database buys nothing.
--
-- No model calls exist yet in Sprint 1 -- this schema and TraceStore are
-- built now so every retrieval/generation call from Sprint 2 onward has
-- somewhere to write from day one, per the "every model call routes through
-- a traced client" acceptance criterion. Retrofitting observability later is
-- how these projects lose their evidence trail.

CREATE TABLE IF NOT EXISTS query (
    id SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    tier TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (query, stage) retrieval invocation -- e.g. "the dense-search
-- call for query 42". Per-chunk detail (rank, score, source) lives on
-- chunk_hit, not here, since one step produces many hits.
CREATE TABLE IF NOT EXISTS retrieval_step (
    id SERIAL PRIMARY KEY,
    query_id INTEGER NOT NULL REFERENCES query (id),
    stage TEXT NOT NULL CHECK (stage IN ('dense', 'bm25', 'rrf', 'prefilter', 'rerank')),
    latency_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_retrieval_step_query_id ON retrieval_step (query_id);

-- S3-10: the "prefilter" stage returns no chunk hits of its own (it narrows
-- the search space for the dense/bm25 stages that follow, rather than
-- producing a ranking) -- this is where its useful trace signal lives
-- instead: which filter(s) actually fired, for S5's later failure analysis
-- on cases where a wrong or missing filter explains a retrieval miss.
-- `IF NOT EXISTS` (not a separate migration) so re-running this file
-- against the already-populated dev database is safe, same convention as
-- db/schema.sql's enrollment_count column.
ALTER TABLE retrieval_step ADD COLUMN IF NOT EXISTS filters_applied TEXT;

-- One row per chunk returned by a retrieval_step. nct_id/doc_type/section/
-- page_range are denormalized here (rather than looked up from the corpus
-- index at read time) so the trace viewer (S5-02) can render a query's full
-- retrieval trace from this table alone.
CREATE TABLE IF NOT EXISTS chunk_hit (
    id SERIAL PRIMARY KEY,
    retrieval_step_id INTEGER NOT NULL REFERENCES retrieval_step (id),
    chunk_id TEXT NOT NULL,
    rank INTEGER,
    score DOUBLE PRECISION,
    nct_id TEXT,
    doc_type TEXT,
    section TEXT,
    page_range TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunk_hit_retrieval_step_id ON chunk_hit (retrieval_step_id);

CREATE TABLE IF NOT EXISTS generation (
    id SERIAL PRIMARY KEY,
    query_id INTEGER NOT NULL REFERENCES query (id),
    model_digest TEXT,
    prompt_hash TEXT,
    response_text TEXT,
    latency_ms DOUBLE PRECISION,
    token_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_generation_query_id ON generation (query_id);

CREATE TABLE IF NOT EXISTS cost_record (
    id SERIAL PRIMARY KEY,
    generation_id INTEGER NOT NULL REFERENCES generation (id),
    tokens_in INTEGER,
    tokens_out INTEGER,
    wall_clock_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cost_record_generation_id ON cost_record (generation_id);
