"""
semantic_chunker.py - Semantic text chunking for Bio-Methane documentation.

Splits markdown documents into semantically coherent chunks for RAG ingestion.
"""

import os
import re
import argparse
from pathlib import Path
from typing import List

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_core.documents import Document


# Maximum section size before triggering fallback split
MAX_SECTION_CHARS = 100_000
MAX_SECTION_LINES = 10_000

# Fallback splitter for large sections
_HEADER_4_SPLITTER = RecursiveCharacterTextSplitter(
    separators=["\n#### ", "\n### ", "\n## ", "\n\n", "\n"],
    chunk_size=5_000,
    chunk_overlap=200,
)


def _is_section_large(text: str) -> bool:
    """Check if a section exceeds size thresholds."""
    return len(text) > MAX_SECTION_CHARS or text.count("\n") > MAX_SECTION_LINES


def _split_by_headers_fallback(text: str) -> List[Document]:
    """Fallback splitter using recursive character splitting."""
    print(f"  ⚠ Section exceeds size limits - using fallback splitter")

    dummy_doc = Document(page_content=text, metadata={"source": "fallback_split"})
    result = _HEADER_4_SPLITTER.split_documents([dummy_doc])

    print(f"  → Fallback produced {len(result)} sub-chunks")
    return result


def chunk_markdown_text(text: str, source_name: str = "document") -> List[Document]:
    """
    Split markdown text into semantically coherent chunks.

    Args:
        text: The markdown text to chunk
        source_name: Name to use in metadata

    Returns:
        List of Document objects with chunked content
    """
    if _is_section_large(text):
        return _split_by_headers_fallback(text)

    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
        ("####", "Header 4"),
    ]

    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

    # Split into documents
    docs = splitter.split_text(text)

    # Add source metadata
    for doc in docs:
        doc.metadata["source"] = source_name

    return docs


def chunk_markdown_file(md_path: str) -> List[Document]:
    """
    Load and chunk a markdown file.

    Args:
        md_path: Path to markdown file

    Returns:
        List of Document objects with chunked content
    """
    source_name = Path(md_path).stem

    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    return chunk_markdown_text(text, source_name)


def _iter_top_level_sections(md_path: str):
    """
    Generator: yields (section_title, section_text) for each top-level '#'
    section in the markdown file, reading line-by-line without loading the
    whole file into memory.
    """
    current_title = "__preamble__"
    current_lines = []

    with open(md_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("# ") and not line.startswith("## "):
                if current_lines:
                    text = "".join(current_lines).strip()
                    if text:
                        yield current_title, text
                current_title = line.strip()
                current_lines = [line]
            else:
                current_lines.append(line)

    if current_lines:
        text = "".join(current_lines).strip()
        if text:
            yield current_title, text


def chunk_markdown_streaming(md_path: str) -> List[Document]:
    """
    Stream-process a large markdown file section by section.
    Memory efficient for large files.

    Args:
        md_path: Path to markdown file

    Returns:
        List of Document objects with chunked content
    """
    all_docs = []

    for section_title, section_text in _iter_top_level_sections(md_path):
        docs = chunk_markdown_text(section_text, source_name=section_title)
        all_docs.extend(docs)

    return all_docs


def main():
    """CLI for testing chunking."""
    parser = argparse.ArgumentParser(description="Chunk markdown files")
    parser.add_argument("file", help="Markdown file to chunk")
    parser.add_argument("--preview", action="store_true", help="Show preview of chunks")
    args = parser.parse_args()

    docs = chunk_markdown_file(args.file)

    print(f"\n✓ Split into {len(docs)} chunks\n")

    if args.preview:
        for i, doc in enumerate(docs[:5]):
            print(f"--- Chunk {i + 1} ---")
            print(f"Source: {doc.metadata.get('source', 'unknown')}")
            print(f"Content (first 200 chars): {doc.page_content[:200]}...")
            print()


if __name__ == "__main__":
    main()
