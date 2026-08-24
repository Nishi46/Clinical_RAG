# Sprint 2 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 2 (Ingestion & Parsing / E2, ~25h). Companion to
`sprint_plan.md`. Each step is small enough to check off individually. This is the project's
technical differentiator — read from the frozen artifacts Sprint 1 produced
(`data/cohort.json`, `data/pdfs/manifest.json`, `data/corpus_classification.json`,
Postgres `trials`/`outcomes`/`arms`/`eligibility`/`amendments`), never re-fetch or re-derive them.

**Sprint 1 is closed and gate S1-G1 has already resolved**: measured page-level scanned rate
**2.69%**, comfortably in the `<15%` bracket (`docs/corpus.md`). Per S1-G1's own guidance at that
bracket — *"Proceed. OCR is a footnote."* — and the project's cut-list (`sprint_plan.md`, item 4:
*"S2-03 OCR fallback (if scan rate is low, just exclude those pages and report it)"*) — S2-03 below
is scoped down accordingly: exclude and report scanned pages by default, real OCR only behind an
explicit flag. Don't build a full OCR pipeline for 2.69% of pages; that's solving a problem this
corpus doesn't have.

Two other Sprint 0/1 findings shape scope here and are referenced throughout:
- `scratch/corpus_assessment.md` — section-header naming and position vary by sponsor (rules out a
  fixed lookup table for S2-04), PDF bookmarks exist on only ~half the sample (can't be the primary
  section signal), a confirmed multi-page assessment-schedule table with merged cells and
  cross-references (`NCT02872116` Table 5.1-2/5.1-3), a confirmed redaction, and a document-depth
  split (some "protocols" are 2–3 page summaries with no assessment table at all).
- `data/pdfs/manifest.json` has three `doc_type` values: `protocol`, `sap`, `icf`. Per
  `scratch/pdf_notes.md`'s finding, ICFs describe participant consent, not trial design or
  outcomes — they are excluded from the ingestion corpus starting at S2-01, not silently carried
  through.

**Do not start S2-01 until every box on the Sprint 1 exit checklist is checked.**

---

## S2-01 — Text extraction pipeline (2.5h)

1. Create `src/protocol_drift/ingestion/extract.py`. Implement
   `document_pdfs(pdf_manifest, corpus_classification) -> Iterator[DocumentRef]` that reads
   `data/pdfs/manifest.json`, filters to `doc_type in {"protocol", "sap"}` (drop `icf` — see
   scope note above), and joins in the per-document classification from
   `data/corpus_classification.json` so downstream code knows a document's `mixed`/`scanned` pages
   up front.
2. Implement `extract_page(page: fitz.Page) -> PageContent` using PyMuPDF's layout-aware extraction
   (`page.get_text("dict")` or `"blocks"`, not plain `"text"`) — preserve block order top-to-bottom,
   left-to-right per column, and keep each block's bounding box for later table/section work.
3. Implement `extract_document(pdf_path, page_classes) -> DocumentContent` — runs `extract_page`
   over every page, tags each page with its classification from S1-07's per-page output
   (`born_digital` / `scanned` / `blank_or_vector`), and skips true `scanned` pages by default
   (content is `None`, `needs_ocr=True`) rather than emitting garbage from a text-less page.
4. Add a redaction flag: a page whose extracted content is dominated by one or more large filled
   black rectangles (`page.get_drawings()`, filter to solid fills covering >5% of page area) is
   marked `has_redaction=True` on the page record. This is detection only — confirmed present at
   least once in `corpus_assessment.md` §6/§8; don't attempt redaction *reconstruction*.
5. Write extracted output to `data/extracted/{nct_id}/{doc_type}.json`: ordered list of pages, each
   with blocks (text + bbox), page class, `needs_ocr`, `has_redaction`. Skip re-extraction if the
   destination file exists and matches the source PDF's SHA-256 from the pdf manifest, unless
   `--force` — same resumability contract as S1-04/S1-06.
6. Implement `extract_corpus(...) -> ExtractionReport` iterating every non-ICF document in the
   cohort; log failures (corrupt PDF, e.g. the known `NCT03081858` SAP xref issue from S1-07) to
   `data/extracted/extraction_errors.log` instead of aborting.
