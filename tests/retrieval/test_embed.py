from unittest.mock import MagicMock, call

from protocol_drift.ingestion.models import Chunk
from protocol_drift.retrieval.embed import (
    DEFAULT_MODEL_REVISION,
    chunk_id_for,
    compute_cache_key,
    embed_chunks,
)

EMBEDDING_DIM = 768


def _chunk(nct_id: str, chunk_index: int, text: str) -> Chunk:
    return Chunk(
        nct_id=nct_id,
        doc_type="protocol",
        doc_version=1,
        section="eligibility",
        subsection=None,
        page_range=(1, 1),
        chunk_type="text",
        is_ocr=False,
        chunk_index=chunk_index,
        text=text,
    )


def _fixture_chunks() -> list[Chunk]:
    return [
        _chunk("NCT00000001", 0, "Patients must be at least 18 years of age."),
        _chunk("NCT00000001", 1, "Primary outcome is overall survival at 24 months."),
        _chunk("NCT00000002", 0, "Target enrollment is 250 subjects across 12 sites."),
    ]


def _fake_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.encode.side_effect = lambda texts: [[float(len(t))] * EMBEDDING_DIM for t in texts]
    return embedder


def test_embed_chunks_produces_expected_dimension() -> None:
    chunks = _fixture_chunks()
    embedder = _fake_embedder()

    embedded = list(embed_chunks(chunks, embedder, DEFAULT_MODEL_REVISION))

    assert len(embedded) == len(chunks)
    for e in embedded:
        assert len(e.embedding) == EMBEDDING_DIM

    embedder.encode.assert_called_once_with([c.text for c in chunks])


def test_embed_chunks_cache_hit_skips_encode_call() -> None:
    chunks = _fixture_chunks()
    embedder = _fake_embedder()
    cache: dict = {}

    first_pass = list(embed_chunks(chunks, embedder, DEFAULT_MODEL_REVISION, cache=cache))
    assert embedder.encode.call_count == 1

    second_pass = list(embed_chunks(chunks, embedder, DEFAULT_MODEL_REVISION, cache=cache))

    assert embedder.encode.call_count == 1  # no new encode calls on the cached re-run
    assert [e.embedding_cache_key for e in second_pass] == [
        e.embedding_cache_key for e in first_pass
    ]


def test_cache_key_changes_with_revision_or_text() -> None:
    chunk_id = "NCT00000001:protocol:0"
    key_a = compute_cache_key("rev-a", chunk_id, "some text")
    key_b = compute_cache_key("rev-b", chunk_id, "some text")
    key_c = compute_cache_key("rev-a", chunk_id, "different text")

    assert key_a != key_b
    assert key_a != key_c


def test_chunk_id_matches_s3_02_format() -> None:
    chunk = _chunk("NCT00000001", 3, "text")
    assert chunk_id_for(chunk) == "NCT00000001:protocol:3"


def test_partial_cache_only_encodes_misses() -> None:
    chunks = _fixture_chunks()
    embedder = _fake_embedder()
    cache: dict = {}

    list(embed_chunks(chunks[:1], embedder, DEFAULT_MODEL_REVISION, cache=cache))
    assert embedder.encode.call_count == 1

    list(embed_chunks(chunks, embedder, DEFAULT_MODEL_REVISION, cache=cache))

    # Second call only encodes the two chunks not already cached.
    assert embedder.encode.call_args_list[-1] == call([chunks[1].text, chunks[2].text])
