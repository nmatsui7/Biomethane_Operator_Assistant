#!/usr/bin/env python3
"""
ingest_all_docs.py - Ingest biomethane MCP server documentation into ChromaDB.

Fixes applied vs original:
  1. Stale chunk cleanup — deletes old chunks when a file shrinks or is
     restructured, preventing stale content from polluting retrieval.
  2. Path validation — warns clearly if expected doc files are missing
     from RAG/docs/ (e.g. doc5_faq.md not copied from outputs/).
  3. FAQ-aware chunking — detects Q&A format files and ensures each
     Q&A pair is kept in a single chunk rather than split across two,
     which caused question text and answer text to be retrieved separately.
  4. Ingest report — prints a per-file summary so you can verify chunk
     counts and catch unexpected changes immediately.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from semantic_chunker import chunk_markdown_file, chunk_markdown_streaming

BATCH_SIZE = 100
CHROMA_PATH = os.getenv("CHROMA_PATH")
if not CHROMA_PATH:
    CHROMA_PATH = str(project_root / "RAG" / "chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "biomethane_knowledge")

# Expected docs — warn if any are missing from RAG/docs/
EXPECTED_DOCS = [
    "doc1_ad4_model_card.md",
    "doc2_bernard2001_equations.md",
    "doc3_ad4_api_reference.md",
    "doc4_failure_modes_lookup.md",
    "doc5_faq.md",
]


# ── FAQ-aware chunker ─────────────────────────────────────────────────────────

def is_faq_format(md_path: Path) -> bool:
    """Detect if file uses ## Q: / ## Q: ... format (FAQ document)."""
    text = md_path.read_text(encoding="utf-8")
    return bool(re.search(r'^## Q:', text, re.MULTILINE))


