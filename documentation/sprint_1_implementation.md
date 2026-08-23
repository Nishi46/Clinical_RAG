# Sprint 1 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 1 (Corpus & Harness / E1, ~21h). Companion to
`sprint_plan.md`. Each step is small enough to check off individually. This sprint builds the real
versions of what Sprint 0 only prototyped in `scratch/` — every step below should read from the
confirmed field paths in `scratch/field_paths.md`, the oncology decision in
`scratch/therapeutic_area_decision.md`, and the pinned models in `configs/models.yaml`, not
re-derive them.

**Do not start S1-01 until the Sprint 0 exit checklist is fully checked off.**

---

## S1-01 — Repo scaffold (2h)

1. Pin a Python version (3.11+; system default is 3.9.6, so install via `pyenv install 3.11.9` or
   `brew install python@3.11`) and write `.python-version`.
2. Create and activate a venv: `python3.11 -m venv .venv && source .venv/bin/activate`.
3. Write `pyproject.toml`: project name `protocol-drift`, `requires-python = ">=3.11"`, runtime deps
   (`requests`, `pymupdf`, `psycopg[binary]`, `pgvector`, `pydantic`, `python-dotenv`), dev deps
   (`ruff`, `mypy`, `pytest`, `pytest-cov`, `responses`, `types-requests`) under an optional group.
4. `pip install -e ".[dev]"` — confirm it resolves cleanly.
5. Create the `src/protocol_drift/` package with stub subpackages matching this sprint's
   deliverables: `registry/` (`client.py`, `cohort.py`, `snapshot.py`, `extract.py`), `corpus/`
   (`download.py`, `classify.py`), `trace/` (`store.py`, `schema.sql`), `db/` (`schema.sql`).
6. Create `tests/` mirroring the `src/` layout, each with `__init__.py`, plus one trivial
   `tests/test_smoke.py` (`assert True`) to confirm pytest discovers and runs.
7. Add `[tool.ruff]` (line-length 100, `select = ["E","F","I","UP","B"]`) and `[tool.mypy]`
   (`strict = true`, per-module `ignore_missing_imports` overrides for untyped libs like `fitz`).
8. Run `ruff check .`, `mypy src`, `pytest` locally against the stub — confirm all three are green
   before writing any real logic.
9. Write `.github/workflows/ci.yml`: checkout → setup-python 3.11 → `pip install -e ".[dev]"` →
   `ruff check .` → `mypy src` → `pytest -m "not integration"`.
10. Add a root `Makefile` with `lint`, `typecheck`, `test`, `fmt` targets that wrap the exact same
    commands CI runs, so local and CI invocations never drift apart.
11. Extend `.gitignore`: add `data/`, `*.egg-info/`, `.mypy_cache/`, `.ruff_cache/`,
    `.pytest_cache/`, `.env` (existing entries already cover `scratch/`, `__pycache__/`, `.venv/`).
12. Commit the scaffold and push; confirm the GitHub Actions run is green on the empty stub before
    building on top of it.

**Done when:** CI is green on a pushed commit containing only the scaffold + smoke test.

---

## S1-02 — API v2 client (2.5h)

Formalizes `scratch/explore_api.py`. Field paths and the two confirmed endpoint families
(`/api/v2/studies` and the undocumented `/api/int/studies/{id}/history[...]`) come straight from
`scratch/field_paths.md` — do not re-probe them.

1. Create `RegistryClient` in `registry/client.py`, wrapping a `requests.Session`, base URL
   `https://clinicaltrials.gov/api/v2`, configurable timeout.
2. Implement `get_study(nct_id, fields=None) -> dict` — `GET /studies/{nct_id}`, optional
   comma-joined `fields=` param (confirmed to shrink the payload in S0-01 step 12).
3. Implement `search_studies(**query_params) -> Iterator[dict]` that transparently follows
   `nextPageToken` pagination and yields individual study dicts — callers never see paging.
4. Add retry/backoff (exponential + jitter, max ~5 attempts) on 429/5xx/connection errors.
5. Add a politeness rate limit between requests (reuse the ~0.5s convention from
   `scratch/download_pdfs.py`), configurable.
6. Implement `get_history(nct_id) -> dict` and `get_history_version(nct_id, version) -> dict`
   against `/api/int/studies/{nct_id}/history` and `/history/{version}`. Docstring both as
   **unstable/undocumented** per the risk flag in `field_paths.md`; raise a named
   `HistoryEndpointUnavailable` on a non-200 or unexpected shape instead of a raw `KeyError`.