7. Run across the full corpus (protocol + sap only, ~277 of the 313 downloaded docs once ICF is
   excluded). Confirm the error log only contains the already-known malformed-PDF case(s).
8. Write `tests/ingestion/test_extract.py` against a real fixture (reuse
   `scratch/pdfs/NCT02872116_protocol.pdf` or add it to `tests/fixtures/pdfs/`): assert reading
   order is preserved across a known two-column or table-heavy page, and that a known scanned page
   (from `NCT04311632`'s SAP, per `corpus_assessment.md` §1) comes back with `needs_ocr=True` and
   empty content rather than throwing.

**Done when:** `data/extracted/` has one JSON per non-ICF cohort document, reading order is visibly
correct on a spot-checked multi-column page, and every truly scanned page is marked `needs_ocr`
instead of silently producing empty/garbled text.

---

## S2-02 — Naive baseline chunker (1h)

Your "before" picture — S2-10's side-by-side comparison needs this to exist first and needs it to
stay naive; do not let it grow structure-awareness by accident.

1. Create `src/protocol_drift/ingestion/chunk_naive.py`. Implement
   `naive_chunk(document_content, chunk_tokens=512, overlap_tokens=0) -> list[NaiveChunk]` — flatten
   all page blocks into one text stream per document (protocol and SAP chunked separately; never
   concatenate across `doc_type`) and cut fixed-size token windows using a simple whitespace/BPE
   token count, no section or table awareness at all.
2. Each `NaiveChunk` carries only the minimum: `nct_id`, `doc_type`, `chunk_index`, `text`,
   approximate `page_range` (derived from cumulative block lengths, not authoritative — this
   chunker doesn't try to get it right).
3. Write to `data/chunks_naive/{nct_id}/{doc_type}.jsonl`, one chunk per line.
4. Run across the full extracted corpus.
5. Write `tests/ingestion/test_chunk_naive.py`: fixed input text of known length produces the
   expected chunk count at a given `chunk_tokens`; confirm it happily splits mid-table on a fixture
   built from the known `NCT02872116` assessment-schedule page (S2-10 needs this exact failure to
   show side-by-side against the section-aware chunker).

**Done when:** `data/chunks_naive/` covers the full corpus and demonstrably splits at least one
known table mid-structure — that failure is the point of this task.

---

## S2-03 — OCR fallback for scanned pages (1h, scoped down — see sprint scope note)

Given the measured 2.69% page-level scanned rate, this is deliberately minimal per S1-G1's own
guidance and the project's cut list. Do not build a queueing/batch OCR system for ~500 pages.

1. In `src/protocol_drift/ingestion/ocr.py`, implement `pages_needing_ocr(extracted_dir) -> list[PageRef]`
   — scan `data/extracted/**/*.json` for `needs_ocr=True` pages and write
   `data/ocr_backlog.json` (nct_id, doc_type, page number, reason).
2. By default, S2-04 through S2-08 treat a `needs_ocr` page as absent content and log it (skip +
   report), matching the cut-list's own phrasing exactly. This is the shipped behavior.
3. Implement `ocr_page(page, lang="eng") -> str` behind a `--with-ocr` flag on the extraction
   pipeline, using a local OCR engine (`pytesseract` + system `tesseract`, or PyMuPDF's built-in
   `Page.get_textpage_ocr` if the local Tesseract install is wired to it) — add `pytesseract` as an
   optional dependency group (`ocr`), not a core dependency, since the default path never needs it.
4. Re-run extraction with `--with-ocr` once, only to confirm the fallback works end-to-end on the
   known scanned page in `NCT04311632`'s SAP; do not run it across the full backlog unless a later
   sprint's failure analysis specifically asks for OCR'd content.
5. Write `tests/ingestion/test_ocr.py` with `pytesseract` mocked — assert `ocr_page` is only invoked
   for pages flagged `needs_ocr`, and that the default (non-`--with-ocr`) path never imports
   `pytesseract` at all (keeps the core install light).

**Done when:** `data/ocr_backlog.json` enumerates every scanned page with its source, the flagged
default path (skip + report) works end-to-end without the `ocr` extra installed, and `--with-ocr`
is proven on at least one real scanned page.

---

## S2-04 — Section segmentation (4h)

**Timebox this. 80% detection with logged failures beats 95% and a blown sprint** — the failure
list is itself analysis material for `docs/ingestion.md`. Per `corpus_assessment.md` §3/§5: section
names and positions vary by sponsor and PDF bookmarks are present on only ~half the corpus, so this
cannot be a fixed lookup table or a bookmark-only approach.

1. Create `src/protocol_drift/ingestion/sections.py`. Define a canonical section taxonomy covering
   the concepts actually seen varying by name in `corpus_assessment.md` §3: `synopsis`,
   `background`, `objectives`, `eligibility`, `study_design`, `interventions`,
   `assessment_schedule`, `statistics`, `ethics`, `administrative`, plus an `unclassified` bucket —
   keep this list short and driven by what S4 (discrepancy detection) and S3 (eligibility/outcome
   retrieval) actually need, not exhaustive ICH-M11 coverage.
2. Build a keyword/regex pattern library per canonical section, seeded from the exact heading
   variants already observed across the three sponsors in `corpus_assessment.md` §3 (e.g.
   `"Ethical Considerations"` / `"ETHICAL CONSIDERATIONS"` all mapping to `ethics`); match against
   heading-candidate blocks (short text, larger/bold font run if font metadata is available from
   S2-01's block extraction) rather than the whole page.
3. Implement a bookmark-assisted path: if `fitz.get_toc()` is non-empty for a document (per
   `corpus_assessment.md` §5, roughly half the corpus), use it as a first-pass section boundary
   signal and confirm/relabel each bookmark title against the canonical taxonomy's regex library —
   don't trust the bookmark title text verbatim as the canonical label.
4. Implement `segment_document(document_content) -> list[Section]` combining both signals: each
   `Section` has `label` (canonical, or `unclassified`), `raw_heading_text`, `page_range`,
   `detection_method` (`bookmark` / `regex` / `unmatched`).
5. Implement the fallback path for documents with no confident section boundaries at all (expect
   this on the thin 2–3 page academic summaries flagged in `corpus_assessment.md` §4): the whole
   document becomes one `unclassified` section rather than the pipeline erroring out.
6. Write to `data/sections/{nct_id}/{doc_type}.json`.
7. Run across the full corpus; compute section-detection rate = fraction of documents with ≥1
   non-`unclassified` section. Log every fully-`unclassified` document with its sponsor
   (`sponsor_name`/`sponsor_class` from the Postgres `trials` table) to
   `data/sections/detection_failures.log` — sponsor attribution is required per the acceptance
   criteria, not optional.
8. Spot-check against the three sponsors profiled in `corpus_assessment.md` §3
   (`NCT02798211`, `NCT02872116`, `NCT02485938`) by hand — confirm each maps its sponsor-specific
   heading text to the correct canonical label.
9. Write `tests/ingestion/test_sections.py` against real fixtures from those three documents:
   assert each maps its known sponsor-specific "ethics" heading variant to canonical `ethics`.

**Done when:** section detection succeeds (≥1 non-`unclassified` section) on ≥80% of documents,
`data/sections/detection_failures.log` names every failure with its sponsor, and the three
hand-profiled sponsor documents map correctly.

---

## S2-05 — Table extraction with header propagation (4h)

**Timebox this alongside S2-04** — both are called out by name in `sprint_plan.md` as sprint-eaters.
Use PyMuPDF's built-in `page.find_tables()` (already a pinned dependency — no new heavy deps like
Camelot/Ghostscript needed) as the extraction primitive; layer header propagation on top.

1. Create `src/protocol_drift/ingestion/tables.py`. Implement `extract_page_tables(page) -> list[RawTable]`
   using `page.find_tables()`, keeping each table's cell grid, bounding box, and page number.
2. Implement header propagation: for a table whose first row(s) don't repeat their header text (a
   continuation page of a multi-page table — the case S2-06 formalizes), carry forward the
   originating table's column headers so every extracted row is self-describing on its own without
   needing adjacent pages in context.
3. Implement caption-line attachment: search the text immediately preceding a table's bounding box
   for a caption pattern (e.g. `"Table 5.1-2"`) via `extract_document`'s page blocks from S2-01, and
   attach it as `table.caption`. Confirmed real example to test against: `NCT02872116`'s Table
   5.1-2/5.1-3 (`corpus_assessment.md` §6).
4. Implement unit-carrying: when a header cell contains a parenthetical unit (`"Weight (kg)"`),
   preserve the unit alongside the column header rather than dropping it during grid flattening —
   this matters later for S4-03's outcome normalization, which needs units intact at the source.
5. Handle merged/spanning cells (confirmed present — the "See Note" cells in
   `corpus_assessment.md` §6): represent a spanned cell's value once with an explicit `colspan`/
   `rowspan`, not duplicated blindly across the cells `find_tables()` reports it into.
6. Handle cross-reference text inside cells (`"See Table 5.5-1"`, `"see Section 4.5.1.6"`): preserve
   as plain text — per the confirmed finding that hyperlink targets don't survive extraction anyway,
   don't attempt to resolve them here.
7. Write to `data/tables/{nct_id}/{doc_type}.json`: list of `RawTable` with caption, headers (with
   units), rows, page range, source section (join against S2-04's `data/sections/` output by page
   overlap).
8. Run across the full corpus; write `data/tables/extraction_failures.log` for pages where
   `find_tables()` finds a visual grid PyMuPDF can't parse (expect some — flag rather than crash).
9. Write `tests/ingestion/test_tables.py` against the `NCT02872116` Table 5.1-2 fixture: assert
   headers propagate onto continuation rows, the caption attaches correctly, and the known merged
   "See Note" cell round-trips without duplicating its value across every cell it spans.

**Done when:** the `NCT02872116` reference table (headers, caption, merged cells, units) extracts
correctly end-to-end, and `data/tables/` covers the full corpus with failures logged, not silently
dropped.

---

## S2-06 — Assessment-schedule handling (3h)

Builds directly on S2-05; this is the single hardest, most concrete artifact in the corpus
(`corpus_assessment.md` §6 — `NCT02872116` Table 5.1-2 spans pages 84–86, adjacent 5.1-3 spans 5
pages).

1. Create `src/protocol_drift/ingestion/assessment_schedule.py`. Implement
   `is_continuation_table(table_a, table_b) -> bool` — same column-header set (post S2-05
   propagation) and either adjacent pages or an explicit "(continued)" caption marker.
2. Implement `merge_tables(tables: list[RawTable]) -> RawTable` — reassemble a run of continuation
   tables into one logical table spanning the full page range, rows concatenated in page order,
   headers taken once from the first table in the run.
3. Implement `reassemble_document_tables(raw_tables) -> list[RawTable]` — run the merge over every
   table in a document, in page order, collapsing multi-page runs while leaving genuinely
   independent single-page tables untouched.
4. Specifically validate against the confirmed 3-page (Table 5.1-2) and 5-page (Table 5.1-3) runs in
   `NCT02872116` — after reassembly each becomes exactly one logical `RawTable` object with the
   right total row count (cross-check by hand against the source pages).
5. Write reassembled output back into `data/tables/{nct_id}/{doc_type}.json`, replacing the raw
   per-page table list with the merged logical tables (keep the original per-page list too, under a
   `_raw_pages` key, for debugging).
6. Write `tests/ingestion/test_assessment_schedule.py`: the `NCT02872116` fixture reassembles to
   exactly 2 logical tables (5.1-2 and 5.1-3) with the hand-counted row totals from step 4, and an
   unrelated single-page table in the same document is left as-is (not incorrectly merged into a
   neighbor).

**Done when:** the two known multi-page assessment-schedule tables in `NCT02872116` each reassemble
into one correctly-sized logical table, and single-page tables are provably left untouched.

---

## S2-07 — Amendment/version tagging (2.5h)

Distinct from S1-05's `amendments` table (registry-level version history from
`.../history`'s `changes[]`) — this tags the **document's own** version marker, confirmed to appear
in running page footers (`corpus_assessment.md` §6: *"Revised Protocol No.: 09"*), not just at the
document level.

1. Create `src/protocol_drift/ingestion/versioning.py`. Implement
   `extract_page_version_marker(page_text) -> VersionMarker | None` — regex over common patterns
   seen in practice (`"Revised Protocol No.: NN"`, `"Amendment N"`, `"Version N.N"`, dated variants)
   against footer/header text specifically (bottom/top ~10% of page bbox from S2-01's block
   coordinates), not full-page text, to avoid false hits from body text.
2. Implement `document_version_timeline(document_content) -> list[(page_range, VersionMarker)]` —
   run the marker extractor over every page, and collapse consecutive same-version pages into
   ranges; log a per-document warning if version markers are inconsistent or absent across most
   pages (expect this on thinner/academic documents, consistent with `corpus_assessment.md` §4).
3. Cross-reference the extracted marker against S1-05's Postgres `amendments` table for the same
   `nct_id` (registry-reported version numbers/dates) — implement
   `reconcile_with_registry(doc_versions, registry_amendments) -> ReconciliationResult` that flags
   agreement/mismatch/unresolvable per document rather than silently trusting either source alone.
4. Write output to `data/versions/{nct_id}/{doc_type}.json`: page-range → version-marker mapping,
   plus the registry reconciliation result.
5. Mark superseded content only where explicitly detectable — i.e. a page range whose version marker
   is strictly older than the document's max detected version — as `superseded=True` in the version
   record; do not infer supersession from anything softer than an explicit marker mismatch.
6. Spot-check `NCT02872116` by hand: confirm the "Revised Protocol No.: 09" page (from the
   assessment-schedule screenshot) is tagged version 9, and that this is plausible against its
   Postgres `amendments` row count (92 revisions per `field_paths.md`'s example trial).
7. Write `tests/ingestion/test_versioning.py`: a fixture page with a known footer string extracts the
   correct version number; a document with no version markers anywhere degrades to
   `version=None` rather than raising.

**Done when:** page-level version markers are extracted where present, reconciled against the
Postgres `amendments` table per document, and the `NCT02872116` spot-check confirms a page tagged
version 9.

---

## S2-08 — Section-aware chunker (2.5h)

The real chunker — depends on S2-04 (sections) and S2-05/06 (tables). Never splits a table; this is
enforced by a test, not just a docstring promise.

1. Create `src/protocol_drift/ingestion/chunk.py`. Implement
   `chunk_document(document_content, sections, tables, versions) -> list[Chunk]`: walk the document
   in page order, respecting section boundaries from S2-04 as hard chunk breaks (never span two
   canonical sections in one chunk).
2. Within a section, chunk body text to a token budget (reuse S2-02's ~512-token target as the
   default, configurable) at paragraph/block boundaries from S2-01 — never mid-sentence if
   avoidable.
3. Table handling: a reassembled logical table from S2-06 that fits under a raised table-specific
   token ceiling becomes exactly one chunk (`chunk_type="table"`); a table too large even for that
   ceiling still is **not** split — instead splits at row boundaries only, with the propagated
   header (from S2-05) repeated at the top of every resulting chunk, so no chunk ever contains a
   half-row or a headerless row.
4. Every chunk is prefixed with a contextual header string before embedding/storage:
   `"[{nct_id} | {doc_type} v{doc_version} | {section_path}]"`, built from this chunk's `nct_id`,
   `doc_type`, the reconciled version from S2-07 for this page range, and the section path from
   S2-04 — this is what lets a retrieved chunk be self-describing outside its source document.
5. Implement `write_chunks(chunks, dest) -> None` → `data/chunks/{nct_id}/{doc_type}.jsonl`.
6. Run across the full corpus (protocol + sap, excluding ICF per S2-01's scope decision).
7. Write `tests/ingestion/test_chunk.py`: build a fixture where a known table spans a would-be
   512-token chunk boundary and assert the chunker does not split it — this is the enforcement test
   the acceptance criteria requires, not just a spot check; also assert two adjacent sections never
   share one chunk.

**Done when:** `data/chunks/` covers the full non-ICF corpus, the table-no-split test passes, and
every chunk's text includes its contextual header prefix.

---

## S2-09 — Metadata schema (1h)

1. Define `Chunk` formally (e.g. a `pydantic` model in `src/protocol_drift/ingestion/chunk.py` or a
   shared `models.py`) with exactly the field set from `sprint_plan.md`: `nct_id`, `doc_type`,
   `doc_version`, `section`, `subsection`, `page_range`, `chunk_type`
   (`text` / `table` / `assessment_schedule`), `is_ocr`.
2. Populate `is_ocr` from S2-01/S2-03's per-page `needs_ocr` flag — `True` if any page contributing
   to this chunk required (or would require) OCR, propagated even though S2-03's default path skips
   actual OCR — the flag records provenance regardless of whether OCR ran.
3. Populate `subsection` where S2-04's segmentation found a nested heading under a canonical
   section (e.g. a numbered sub-heading within `assessment_schedule`); `None` when only a top-level
   section was detected.
4. Re-serialize `data/chunks/{nct_id}/{doc_type}.jsonl` through this formal schema (validates every
   existing chunk from S2-08 against it, catching any field the ad-hoc dict version missed).
5. Write `tests/ingestion/test_chunk_metadata.py`: every field is present and correctly typed on a
   sample of real chunks pulled from `data/chunks/`; `is_ocr=True` appears on at least one chunk
   sourced from the known scanned page in `NCT04311632`'s SAP.

**Done when:** every chunk in `data/chunks/` validates against the formal `Chunk` schema and
`is_ocr` is correctly populated, spot-checked against the one known scanned-page source.

---

## S2-10 — Ingestion quality report (2h)

1. Create `scripts/ingestion_report.py` — same "regenerates via `scripts/`" pattern as
   `scripts/corpus_report.py` from S1-09. Pull only from frozen artifacts: `data/sections/`,
   `data/tables/`, `data/chunks/`, `data/chunks_naive/`, `data/ocr_backlog.json` — no live
   re-extraction.
2. Compute: section-detection rate (overall and by sponsor class, joined from Postgres `trials`),
   count of documents in `data/sections/detection_failures.log`, tables found (raw vs. reassembled
   logical count — shows how many multi-page runs S2-06 collapsed), chunks per document
   (mean/median), chunk-type breakdown (`text`/`table`/`assessment_schedule`), `is_ocr` chunk count.
3. Build the required naive-vs-section-aware side-by-side on the `NCT02872116` assessment-schedule
   table specifically: render (as Markdown tables or plain text) the naive chunker's mangled
   mid-table cut from `data/chunks_naive/` next to the section-aware chunker's single clean
   `chunk_type="table"` output from `data/chunks/` for the same source pages — this pairing is the
   literal deliverable the acceptance criteria and the future blog post need.
4. Include the document-depth-split stat flagged as a new finding in `corpus_assessment.md` §4:
   fraction of documents with zero detected sections vs. a full section set, so the thin
   academic-summary risk is visible as a number, not just a note.
5. Render to `docs/ingestion.md` as plain Markdown.
6. Run the script; commit `docs/ingestion.md`.
7. Add a `make ingestion-report` target alongside the existing `make corpus-report`.

**Done when:** `docs/ingestion.md` states the section-detection rate with sponsor-attributed
failures, and contains the naive-vs-section-aware pair on the `NCT02872116` assessment table.

---

## Sprint 2 acceptance criteria

- Section detection succeeds on ≥80% of documents; failures logged with sponsor attribution
- No chunk splits a table mid-structure — enforced by a test (S2-08 step 7)
- `docs/ingestion.md` contains the naive-vs-section-aware screenshot/table pair
- Every chunk carries full metadata (S2-09's `Chunk` schema); `is_ocr` populated

---

## Sprint 2 exit checklist

- [ ] `data/extracted/` — every non-ICF cohort document extracted, reading order preserved, scanned
      pages marked `needs_ocr` rather than garbled
- [ ] `data/chunks_naive/` — naive 512-token baseline chunks for the full corpus (the "before")
- [ ] `data/ocr_backlog.json` — every scanned page enumerated; default pipeline skips + reports them
- [ ] `data/sections/` — ≥80% document-level detection rate; failures logged with sponsor
- [ ] `data/tables/` — headers propagated, captions attached, units preserved, merged cells handled;
      `NCT02872116`'s two known multi-page tables correctly reassembled
- [ ] `data/versions/` — page-level version markers extracted where present, reconciled against
      Postgres `amendments`
- [ ] `data/chunks/` — section-aware, never splits a table (test-enforced), every chunk carries a
      contextual header prefix and validates against the S2-09 `Chunk` schema
- [ ] `docs/ingestion.md` — committed, states the section-detection rate and contains the
      naive-vs-section-aware pair
- [ ] CI still green: ruff + mypy + pytest (integration tests excluded) on every push

Do not start Sprint 3's retrieval work (S3-01) until every box above is checked. The next gate is
**S3-G1** (local model adequacy), after S3-07 — nothing in Sprint 2 blocks on a gate of its own.
