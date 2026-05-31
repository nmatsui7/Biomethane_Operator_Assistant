#!/usr/bin/env python3
"""
query_rag.py - Query the Biomethane MCP Server RAG system.

Allows semantic search over biomethane documentation using embeddings.
Optional cross-encoder reranking for improved relevance.
"""

import os
import argparse
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHROMA_PATH = os.getenv("CHROMA_PATH")
if not CHROMA_PATH:
    CHROMA_PATH = str(project_root / "RAG" / "chroma_db")

COLLECTION_NAME = os.getenv("COLLECTION_NAME", "biomethane_knowledge")

# Reranker settings
USE_RERANKER = False  # Disabled - causes inconsistent results


def get_reranker():
    """Lazy load the reranker."""
    from reranker import Reranker
    return Reranker()


def query_text(
    query_text: str,
    n_results: int = 5,
    filter_source: str = None,
    use_reranker: bool = USE_RERANKER,
) -> List[Dict[str, Any]]:
    """Query the text collection with optional reranking."""
    results = []

    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-en-v1.5"
    )

    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=embedding_function
        )

        where = {}
        if filter_source:
            where["source_file"] = {"$eq": filter_source}

        # Retrieve more candidates if using reranker
        retrieve_n = RETRIEVE_TOP_N if use_reranker else n_results

        text_results = collection.query(
            query_texts=[query_text],
            n_results=retrieve_n,
            where=where if where else None,
            include=["documents", "metadatas", "distances"],
        )

        if not text_results["documents"] or not text_results["documents"][0]:
            return results

        docs = text_results["documents"][0]
        metas = text_results["metadatas"][0]
        dists = text_results["distances"][0]

        # Apply reranking if enabled
        if use_reranker and len(docs) > n_results:
            reranker = get_reranker()
            ranked_docs, rerank_scores = reranker.rerank(
                query_text, docs, top_k=RERANK_TOP_K
            )

            # Reorder results based on reranking
            reranked_metas = []
            reranked_dists = []
            for doc in ranked_docs:
                idx = docs.index(doc)
                reranked_metas.append(metas[idx])
                reranked_dists.append(dists[idx])

            docs = ranked_docs
            metas = reranked_metas
            dists = reranked_dists

        # Take top n_results after reranking
        for i in range(min(n_results, len(docs))):
            results.append(
                {
                    "content": docs[i],
                    "metadata": metas[i],
                    "distance": dists[i],
                    "score": 1 - dists[i],
                    "type": "text",
                }
            )
    except Exception as e:
        print(f"Warning: Text search unavailable: {e}")

    return results


def query(
    query_str: str,
    n_results: int = 5,
    filter_source: str = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Query the RAG system.

    Args:
        query_str: The search query
        n_results: Number of results to return
        filter_source: Optional source file to filter by

    Returns:
        Dictionary with text results
    """
    results = {"text": []}

    results["text"] = query_text(
        query_str, n_results, filter_source
    )

    return results


def display_results(results: Dict[str, List[Dict]], show_content: bool = True):
    """Pretty print query results."""
    if results.get("text"):
        print(f"\n{'='*65}")
        print("📄 RAG RETRIEVAL RESULTS (from biomethane documentation)")
        print(f"{'='*65}\n")
        
        for i, result in enumerate(results["text"], 1):
            source_file = result["metadata"].get("source_file", "unknown")
            source_path = result["metadata"].get("source_path", "")

            print(f"[Result {i}]")
            print(f"  Source: {source_file}")
            print(f"  From: {source_path}")
            print(f"  Relevance: {result['score']:.1%}")

            if show_content:
                content = result["content"]
                if len(content) > 500:
                    content = content[:500] + "..."
                print(f"\n  Content:\n{content}")
            print()

    if not results.get("text"):
        print("\nNo results found.")


def interactive_mode():
    """Run in interactive query mode."""
    print("\n🔍 Biomethane MCP Server RAG Query System")
    print("=" * 50)
    print("Type your questions to search the documentation.")
    print("Commands: 'filter ', 'quit'")
    print()

    current_filter = None

    while True:
        try:
            cmd = input(f"[Filter: {current_filter or 'none'}] Query: ").strip()

            if cmd.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break

            if cmd.lower().startswith("filter "):
                current_filter = cmd.split()[1]
                print(f"Filter set to: {current_filter}")
                continue

            if cmd.lower() == "filter":
                current_filter = None
                print("Filter cleared")
                continue

            if not cmd:
                continue

            results = query(cmd, n_results=5, filter_source=current_filter)
            display_results(results)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    """CLI for RAG queries."""
    parser = argparse.ArgumentParser(description="Query Biomethane RAG system")
    parser.add_argument("query", nargs="?", help="Query text")
    parser.add_argument(
        "-n", "--num-results", type=int, default=5, help="Number of results to return"
    )
    parser.add_argument("-f", "--filter", help="Filter by source file")
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="Run in interactive mode"
    )
    args = parser.parse_args()

    if args.interactive or (not args.query and not args.interactive):
        interactive_mode()
    else:
        results = query(
            args.query,
            n_results=args.num_results,
            filter_source=args.filter,
        )
        display_results(results)


if __name__ == "__main__":
    main()