7. Log request URL + elapsed time at DEBUG on every call — this is the seed the trace store
   (S1-08) will wrap later.
8. Write `tests/registry/test_client.py` against `responses`-mocked HTTP (no live network in CI):
   pagination stitches two mocked pages with no duplicate NCT IDs; a mocked 429 triggers retry then
   succeeds; `fields=` is passed through correctly.
9. Write one `@pytest.mark.integration` live test fetching `NCT02872116` and asserting
   `hasResults is True` — excluded from the default CI run (S1-01 step 9), run manually.

**Done when:** `pytest -m "not integration"` is green against mocked HTTP, and the one live
integration test passes when run manually.

---

## S1-03 — Cohort query + freeze (2.5h)

Oncology was chosen in `scratch/therapeutic_area_decision.md`; `hasProtocol`/`hasSap` are
per-document fields that cannot be filtered server-side (`field_paths.md` §1) — filter client-side.

1. Create `registry/cohort.py`; implement `candidate_query()` returning the confirmed params:
   `query.cond=cancer`, `filter.overallStatus=COMPLETED`, results-posted filter, and a
   `studyFirstPostDate >= 2017-01-01` cutoff (server-side param if available, else client-side
   check — confirm which against `scratch/area_doc_rate.py`).
2. Fetch candidates via `search_studies`, restricting `fields=` to identification / status / dates
   / `documentSection` to keep the sweep cheap.
3. Client-side filter to records where `documentSection.largeDocumentModule.largeDocs[]` has ≥1
   entry with `hasProtocol` or `hasSap` true.
4. Extract stratification keys per candidate: sponsor class (`sponsorCollaborators.leadSponsor.class`)
   and phase (`design.phases[]`).
