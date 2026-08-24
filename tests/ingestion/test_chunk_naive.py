import json
import math
from pathlib import Path

import pytest

from protocol_drift.ingestion.chunk_naive import (
    naive_chunk,
    naive_chunk_corpus,
    write_naive_chunks,
)
from protocol_drift.ingestion.extract import extract_document

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
ASSESSMENT_SCHEDULE_PDF = FIXTURES / "NCT02872116_protocol.pdf"

# corpus_assessment.md Sec.6: Table 5.1-2 (0-indexed pages 84-85) and the
# adjacent Table 5.1-3 (starting page 86) -- one continuous multi-page
# assessment-schedule region, confirmed by both pages carrying the
# "Table 5.1-2" caption in their extracted blocks.
ASSESSMENT_SCHEDULE_PAGE_RANGE = (84, 87)  # slice bounds, exclusive end


def _document_content(nct_id: str, doc_type: str, n_pages: int, words_per_page: int) -> dict:
    return {
        "nct_id": nct_id,
        "doc_type": doc_type,
        "total_pages": n_pages,
        "pages": [
            {
                "page_number": i,
                "page_class": "born_digital",
                "needs_ocr": False,
                "has_redaction": False,
                "blocks": [
                    {
                        "bbox": [0, 0, 1, 1],
                        "text": " ".join(f"w{i}_{j}" for j in range(words_per_page)),
                    }
                ],
            }
            for i in range(n_pages)
        ],
    }


def test_naive_chunk_count_matches_fixed_window_math() -> None:
    # 5 pages x 100 words/page = 500 tokens total.
    doc = _document_content("NCT00000001", "protocol", n_pages=5, words_per_page=100)

    chunks = naive_chunk(doc, chunk_tokens=200, overlap_tokens=0)

    assert len(chunks) == math.ceil(500 / 200)  # 3
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert len(chunks[0]["text"].split()) == 200
    assert len(chunks[1]["text"].split()) == 200
    assert len(chunks[2]["text"].split()) == 100  # trailing partial window
    # no token dropped or duplicated (overlap=0)
    expected_tokens = [tok for page in doc["pages"] for tok in page["blocks"][0]["text"].split()]
    reconstructed_tokens = " ".join(c["text"] for c in chunks).split()
    assert reconstructed_tokens == expected_tokens


def test_naive_chunk_carries_only_minimum_fields() -> None:
    doc = _document_content("NCT00000001", "sap", n_pages=1, words_per_page=10)

    chunks = naive_chunk(doc, chunk_tokens=200, overlap_tokens=0)

    assert len(chunks) == 1
    assert set(chunks[0].keys()) == {"nct_id", "doc_type", "chunk_index", "text", "page_range"}
    assert chunks[0]["nct_id"] == "NCT00000001"
    assert chunks[0]["doc_type"] == "sap"
    assert chunks[0]["page_range"] == [0, 0]


def test_naive_chunk_overlap_repeats_tokens_between_windows() -> None:
    doc = _document_content("NCT00000001", "protocol", n_pages=1, words_per_page=100)

    chunks = naive_chunk(doc, chunk_tokens=30, overlap_tokens=10)

    assert len(chunks) == 5  # step=20 over 100 tokens: starts 0,20,40,60,80
    first_words = chunks[0]["text"].split()
    second_words = chunks[1]["text"].split()
    assert first_words[-10:] == second_words[:10]  # the 10-token overlap


def test_naive_chunk_empty_document_produces_no_chunks() -> None:
    doc = {"nct_id": "NCT00000001", "doc_type": "protocol", "total_pages": 1, "pages": []}

    assert naive_chunk(doc) == []


def test_naive_chunk_rejects_invalid_window_sizes() -> None:
    doc = _document_content("NCT00000001", "protocol", n_pages=1, words_per_page=10)

    with pytest.raises(ValueError, match="chunk_tokens"):
        naive_chunk(doc, chunk_tokens=0)
    with pytest.raises(ValueError, match="overlap_tokens"):
        naive_chunk(doc, chunk_tokens=10, overlap_tokens=10)


def test_naive_chunk_splits_known_assessment_schedule_table_mid_row() -> None:
    # The exact failure S2-10 needs to show side-by-side against the
    # section-aware chunker: a continuous multi-page table, chunked with no
    # table awareness, gets cut apart at an arbitrary token boundary.
    content = extract_document(ASSESSMENT_SCHEDULE_PDF, ["born_digital"] * 171)
    start, end = ASSESSMENT_SCHEDULE_PAGE_RANGE
    doc = {
        "nct_id": "NCT02872116",
        "doc_type": "protocol",
        "total_pages": end - start,
        "pages": content["pages"][start:end],
    }
    all_text = " ".join(block["text"] for page in doc["pages"] for block in page["blocks"])
    assert "Table 5.1-2" in all_text  # sanity: this really is the known table region

    chunks = naive_chunk(doc, chunk_tokens=512, overlap_tokens=0)

    assert len(chunks) > 1, "expected the naive chunker to split this table across chunks"
    # the split isn't at a page or table boundary -- both chunks touch the
    # same page, i.e. content that belongs together got torn apart.
    assert chunks[0]["page_range"][1] == chunks[1]["page_range"][0]


def test_write_naive_chunks_one_json_object_per_line(tmp_path: Path) -> None:
    doc = _document_content("NCT00000001", "protocol", n_pages=1, words_per_page=10)
    chunks = naive_chunk(doc, chunk_tokens=5, overlap_tokens=0)
    dest_path = tmp_path / "NCT00000001" / "protocol.jsonl"

    write_naive_chunks(chunks, dest_path)

    lines = dest_path.read_text().splitlines()
    assert len(lines) == len(chunks)
    assert [json.loads(line)["chunk_index"] for line in lines] == [0, 1]


def test_naive_chunk_corpus_covers_every_extracted_document_and_disambiguates_duplicates(
    tmp_path: Path,
) -> None:
    extracted_dir = tmp_path / "extracted"
    doc_a = _document_content("NCT00000001", "protocol", n_pages=1, words_per_page=10)
    doc_b = _document_content("NCT00000001", "sap", n_pages=1, words_per_page=10)
    doc_c = _document_content("NCT00000001", "protocol", n_pages=1, words_per_page=10)

    (extracted_dir / "NCT00000001").mkdir(parents=True)
    (extracted_dir / "NCT00000001" / "protocol.json").write_text(json.dumps(doc_a))
    (extracted_dir / "NCT00000001" / "sap.json").write_text(json.dumps(doc_b))
    (extracted_dir / "NCT00000001" / "protocol_2.json").write_text(json.dumps(doc_c))
    (extracted_dir / "extraction_errors.log").write_text("some prior failure\n")

    dest_dir = tmp_path / "chunks_naive"
    summary = naive_chunk_corpus(extracted_dir=extracted_dir, dest_dir=dest_dir, chunk_tokens=5)

    assert summary["documents"] == 3
    assert (dest_dir / "NCT00000001" / "protocol.jsonl").exists()
    assert (dest_dir / "NCT00000001" / "sap.jsonl").exists()
    assert (dest_dir / "NCT00000001" / "protocol_2.jsonl").exists()
