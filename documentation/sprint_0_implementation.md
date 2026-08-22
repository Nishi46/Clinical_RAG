# Sprint 0 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 0 (Reconnaissance, ~8 hrs). Companion to `sprint_plan.md`. Each step is small enough to check off individually. Scratch scripts here live in `scratch/` and are **not** part of the frozen pipeline — Sprint 1 rebuilds the real versions properly.

---

## S0-01 — Explore API v2 by hand (2h)

1. Create `scratch/` directory (gitignored) for throwaway exploration scripts.
2. `curl` the base endpoint with no params: `curl "https://clinicaltrials.gov/api/v2/studies?pageSize=1"` → save raw response to `scratch/sample_study.json`.
3. Pretty-print `sample_study.json` and list every top-level key (`protocolSection`, `resultsSection`, `hasResults`, etc.).
4. Write `scratch/explore_api.py` with a single function `fetch_study(nct_id: str) -> dict` using `requests`.
5. Call `fetch_study` on one known trial with amendments (search manually on clinicaltrials.gov UI first to find one) and dump to `scratch/sample_study_with_amendments.json`.
6. In a scratch REPL/notebook, walk `protocolSection` and print every nested module key one level deep — write the list into a comment block at the top of `explore_api.py`.
7. Locate `hasProtocol` and `hasSap` in the dumped JSON — print their exact dotted path (e.g., `protocolSection.identificationModule...` or wherever they actually live — don't assume).
8. Locate the results-reported primary outcome field inside `resultsSection.outcomeMeasuresModule` — print its exact path and one example value.
9. Find the version/amendment-history mechanism: check whether it's a separate endpoint (`/studies/{nct_id}/history` or similar) or embedded field — test both by hitting the API docs' listed endpoint and confirming a non-empty response on the amended trial from step 5.
10. Add a `fetch_history(nct_id: str) -> dict` function to `explore_api.py` once the mechanism is confirmed.
11. Test `pageSize` + `pageToken` pagination: fetch page 1 with `pageSize=10`, grab `nextPageToken`, fetch page 2, confirm no overlap in NCT IDs.
12. Test field-selection query param (`fields=NCTId,BriefTitle,...` or the v2 equivalent) — confirm the response payload shrinks accordingly.
13. Write `scratch/field_paths.md` recording the exact JSON paths for: registered primary outcome, first-posted primary outcome (if distinct), results-reported primary outcome, amendment/version history. These paths are inputs to S1-05 — get them right now to avoid rework.

**Done when:** `field_paths.md` exists with 4 confirmed paths, each with one real example value.

---

## S0-02 — Download ~20 protocol/SAP PDFs (1.5h)

1. Write `scratch/sample_cohort.py`: query `/studies` with params `filter.overallStatus=COMPLETED`, `filter.advanced` for `AREA[HasResults]true` (or equivalent), no therapeutic-area filter yet.
2. From the response, filter client-side to records where `hasProtocol == true or hasSap == true`.
3. Dedupe by sponsor (`leadSponsor.name` or equivalent field) so no more than 2–3 trials share a sponsor — target ≥6 distinct sponsors across the sample.
4. Cap the list at 20 NCT IDs; write them to `scratch/sample_nct_ids.txt`.
5. For each NCT ID, pull the document URLs — check the module that lists `largeDocs` (or equivalent) inside `protocolSection` / `documentSection`, extract `filename`/`url` per document, and note `label`/`type` (protocol vs. SAP vs. combined) if present.
6. Write `scratch/download_pdfs.py`: loop over the 20 NCT IDs, download each PDF via `requests.get(url, stream=True)`, save to `scratch/pdfs/{nct_id}_{doctype}.pdf`, add a `time.sleep(0.5)` between requests to be polite.
7. Run the script; confirm 20+ files landed in `scratch/pdfs/` (some trials may have both protocol and SAP — that's fine, more data points).
8. Manually open each PDF (Preview/any viewer) and skim first + last 2 pages — jot one-line first impressions into `scratch/pdf_notes.md` (e.g., "NCT0012345 — scanned, ~180pp, protocol only").

**Done when:** `scratch/pdfs/` has ≥20 files from ≥6 sponsors, and `pdf_notes.md` has one line per file.

---

## S0-03 — Manual document assessment (2h)

1. Write `scratch/check_text_layer.py`: for each PDF, open with `pymupdf` (`fitz.open(path)`), extract text from page 1 and page middle via `page.get_text()`, flag `born_digital` if extracted text length > some threshold (e.g., >200 chars), else flag `scanned_or_empty`.
2. Run it across all 20 PDFs; if a document has some `born_digital` pages and some `scanned_or_empty` pages, tag it `mixed`.
3. Output a table (print or CSV) with columns: `nct_id, doc_type, pages, classification`.
4. Compute and print the aggregate percentage: `% born_digital`, `% scanned`, `% mixed`.
5. For each PDF, open manually and check the table of contents or first section heading — record whether it's a standalone protocol, standalone SAP, or combined Protocol+SAP (section numbering restart is the tell) into the CSV as an extra column `combined_doc: bool`.
6. Pick 5–6 PDFs spanning different sponsors; manually list their top-level section headings into `scratch/section_headings.md` (one sub-list per sponsor).
7. Diff the section-heading lists by eye — write down 3–5 concrete examples where the same concept has a different literal heading across sponsors (e.g., "Study Objectives" vs. "Purpose and Objectives").
8. Open at least one PDF you flagged as long/dense and manually locate an assessment-schedule table (usually in "Study Procedures" or "Schedule of Assessments" section); screenshot it and save to `scratch/assessment_schedule_example.png` — note in a caption whether it spans multiple pages.
9. Scan all 20 for any visible redaction (black boxes, "page intentionally removed," missing page numbers in sequence) — log any hits in the CSV as `has_redaction: bool`.
10. Consolidate steps 3–9 into `scratch/corpus_assessment.md`: scanned-page rate, combined-doc rate, section-heading inconsistency examples, assessment-schedule confirmation, redaction count. This file is the direct precursor to `docs/corpus.md` in Sprint 1.

**Done when:** `corpus_assessment.md` states a concrete scanned-page percentage and lists ≥3 real section-heading mismatches.

---

## S0-04 — Pick therapeutic area (0.5h)

1. Re-run `sample_cohort.py` with an added condition filter for oncology (e.g., `query.cond=cancer` or `AREA[ConditionSearch]`) plus the existing `hasProtocol/hasSap` + results-posted + `filter.startDate >= 2017-01-01` — record the total count returned.
2. Repeat with a cardiovascular condition filter (e.g., `query.cond=cardiovascular`) — record the total count.
3. Confirm both pools are ≥150–250 after filters; if either is short, loosen the results-posted requirement slightly and re-check (but log that you did this).
4. Cross-tab against `scratch/pdf_notes.md`/`corpus_assessment.md`: if any of the 20 sampled PDFs happen to fall in oncology or cardio, note whether their scan-rate/section-consistency looked better or worse than the full-20 average.
5. Make the call (oncology or cardiovascular) and write one paragraph in `scratch/therapeutic_area_decision.md`: pool size, any signal from the sample, and the final choice.

**Done when:** `therapeutic_area_decision.md` names one area with a stated pool size ≥150.

---

## S0-05 — Outcome-switching literature + discrepancy definition (1.5h)

1. Fetch and read the PMC4032105 paper in full.
2. Extract its operational definition of "outcome change" — copy the exact criteria they used (e.g., comparing first-registered vs. final-registered vs. published) into `scratch/outcome_switching_notes.md`.
3. Follow 1–2 of its own citations if readily available (skim only, not full read) to see if other papers use a stricter/looser definition — note any variance.
4. Draft a decision table in `documentation/discrepancy_definition.md` (this one **is** a real deliverable, not scratch) with columns: `comparison pair` (e.g., first-posted registry vs. protocol text; first-posted vs. current registry; protocol vs. results-reported), `what counts as a match`, `what counts as a divergence`, `what counts as ambiguous/needs human review`.
5. Explicitly write the normalization caveat: semantically equivalent phrasings (e.g., "OS at 24 months" vs "overall survival at 2 years") must not count as a divergence — flag this as the known hard case for later (S4-03).
6. Add one paragraph explicitly stating the ethics stance inline: divergence ≠ misconduct, legitimate reasons exist (amendments, regulatory feedback, safety findings) — this becomes the seed of the README ethics section later.

**Done when:** `documentation/discrepancy_definition.md` is committed with the comparison table and the ethics paragraph.

---

## S0-06 — Local environment setup (0.5h)

1. Install Postgres (`brew install postgresql@16` on Apple Silicon, or confirm existing install) and start it (`brew services start postgresql@16`).
2. Create a scratch database: `createdb protocol_drift_dev`.
3. Install pgvector: `brew install pgvector` (or build from source if unavailable), then in `psql protocol_drift_dev`: `CREATE EXTENSION vector;` — confirm no error.
4. Smoke-test pgvector: `CREATE TABLE vtest (id serial, embedding vector(3)); INSERT INTO vtest (embedding) VALUES ('[1,2,3]'); SELECT * FROM vtest;` then `DROP TABLE vtest;`.
5. Install Ollama (`brew install ollama`) if not already present; start the service (`ollama serve` or via the app).
6. Pull the target generation model, e.g. `ollama pull llama3.1:8b-instruct-q4_K_M` (or whichever 8B Q4 model is chosen) — after pulling, run `ollama show llama3.1:8b-instruct-q4_K_M --modelfile` or `ollama list` to get the digest.
7. Record the exact model name + digest in `configs/models.yaml` (create this file now — it'll be reused/extended in later sprints).
8. Smoke-test generation: `ollama run <model> "Say OK if you're working."` — confirm a sane response.
9. Confirm `sentence-transformers` and `pymupdf` install cleanly in a local Python env (`pip install sentence-transformers pymupdf`) — this validates the embedding/parsing stack will work before Sprint 1 depends on it.

**Done when:** pgvector smoke test passes, Ollama returns a response, and `configs/models.yaml` has one pinned digest.

---

## Sprint 0 exit checklist

- [ ] `scratch/field_paths.md` — 4 confirmed API field paths with example values
- [ ] `scratch/corpus_assessment.md` — scanned-page rate + section-heading mismatch examples
- [ ] `scratch/therapeutic_area_decision.md` — chosen area + pool size
- [ ] `documentation/discrepancy_definition.md` — comparison table + ethics paragraph (real deliverable, committed)
- [ ] `configs/models.yaml` — pinned model digest(s)
- [ ] Postgres + pgvector + Ollama all smoke-tested locally

Do not start Sprint 1's repo scaffold (S1-01) until every box above is checked.