5. Implement deterministic stratified sampling: group by (sponsor_class, phase), sort each group by
   NCT ID, take a proportional slice per group with a hard cap of 2–3 trials per sponsor
   (per S0-02's own dedupe rule) until 150–250 total. Use NCT-ID sort order, not wall-clock
   `random` — determinism is the point.
6. Implement `write_cohort_manifest(trials, path)` — serialize to `data/cohort.json` with sorted
   keys, `indent=2`, trailing newline. Formatting is part of the determinism contract, not cosmetic.
7. Run selection twice back-to-back; `diff` the two output files; confirm zero diff.
8. Save a stratification summary (counts by sponsor class × phase) alongside the manifest, and
   sanity-check it against S0-04's pool-size numbers.
9. Write `tests/registry/test_cohort.py`: fixed mocked candidate list → deterministic NCT ID list
   across two runs, and the per-sponsor cap is respected.

**Done when:** `data/cohort.json` has 150–250 NCT IDs, two consecutive runs diff to nothing, and
the stratification summary sits next to it.

---

## S1-04 — Registry snapshot (1.5h)

1. Create `registry/snapshot.py`. For each cohort NCT ID, fetch the **full** record (no field
   restriction — gold extraction in S1-05 spans many modules) plus `get_history(nct_id)`.
2. Fetch version snapshots via `get_history_version` for version 0 (first-posted) and the latest
   version at minimum — document explicitly whether you fetch every intermediate version or just
   the endpoints, since that choice bounds cost.
3. Write to `data/registry_snapshots/{nct_id}/current.json`, `history.json`,
   `versions/{version}.json` — skip re-fetch if the file already exists, unless `--force` is passed.
4. Record `data/registry_snapshots/manifest.json`: NCT ID, fetch timestamp (UTC), source URL, and a
   SHA-256 hash of the raw response body per file — the archive's own integrity record.
5. Run across the full cohort; log failures (e.g. `HistoryEndpointUnavailable`) to
   `data/registry_snapshots/fetch_errors.log` instead of aborting the run — must be resumable.
6. Spot-check 3 snapshots by hand against the live site — last chance to confirm the archive
   matches what's currently posted before the registry can drift further.
7. Add a short README at the top of `data/registry_snapshots/` stating this directory is the frozen
   gold source and all downstream extraction must read from here, never re-fetch live — this is the
   direct fix for the "Registry data mutates mid-project" risk in `sprint_plan.md`'s risk register.

**Done when:** every cohort NCT ID has `current.json` + `history.json` + a hashed manifest row, and
`fetch_errors.log` is empty or every entry is explained.

---

## S1-05 — Registry fact extraction → Postgres (3h)

Requires Postgres + pgvector from S0-06 (`protocol_drift_dev`). Reads only from
`data/registry_snapshots/`, never live, per S1-04.

1. Write `db/schema.sql`: `trials` (nct_id PK, brief_title, condition, phase, sponsor_class,
   sponsor_name, overall_status, start_date, primary_completion_date, has_protocol, has_sap),
   `outcomes` (id PK, nct_id FK, kind, source [`registered_first`/`registered_current`/
   `results_reported`], measure, timeframe, description, version), `arms` (id PK, nct_id FK,
   arm_label, arm_type, description), `eligibility` (nct_id PK/FK, min_age, max_age, sex,
   criteria_text), `amendments` (id PK, nct_id FK, version, date, modules_changed text[]).
2. Apply the schema to `protocol_drift_dev` (`psql -f db/schema.sql`), with `CREATE TABLE IF NOT
   EXISTS` so it's safely re-runnable.
3. Implement `extract_trial_row(snapshot) -> dict` using the confirmed paths in
   `field_paths.md` §1/§7.
4. Implement `extract_outcomes(snapshot_current, snapshot_v0, results) -> list[dict]` — three
   sources per the three-way comparison design: current
   `protocolSection.outcomesModule.primaryOutcomes[]`, first-posted from the v0 snapshot at the
   same path, results-reported from `resultsSection.outcomeMeasuresModule.outcomeMeasures[]`
   filtered to `type == "PRIMARY"` (paths confirmed in `field_paths.md` §2/§3/§5).
5. Implement `extract_arms` / `extract_eligibility` from `armsInterventionsModule` /
   `eligibilityModule`.
6. Implement `extract_amendments(history) -> list[dict]` from `changes[]`
   (`version`, `date`, `moduleLabels`) per the confirmed history-endpoint shape (`field_paths.md` §4).
7. Implement `load_cohort_into_db(cohort_manifest, snapshot_dir, conn)` — iterate the cohort, run
   the `extract_*` functions, bulk-insert via `psycopg`.
8. Run against the full frozen cohort; `SELECT count(*) FROM trials` must equal cohort size.
9. Write sanity SQL: every `trials.nct_id` has ≥1 `outcomes` row with
   `source='registered_current'`; confirm the FK constraint on `outcomes.nct_id` is actually
   declared (not just assumed).
10. Write `tests/registry/test_extract.py` against a real fixture — copy `scratch/NCT02872116_full.json`
    into `tests/fixtures/` and assert hand-checked expected values for each `extract_*` function.

**Done when:** `trials`, `outcomes`, `arms`, `eligibility`, `amendments` are populated for the full
cohort, row counts match expectations, and the fixture-based extraction tests pass.

---

## S1-06 — PDF downloader (2h)

Formalizes `scratch/download_pdfs.py`, using the confirmed CDN URL pattern from
`field_paths.md` §1.

1. Create `corpus/download.py`; implement `document_urls(nct_id, snapshot) -> list[DocumentRef]`
   reading `documentSection.largeDocumentModule.largeDocs[]` and building
   `https://cdn.clinicaltrials.gov/large-docs/{last2digits}/{nct_id}/{filename}`, tagged by doc
   type (protocol/sap/other) from `hasProtocol`/`hasSap`/`label`.
2. Implement `download_document(ref, dest_dir) -> Path` — streamed download to
   `data/pdfs/{nct_id}/{nct_id}_{doctype}.pdf`; skip if the destination exists and its size matches
   `Content-Length` (resumable, idempotent).
3. Reuse S1-02's retry/backoff helper and the same ~0.5s politeness sleep, configurable.
4. Implement `download_cohort(cohort_manifest, snapshot_dir, dest_dir) -> DownloadReport` — iterate
   every cohort NCT ID, collect per-file success/failure/size.
5. Log failures (404, timeout) to `data/pdfs/download_errors.log` with NCT ID + URL; don't abort.
6. Write `data/pdfs/manifest.json`: nct_id, doc_type, filename, url, local path, byte size,
   SHA-256 hash, download timestamp — same integrity pattern as S1-04.
7. Run across the full cohort; confirm total file count is plausible (roughly cohort_size × 1.5,
   matching S0-02's observation that many trials have both protocol and SAP).
8. Re-run the whole command a second time; confirm near-instant completion (all skipped) and an
   unchanged manifest — proves resumability.
9. Write `tests/corpus/test_download.py`: `document_urls` against a fixture snapshot asserts the
   exact CDN URL pattern; a mocked-HTTP test covers skip-if-exists.

**Done when:** `data/pdfs/manifest.json` covers every cohort trial with ≥1 document,
`download_errors.log` is empty or fully explained, and a second run is a no-op.

---

## S1-07 — Page classifier (2h)

Ports the *refined* heuristic from `scratch/check_text_layer.py` — S0-03 explicitly found the naive
low-text-length heuristic overstated the scan rate (9/34 → 4/34) until image-presence was added as
a second condition. Use the refined version, not the first pass.

1. Port the rule into `corpus/classify.py::classify_page(page) -> PageClass`
   (`BORN_DIGITAL` / `SCANNED` / `BLANK_OR_VECTOR`): a page counts as `SCANNED` only if it has no
   extractable text **and** carries a raster image; vector-only or empty pages are excluded.
2. Implement `classify_document(pdf_path) -> DocumentClassification` — runs `classify_page` over
   **every** page (S0-03 used a 3-page sample; run the full document here), rolls up to
   born_digital / mixed / scanned per the S0-03-validated thresholding.
3. Run over the full downloaded corpus; write `data/corpus_classification.json` — per-document page
   counts by class, plus a flat per-page table (feeds `is_ocr` in S2-09 later).
4. Cross-check the full-cohort aggregate against S0-03's sample numbers (1.1% page-level scanned,
   88% born-digital documents); flag in the output if the full-cohort rate diverges >2x from the
   sample — that would be a retro-worthy surprise.
5. Write `tests/corpus/test_classify.py` using real fixtures: copy one known-mixed doc
   (`NCT04311632` SAP) and one clean born-digital doc from `scratch/pdfs/` into
   `tests/fixtures/pdfs/`; assert expected classification.

**Done when:** `data/corpus_classification.json` covers every PDF in the corpus, and the full-cohort
scanned-page rate is computed — this number feeds S1-09 and the S1-G1 gate directly.

---

## S1-08 — Trace store (2.5h)

Independent of the registry work above — only needs Postgres. Every model/retrieval call from
Sprint 2 onward routes through this.

1. Write `trace/schema.sql`: `query` (id, text, tier, created_at), `retrieval_step` (id, query_id
   FK, stage, rank, chunk_id, score, latency_ms), `chunk_hit` (id, retrieval_step_id FK, chunk_id,
   nct_id, doc_type, section, page_range), `generation` (id, query_id FK, model_digest,
   prompt_hash, response_text, latency_ms, token_count), `cost_record` (id, generation_id FK,
   tokens_in, tokens_out, wall_clock_ms) — column set matches `sprint_plan.md`'s S1-08 spec exactly.
2. Apply the schema to `protocol_drift_dev`; decide whether it shares the default schema/namespace
   with S1-05's tables or lives under `trace.*`, and note the choice in the file.
3. Implement `TraceStore` in `trace/store.py`: `log_query`, `log_retrieval_step`, `log_generation`,
   `log_cost` — parameterized INSERTs, each returning the new row's ID for FK chaining.
4. Implement `traced_call(store, stage)` as a context manager/decorator that times any wrapped call
   and writes a row automatically — the hook every future model/retrieval call will use, per the
   "every model call routes through a traced client" acceptance criterion.
5. Since no real model calls exist yet, write a synthetic smoke test: log one fake
   query → retrieval_step → generation → cost_record, read it back with a JOIN, assert referential
   integrity end-to-end.
6. Write `tests/trace/test_store.py`: each `log_*` inserts and returns a valid ID; `traced_call`
   measures latency within tolerance of a `time.sleep(0.1)` test function.
7. Document the `prompt_hash` strategy now (e.g. `sha256(model_digest + prompt_text)`) in a module
   docstring, even though nothing hashes yet — Sprint 3's "cache everything" requirement
   (`sprint_plan.md` appendix) needs a stable contract to build against.

**Done when:** all 5 trace tables exist, `TraceStore` round-trips a synthetic
query→retrieval→generation→cost chain, and `traced_call` correctly measures latency.

---

## S1-09 — Corpus stats report (1.5h)

1. Create `scripts/corpus_report.py` — one of the "tables and figures regenerate via `scripts/`"
   items from the project's Definition of Done.
2. Pull only from frozen artifacts: `data/cohort.json`, `data/pdfs/manifest.json`,
   `data/corpus_classification.json` — no live queries.
3. Compute: total trials, total documents, total pages, page-count distribution (min/median/max +
   histogram buckets), scanned-page % at both page-level and document-level (mirroring S0-03's
   two-tier reporting), doc-type breakdown, and the sponsor-class × phase stratification table
   (reuse from S1-03).
4. Render to `docs/corpus.md` as plain Markdown (string templating is enough).
5. Include a short "vs. Sprint 0 sample" section citing the S0-03 numbers (1.1% page-level, 88%
   document-level born-digital) next to the full-cohort numbers, so any drift between the 20-doc
   sample and the real 150–250 cohort is visible and explained, not silently overwritten.
6. State the S1-G1 gate outcome explicitly: which bracket (<15% / 15–40% / >40%) the measured rate
   falls into and what that implies for Sprint 2.
7. Run the script; commit `docs/corpus.md`.
8. Add a `make corpus-report` target so the report is a documented, repeatable command (this is the
   seed of S6-02's later `make reproduce`).

**Done when:** `docs/corpus.md` states a concrete scanned-page rate and explicitly names the S1-G1
gate bracket it falls into.

---

## S1-10 — Tests: cohort determinism, trace integrity (1h)

Consolidation and CI-hardening pass over the unit tests already written inline in S1-03 and S1-08 —
this task promotes them to a stronger, CI-enforced guarantee rather than duplicating their content.

1. Strengthen the S1-03 determinism test into an end-to-end version: run the actual cohort
   selection entrypoint against a fixed mocked candidate pool twice, as separate invocations, and
   diff the two `data/cohort.json` files byte-for-byte — catches formatting/ordering bugs an
   in-process comparison would miss.
2. Add a dedicated test for `write_cohort_manifest`'s JSON formatting (key order, indentation) so a
   future refactor can't silently reintroduce non-determinism via dict ordering.
3. Extend `tests/trace/test_store.py` with a referential-integrity test: insert a `retrieval_step`
   referencing a nonexistent `query_id` and assert the FK constraint rejects it — proves the
   constraint is actually wired, not just assumed.
4. Add a concurrent-write smoke test for the trace store: fire several `log_query` calls in
   parallel and assert all rows land with distinct IDs and no lost writes — cheap insurance before
   Sprint 3's eval loop hammers this store.
5. Confirm both test modules run automatically in CI (pytest auto-discovers `tests/`) and show up
   as passing, not skipped, in the CI log.
6. Register the `integration` marker in `pyproject.toml` so `pytest -m "not integration"` is the
   literal CI invocation and live-network tests never run unattended.

**Done when:** cohort determinism and trace referential-integrity tests are green in CI, and
integration-marked tests are excluded from CI by config, not convention.

---

## Sprint 1 acceptance criteria

- `data/cohort.json` is frozen; re-running selection produces byte-identical output
- Raw registry JSON is archived per trial — the live registry mutates, unarchived gold is
  unreproducible gold
- `docs/corpus.md` reports the scanned-page rate at both page- and document-level
- Every future model call routes through `TraceStore`/`traced_call` (nothing to trace yet, but the
  path exists and is tested)

---

## 🚧 GATE S1-G1 — Scanned-page rate

Check `docs/corpus.md` (S1-09 output) before starting Sprint 2.

| Rate | Action |
|---|---|
| **< 15%** | Proceed. OCR is a footnote. |
| **15–40%** | Proceed, but budget S2-03 fully and report OCR'd content rate in every results table. |
| **> 40%** | **Re-select the cohort**, biasing toward sponsors with born-digital submissions. Do not let OCR become the project. |

S0-03's 20-document sample measured **1.1% page-level scanned**, comfortably in the `<15%` bracket —
the full-cohort number should land near this, but confirm it, don't assume it.

---

## Sprint 1 exit checklist

- [ ] `data/cohort.json` — 150–250 NCT IDs, deterministic (two runs diff to nothing)
- [ ] `data/registry_snapshots/` — full record + history archived per trial, hashed manifest
- [ ] Postgres `trials`, `outcomes`, `arms`, `eligibility`, `amendments` populated for the full cohort
- [ ] `data/pdfs/` — protocol/SAP PDFs downloaded per trial, hashed manifest, resumable
- [ ] `data/corpus_classification.json` — every PDF classified born-digital/mixed/scanned
- [ ] Trace store schema live in Postgres; `TraceStore` round-trips a synthetic trace chain
- [ ] `docs/corpus.md` — committed, states scanned-page rate and the S1-G1 bracket
- [ ] CI green: ruff + mypy + pytest (integration tests excluded) on every push

Do not start Sprint 2's ingestion work (S2-01) until every box above is checked and the S1-G1 gate
outcome is recorded.