def chunk_faq_file(md_path: Path) -> list:
    """
    Split an FAQ document with moderate chunking:
    1. Each Q&A pair as ONE chunk (complete answer)
    2. Only split very long answers (>1500 chars) into 2 pieces
    
    Avoids splitting on every header/bullet, which breaks context.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    text = md_path.read_text(encoding="utf-8")

    # Split on ## Q: boundaries to get Q&A pairs
    parts = re.split(r'(?=^## Q:)', text, flags=re.MULTILINE)

    class Chunk:
        def __init__(self, content, meta):
            self.page_content = content.strip()
            self.metadata = meta

    all_chunks = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue

        # Skip preamble (before first Q:)
        if not part.startswith("## Q:") and i == 0:
            if len(part) > 100:
                all_chunks.append(Chunk(part, {"chunk_type": "preamble", "chunk_index": 0}))
            continue

        # Extract question for metadata
        q_match = re.match(r'^## Q:\s*(.+?)(?:\n|$)', part)
        question = q_match.group(1).strip() if q_match else f"qa_{i}"

        # If answer is very long, split into 2 pieces max
        if len(part) > 1500:
            # Split only on major boundaries (## headers)
            splitter = RecursiveCharacterTextSplitter(
                separators=["\n## ", "\n\n"],
                chunk_size=1000,
                chunk_overlap=100,
            )
            sub_chunks = splitter.split_text(part)
            for sc_idx, sc in enumerate(sub_chunks):
                all_chunks.append(Chunk(
                    sc,
                    {
                        "chunk_type": "qa_split",
                        "chunk_index": i,
                        "sub_index": sc_idx,
                        "question": question,
                    }
                ))
        else:
            # Keep as one complete chunk
            all_chunks.append(Chunk(part, {
                "chunk_type": "qa_full",
                "chunk_index": i,
                "question": question,
            }))

    return all_chunks


# ── Stale chunk cleanup ───────────────────────────────────────────────────────

def delete_stale_chunks(collection, file_stem: str, new_chunk_count: int):
    """
    Remove chunks from a previous ingest run that no longer exist.

    If a file previously had 18 chunks and now has 12, chunks 12..17
    remain in ChromaDB and compete with new content in retrieval.
    This function deletes any chunk IDs beyond the current count.

    Args:
        collection:       ChromaDB collection
        file_stem:        filename without extension (e.g. 'doc1_ad4_model_card')
        new_chunk_count:  number of chunks in the current ingest run
    """
    # Find all existing IDs for this file
    existing = collection.get(where={"source_file": {"$eq": f"{file_stem}.md"}})
    existing_ids = existing["ids"] if existing and existing["ids"] else []

    if not existing_ids:
        return  # First ingest — nothing to clean up

    # Identify stale IDs (index >= new_chunk_count)
    stale_ids = []
    for eid in existing_ids:
        # ID format: {file_stem}_{index}
        try:
            idx = int(eid.rsplit("_", 1)[-1])
            if idx >= new_chunk_count:
                stale_ids.append(eid)
        except (ValueError, IndexError):
            pass

    if stale_ids:
        collection.delete(ids=stale_ids)
        print(f"  → Deleted {len(stale_ids)} stale chunk(s) from previous ingest")


# ── Single file ingest ────────────────────────────────────────────────────────

def ingest_file(md_path: Path, collection, embedding_function) -> int:
    """Ingest a single markdown file with stale chunk cleanup."""
    print(f"\n📄 Processing: {md_path.name}")

    # Choose chunking strategy
    if is_faq_format(md_path):
        docs = chunk_faq_file(md_path)
        print(f"  → FAQ format detected — using Q&A-preserving chunker")
    elif md_path.stat().st_size > 5_000_000:
        docs = chunk_markdown_streaming(str(md_path))
    else:
        docs = chunk_markdown_file(str(md_path))

    if not docs:
        print(f"  ⚠️  No chunks produced — skipping")
        return 0

    print(f"  → Split into {len(docs)} chunks")

    # Clean up stale chunks from previous ingests of this file
    delete_stale_chunks(collection, md_path.stem, len(docs))

    # Build IDs, documents, and metadata
    ids       = [f"{md_path.stem}_{i}" for i in range(len(docs))]
    documents = [doc.page_content for doc in docs]
    metadatas = []
    for doc in docs:
        meta = dict(doc.metadata) if hasattr(doc, "metadata") else {}
        meta["source_file"] = md_path.name
        meta["source_path"] = str(md_path)
        metadatas.append(meta)

    # Upsert in batches
    for i in range(0, len(docs), BATCH_SIZE):
        collection.upsert(
            ids       = ids[i : i + BATCH_SIZE],
            documents = documents[i : i + BATCH_SIZE],
            metadatas = metadatas[i : i + BATCH_SIZE],
        )
        print(f"  → Indexed batch {i // BATCH_SIZE + 1} "
              f"({min(i + BATCH_SIZE, len(docs))}/{len(docs)} chunks)")

    return len(docs)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    """Ingest all documentation from RAG/docs directory."""
    print("Initialising ChromaDB...")
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-en-v1.5"
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
    )

    docs_dir = project_root / "RAG" / "docs"
    if not docs_dir.exists():
        print(f"❌ Docs directory not found: {docs_dir}")
        return

    # ── Pre-flight: warn about missing expected docs ──────────────────────────
    print("\n=== Pre-flight check ===")
    all_present = True
    for expected in EXPECTED_DOCS:
        path = docs_dir / expected
        if path.exists():
            print(f"  ✓ {expected}")
        else:
            print(f"  ✗ {expected}  ← MISSING from {docs_dir}")
            print(f"    Add the file to {docs_dir} before ingesting.")
            all_present = False

    if not all_present:
        print("\n⚠️  Some expected docs are missing. Continuing with available files.")
        print("   Missing files will NOT be in ChromaDB and will score 0 in evaluation.\n")

    # ── Ingest RAG/docs/ ─────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("Ingesting: RAG/docs/")
    print("=" * 55)

    total = 0
    report = []

    for md in sorted(docs_dir.glob("*.md")):
        n = ingest_file(md, collection, embedding_function)
        total += n
        report.append((md.name, n))

    # ── Also ingest project root docs/ if present ────────────────────────────
    root_docs_dir = project_root / "docs"
    if root_docs_dir.exists():
        print("\n" + "=" * 55)
        print("Ingesting: docs/")
        print("=" * 55)
        for md in sorted(root_docs_dir.glob("*.md")):
            n = ingest_file(md, collection, embedding_function)
            total += n
            report.append((md.name, n))

    # ── Summary report ────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("INGEST COMPLETE")
    print("=" * 55)
    print(f"\n{'File':<45} {'Chunks':>6}")
    print("-" * 55)
    for fname, n in report:
        print(f"  {fname:<43} {n:>6}")
    print("-" * 55)
    print(f"  {'TOTAL':<43} {total:>6}")
    print(f"\n  Collection total: {collection.count()} chunks in ChromaDB")
    print()

    # ── Warn if FAQ is missing ────────────────────────────────────────────────
    faq_ingested = any(fname == "doc5_faq.md" for fname, _ in report)
    if not faq_ingested:
        print("⚠️  doc5_faq.md was NOT ingested.")
        print("   Questions about souring_probability, FOS/TAC, and winter")
        print("   productivity will continue to score 0 in evaluation.")
        print(f"   Place doc5_faq.md in {docs_dir}/ first.")
    else:
        print("✅ doc5_faq.md ingested — Q14, Q15 should now score correctly.")


if __name__ == "__main__":
    main()
