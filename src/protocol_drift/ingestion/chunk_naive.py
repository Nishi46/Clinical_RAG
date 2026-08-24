"""Naive fixed-size chunker -- the "before" picture for S2-10's comparison.

Deliberately dumb: flattens every page's blocks into one token stream per
document and cuts fixed-size windows, with zero section or table awareness.
Protocol and SAP are chunked separately (never concatenated across
doc_type -- they're different documents with different structure). This is
what S2-08's section-aware chunker is measured against, so it must stay
naive rather than accidentally growing structure-awareness over time.

Reads only from data/extracted/ (S2-01's output), never re-extracts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_DEST_DIR = Path("data/chunks_naive")
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 0


def _document_tokens(document_content: dict[str, Any]) -> list[tuple[str, int]]:
    """Every page's blocks, flattened in page order, as (token, page_number)
    pairs -- a simple whitespace token count, not a real BPE tokenizer;
    "naive" is the point, not an approximation to fix later."""
    tokens: list[tuple[str, int]] = []
    for page in document_content["pages"]:
        page_number = page["page_number"]
        for block in page["blocks"]:
            for token in block["text"].split():
                tokens.append((token, page_number))
    return tokens


def naive_chunk(
    document_content: dict[str, Any],
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[dict[str, Any]]:
    """Fixed-size token windows over one document's flattened text -- no
    regard for section or table boundaries, so a window can (and, on a
    real multi-page table, will) land mid-row or mid-cell."""
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens must be in [0, chunk_tokens)")

    tokens = _document_tokens(document_content)
    step = chunk_tokens - overlap_tokens

    chunks: list[dict[str, Any]] = []
    # range(0, len(tokens), step) already stops once a start would exceed
    # len(tokens), and any start < len(tokens) yields a non-empty window --
    # no extra bounds-checking needed.
    for chunk_index, start in enumerate(range(0, len(tokens), step)):
        window = tokens[start : start + chunk_tokens]
        pages = [page_number for _, page_number in window]
        chunks.append(
            {
                "nct_id": document_content["nct_id"],
                "doc_type": document_content["doc_type"],
                "chunk_index": chunk_index,
                "text": " ".join(token for token, _ in window),
                "page_range": [min(pages), max(pages)],
            }
        )
    return chunks


def extracted_documents(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[Path]:
    """Every S2-01 extraction output -- glob excludes extraction_errors.log,
    which sits at the top level rather than under an nct_id subdirectory."""
    return sorted(extracted_dir.glob("*/*.json"))


def write_naive_chunks(chunks: list[dict[str, Any]], dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with dest_path.open("w") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


def naive_chunk_corpus(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    dest_dir: Path = DEFAULT_DEST_DIR,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> dict[str, Any]:
    documents = 0
    total_chunks = 0
    for path in extracted_documents(extracted_dir):
        document_content = json.loads(path.read_text())
        chunks = naive_chunk(
            document_content, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens
        )
        # Mirror S2-01's own output naming (doc_type[_N].json -> [_N].jsonl)
        # so a duplicate-doc_type trial (e.g. NCT03083873's 4 protocol PDFs)
        # keeps distinct chunk files instead of colliding.
        dest_path = dest_dir / document_content["nct_id"] / f"{path.stem}.jsonl"
        write_naive_chunks(chunks, dest_path)
        documents += 1
        total_chunks += len(chunks)

    summary = {"documents": documents, "chunks": total_chunks}
    print(f"Naive-chunked {documents} document(s) -> {total_chunks} chunks -> {dest_dir}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Naive fixed-size baseline chunker (the 'before' picture for S2-10)."
    )
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--chunk-tokens", type=int, default=DEFAULT_CHUNK_TOKENS)
    parser.add_argument("--overlap-tokens", type=int, default=DEFAULT_OVERLAP_TOKENS)
    args = parser.parse_args()

    naive_chunk_corpus(
        extracted_dir=args.extracted_dir,
        dest_dir=args.dest,
        chunk_tokens=args.chunk_tokens,
        overlap_tokens=args.overlap_tokens,
    )


if __name__ == "__main__":
    main()
