import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from protocol_drift.ingestion.chunk import DEFAULT_DEST_DIR, chunk_document
from protocol_drift.ingestion.extract import extract_document
from protocol_drift.ingestion.models import Chunk

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
NO_BOOKMARKS_PDF = FIXTURES / "NCT04311632_sap.pdf"  # confirmed empty get_toc()

# corpus_assessment.md Sec.1 / test_sections.py's own known-scanned-page
# fixture: this 29-page SAP has no PDF bookmarks and these 0-indexed pages
# confirmed scanned.
SCANNED_PAGE_INDEXES = {2, 3, 7, 8, 9, 10, 11, 22}


def _real_document_content() -> dict:
    page_classes = ["scanned" if i in SCANNED_PAGE_INDEXES else "born_digital" for i in range(29)]
    content = extract_document(NO_BOOKMARKS_PDF, page_classes)
    content["nct_id"] = "NCT04311632"
    content["doc_type"] = "sap"
    content["source_path"] = str(NO_BOOKMARKS_PDF)
    return content


# --- Chunk model: field presence, typing, and validation --------------------


def test_chunk_model_accepts_a_well_formed_chunk() -> None:
    chunk = Chunk(
        nct_id="NCT00000001",
        doc_type="protocol",
        doc_version=9,
        section="synopsis",
        subsection=None,
        page_range=(0, 1),
        chunk_type="text",
        is_ocr=False,
        chunk_index=0,
        text="[NCT00000001 | protocol v9 | synopsis]\nSome body text.",
    )
    assert chunk.page_range == (0, 1)
    assert chunk.doc_version == 9


@pytest.mark.parametrize(
    "field,value",
    [
        ("doc_type", "icf"),  # ICF is excluded from ingestion entirely, per S2-01
        ("chunk_type", "figure"),  # not one of S2-08's three emitted chunk types
    ],
)
def test_chunk_model_rejects_values_outside_the_closed_sets(field: str, value: str) -> None:
    kwargs = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "doc_version": None,
        "section": "synopsis",
        "subsection": None,
        "page_range": (0, 0),
        "chunk_type": "text",
        "is_ocr": False,
        "chunk_index": 0,
        "text": "some text",
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        Chunk(**kwargs)


def test_chunk_model_rejects_inverted_page_range() -> None:
    with pytest.raises(ValidationError, match="page_range"):
        Chunk(
            nct_id="NCT00000001",
            doc_type="protocol",
            doc_version=None,
            section="synopsis",
            subsection=None,
            page_range=(5, 2),
            chunk_type="text",
            is_ocr=False,
            chunk_index=0,
            text="some text",
        )


def test_chunk_model_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        Chunk(
            nct_id="NCT00000001",
            doc_type="protocol",
            doc_version=None,
            section="synopsis",
            subsection=None,
            page_range=(0, 0),
            chunk_type="text",
            is_ocr=False,
            chunk_index=0,
            text="",
        )


def test_chunk_model_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Chunk(
            nct_id="NCT00000001",
            doc_type="protocol",
            doc_version=None,
            section="synopsis",
            subsection=None,
            page_range=(0, 0),
            chunk_type="text",
            is_ocr=False,
            chunk_index=0,
            text="some text",
            embedding="not a real field",  # type: ignore[call-arg]
        )


# --- is_ocr propagation, real fixture ----------------------------------------


def test_chunk_document_is_ocr_true_on_a_chunk_spanning_a_known_scanned_page() -> None:
    content = _real_document_content()
    # One whole-document section isolates what this test actually checks
    # (S2-01/S2-03's needs_ocr flag propagating through S2-08's chunker)
    # from S2-04's section-detection quality on this specific fixture --
    # segment_document's own regex fallback is exercised and asserted
    # separately in test_sections.py.
    sections = [
        {
            "label": "unclassified",
            "raw_heading_text": None,
            "page_range": [0, content["total_pages"] - 1],
            "detection_method": "unmatched",
        }
    ]

    chunks = chunk_document(content, sections, tables=[], versions=[])
    for chunk in chunks:
        Chunk.model_validate(chunk)  # every emitted chunk is schema-valid

    ocr_chunks = [c for c in chunks if c["is_ocr"]]
    assert ocr_chunks, "expected at least one is_ocr=True chunk on this known-scanned-page fixture"
    assert any(
        start <= scanned <= end
        for c in ocr_chunks
        for start, end in [c["page_range"]]
        for scanned in SCANNED_PAGE_INDEXES
    )
    # non-OCR chunks on this same document exist too -- is_ocr isn't just
    # stuck True for the whole document.
    assert any(not c["is_ocr"] for c in chunks)


def test_chunk_document_is_ocr_false_when_no_page_needs_ocr() -> None:
    content = _real_document_content()
    for page in content["pages"]:
        page["needs_ocr"] = False
    sections = [
        {
            "label": "unclassified",
            "raw_heading_text": None,
            "page_range": [0, content["total_pages"] - 1],
            "detection_method": "unmatched",
        }
    ]

    chunks = chunk_document(content, sections, tables=[], versions=[])

    assert all(not c["is_ocr"] for c in chunks)


# --- every real chunk in data/chunks/ validates ------------------------------


def _real_chunk_files() -> list[Path]:
    return sorted(DEFAULT_DEST_DIR.glob("*/*.jsonl"))


@pytest.mark.skipif(
    not _real_chunk_files(), reason="data/chunks/ not generated in this environment"
)
def test_sample_of_real_chunks_validate_against_the_formal_schema() -> None:
    files = _real_chunk_files()
    random.seed(0)
    sample_files = random.sample(files, min(20, len(files)))

    validated = 0
    for path in sample_files:
        lines = path.read_text().splitlines()
        for line in random.sample(lines, min(5, len(lines))):
            chunk = Chunk.model_validate_json(line)
            assert chunk.text.startswith(f"[{chunk.nct_id} | {chunk.doc_type} v")
            validated += 1

    assert validated > 0
