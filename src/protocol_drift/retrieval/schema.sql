-- S3-02: chunk store -- pgvector dense index + tsvector lexical index +
-- metadata columns for the retrieval ladder's prefilter stage. Same
-- one-schema.sql-per-package convention as db/schema.sql, trace/schema.sql.
--
-- Naming note (results/ablation.md, S3-12, repeats this): Postgres's
-- tsvector + ts_rank_cd is a cover-density ranking function, not Okapi
-- BM25. Every place this project's docs/tables say "BM25" it means
-- "Postgres full-text search used as the lexical leg of hybrid retrieval."
--
-- chunk_id format ("{nct_id}:{doc_type}:{chunk_index}") matches
-- trace/schema.sql's chunk_hit.chunk_id and S3-01's embed.py::chunk_id_for
-- -- picked once so retrieval, the trace store, and S5-02's trace viewer
-- all read the same identifier without a join table.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,              -- "{nct_id}:{doc_type}:{chunk_index}"
    nct_id TEXT NOT NULL REFERENCES trials (nct_id),
    doc_type TEXT NOT NULL CHECK (doc_type IN ('protocol', 'sap')),
    -- DOUBLE PRECISION, not INTEGER as sprint_3_implementation.md's draft
    -- SQL sketches it: real chunks carry non-integer version markers (e.g.
    -- "Amendment 4.03", "Revised Protocol No.: 1.1") from S2-07's page-
    -- footer extraction -- confirmed on 905 of 30,332 chunks in the actual
    -- corpus. An INTEGER column would truncate or reject those on load.
    doc_version DOUBLE PRECISION,
    section TEXT,
    subsection TEXT,
    page_range TEXT,
    chunk_type TEXT NOT NULL CHECK (chunk_type IN ('text', 'table', 'assessment_schedule')),
    is_ocr BOOLEAN NOT NULL DEFAULT FALSE,
    text TEXT NOT NULL,
    embedding vector(768),
    embedding_cache_key TEXT,
    text_search tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

-- pgvector >=0.5 supports HNSW (confirmed locally installed: 0.8.6). If a
-- much older pgvector is ever the target, swap this for
-- `CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);`
-- -- check `SELECT extversion FROM pg_extension WHERE extname = 'vector';` first.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_chunks_text_search ON chunks USING gin (text_search);

-- S3-10's metadata prefilter: nct_id is always known (the eval harness asks
-- about one trial at a time), doc_type/doc_version narrow further.
CREATE INDEX IF NOT EXISTS idx_chunks_nct_doc_version ON chunks (nct_id, doc_type, doc_version);
