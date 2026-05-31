"""
vector_store.py - Load markdown documents into ChromaDB.

Streaming-safe for large markdown files.
Optimized for memory efficiency with batch processing.
"""

import os
import gc
import argparse
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Setup path
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from semantic_chunker import chunk_markdown_file, chunk_markdown_streaming

BATCH_SIZE = 100

CHROMA_PATH = os.getenv("CHROMA_PATH")
if not CHROMA_PATH:
    CHROMA_PATH = str(project_root / "chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "biomethane_knowledge")


_collection = None
_embedding_function = None


def _get_collection():
    """Get or create ChromaDB collection."""
    global _collection, _embedding_function

    if _collection is None:
        print(f"Initializing ChromaDB at {CHROMA_PATH}...")
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _embedding_function = SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-small-en-v1.5"
        )
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=_embedding_function
        )
        print(f"✓ Collection '{COLLECTION_NAME}' ready")

    return _collection


def ingest_document(md_path: str, use_streaming: bool = False) -> int:
    """
    Ingest a markdown document into ChromaDB.

    Args:
        md_path: Path to markdown file
        use_streaming: Use streaming mode for large files

    Returns:
        Number of chunks ingested
    """
    md_path = Path(md_path)

    if not md_path.exists():
        raise FileNotFoundError(f"File not found: {md_path}")

    print(f"\n📄 Processing: {md_path.name}")

    # Chunk the document
    if use_streaming or md_path.stat().st_size > 5_000_000:
        print(f"  → Using streaming mode (file size: {md_path.stat().st_size:,} bytes)")
        docs = chunk_markdown_streaming(str(md_path))
    else:
        docs = chunk_markdown_file(str(md_path))

    print(f"  → Split into {len(docs)} chunks")

    # Get collection
    collection = _get_collection()

    # Batch upsert
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]

        ids = [f"{md_path.stem}_{j}" for j in range(i, min(i + BATCH_SIZE, len(docs)))]
        documents = [doc.page_content for doc in batch]
        metadatas = [doc.metadata for doc in batch]

        # Add source file to metadata
        for meta in metadatas:
            meta["source_file"] = md_path.name
            meta["source_path"] = str(md_path)

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        print(
            f"  → Indexed batch {i // BATCH_SIZE + 1}/{(len(docs) + BATCH_SIZE - 1) // BATCH_SIZE}"
        )

    # Force garbage collection
    chunk_count = len(docs)
    del docs
    gc.collect()

    print(f"  ✓ Successfully indexed {chunk_count} chunks")
    return chunk_count


def clear_collection():
    """Clear all documents from the collection."""
    collection = _get_collection()
    collection.delete(where={}, where_document={})
    print("✓ Collection cleared")


def get_collection_stats():
    """Get collection statistics."""
    collection = _get_collection()
    return {"name": collection.name, "count": collection.count()}


def main():
    """CLI for vector store operations."""
    parser = argparse.ArgumentParser(description="Ingest markdown into ChromaDB")
    parser.add_argument("file", nargs="?", help="Markdown file to ingest")
    parser.add_argument("--clear", action="store_true", help="Clear collection first")
    parser.add_argument("--stats", action="store_true", help="Show collection stats")
    parser.add_argument("--streaming", action="store_true", help="Force streaming mode")
    args = parser.parse_args()

    if args.clear:
        print("Clearing collection...")
        clear_collection()

    if args.stats:
        stats = get_collection_stats()
        print(f"\n📊 Collection: {stats['name']}")
        print(f"   Total chunks: {stats['count']}")
        return

    if args.file:
        count = ingest_document(args.file, use_streaming=args.streaming)
        print(f"\n✓ Total chunks indexed: {count}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
