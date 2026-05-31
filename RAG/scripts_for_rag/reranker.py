"""
reranker.py - Cross-encoder reranking for Bio-Methane RAG

Uses BAAI/bge-reranker-base for improved retrieval precision.
"""

import os
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# Model settings
HF_HOME = os.getenv("HF_HOME", str(project_root / ".hf_cache"))
os.environ["HF_HOME"] = HF_HOME

# Default model
DEFAULT_MODEL = "BAAI/bge-reranker-base"


class Reranker:
    """Cross-encoder reranker for improved retrieval."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        """Initialize reranker with specified model."""
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        """Lazy load model."""
        if self._model is None:
            print(f"Loading reranker model: {self.model_name}...")
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            print("✓ Reranker model loaded")
        return self._model

    def rerank(
        self, query: str, documents: list, top_k: int = 5, return_scores: bool = True
    ) -> tuple[list, list]:
        """
        Rerank documents by relevance to query.

        Args:
            query: Search query
            documents: List of document strings to rerank
            top_k: Number of top documents to return
            return_scores: Whether to return scores

        Returns:
            Tuple of (ranked_documents, scores)
        """
        if not documents:
            return documents, []

        # Create query-document pairs
        pairs = [(query, doc) for doc in documents]

        # Get cross-encoder scores
        scores = self.model.predict(pairs)

        # Sort by score descending
        sorted_indices = np.argsort(scores)[::-1]

        # Return top_k
        ranked_docs = [documents[i] for i in sorted_indices[:top_k]]

        if return_scores:
            ranked_scores = [float(scores[i]) for i in sorted_indices[:top_k]]
            return ranked_docs, ranked_scores
        else:
            return ranked_docs, None

    def rerank_with_metadata(
        self, query: str, docs_with_meta: list, top_k: int = 5
    ) -> tuple[list, list]:
        """
        Rerank documents that have metadata, preserving metadata.

        Args:
            query: Search query
            docs_with_meta: List of dicts with 'content' and 'metadata' keys
            top_k: Number of top documents to return

        Returns:
            Tuple of (ranked_docs_with_meta, scores)
        """
        if not docs_with_meta:
            return [], []

        # Extract content for reranking
        documents = [d.get("content", "") for d in docs_with_meta]

        # Create pairs
        pairs = [(query, doc) for doc in documents]

        # Get scores
        scores = self.model.predict(pairs)

        # Sort by score
        sorted_indices = np.argsort(scores)[::-1]

        # Build ranked results preserving metadata
        ranked_results = []
        ranked_scores = []

        for i in sorted_indices[:top_k]:
            ranked_results.append(docs_with_meta[i])
            ranked_scores.append(float(scores[i]))

        return ranked_results, ranked_scores


def rerank_results(
    query: str,
    text_results: dict,
    vision_results: dict,
    text_top: int = 10,
    vision_top: int = 5,
    final_top: int = 5,
    reranker: "Reranker" = None,
) -> tuple[list, list, dict]:
    """
    Full reranking pipeline for text and vision results.

    Args:
        query: User query
        text_results: ChromaDB text query results
        vision_results: ChromaDB vision query results
        text_top: How many text results to rerank
        vision_top: How many vision results to rerank
        final_top: Final number of results to return
        reranker: Optional pre-initialized Reranker instance

    Returns:
        Tuple of (context_chunks, image_paths, metrics)
    """
    if reranker is None:
        reranker = Reranker()

    context_chunks = []
    image_paths = []
    metrics = {"text_count": 0, "vision_count": 0, "total_chars": 0, "sources": []}

    # Process text results
    if text_results["documents"] and text_results["documents"][0]:
        text_docs = text_results["documents"][0][:text_top]
        text_metas = text_results["metadatas"][0][:text_top]

        # Rerank
        ranked_text, text_scores = reranker.rerank(
            query, text_docs, top_k=min(5, len(text_docs))
        )

        # Get corresponding metadata for reranked order
        text_docs_original = text_results["documents"][0]
        text_metas_original = text_results["metadatas"][0]

        # Map scores back to reranked order
        for i, doc in enumerate(ranked_text):
            # Find original index
            orig_idx = text_docs_original.index(doc)
            meta = text_metas_original[orig_idx]
            score = text_scores[i]

            label = f"[Text | {meta.get('source_file', 'unknown')}]"
            context_chunks.append(f"{label} [rerank_score: {score:.3f}]\n{doc[:500]}")

            metrics["text_count"] += 1
            metrics["sources"].append(
                {
                    "type": "text",
                    "source": meta.get("source_file", "unknown"),
                    "relevance": score,
                }
            )

    # Process vision results
    if vision_results["documents"] and vision_results["documents"][0]:
        vision_docs = vision_results["documents"][0][:vision_top]
        vision_metas = vision_results["metadatas"][0][:vision_top]

        # Rerank
        ranked_vision, vision_scores = reranker.rerank(
            query, vision_docs, top_k=min(3, len(vision_docs))
        )

        # Map back
        vision_docs_original = vision_results["documents"][0]
        vision_metas_original = vision_results["metadatas"][0]

        for i, doc in enumerate(ranked_vision):
            orig_idx = vision_docs_original.index(doc)
            meta = vision_metas_original[orig_idx]
            score = vision_scores[i]

            diagram_name = meta.get("diagram_name", "unknown")
            img_path = meta.get("image_path", "")

            label = f"[Diagram | {diagram_name}]"
            context_chunks.append(f"{label} [rerank_score: {score:.3f}]\n{doc[:500]}")

            if img_path:
                image_paths.append(img_path)

            metrics["vision_count"] += 1
            metrics["sources"].append(
                {
                    "type": "vision",
                    "source": diagram_name,
                    "relevance": score,
                    "image": img_path,
                }
            )

    # Limit total chunks
    if len(context_chunks) > final_top:
        context_chunks = context_chunks[:final_top]

    metrics["total_chars"] = sum(len(c) for c in context_chunks)
    metrics["chunk_count"] = len(context_chunks)

    return context_chunks, image_paths, metrics


if __name__ == "__main__":
    # Quick test
    print("Testing reranker...")

    reranker = Reranker()

    docs = [
        "The digester volume is calculated as V = Load / OLR",
        "pH should be maintained between 6.8 and 7.2",
        "CHP units convert biogas to electricity",
        "The temperature in mesophilic digestion is 35°C",
        " HRT is typically 20-60 days",
    ]

    query = "How to calculate digester size?"

    ranked, scores = reranker.rerank(query, docs, top_k=3)

    print(f"\nQuery: {query}")
    print(f"\nTop 3 results:")
    for i, (doc, score) in enumerate(zip(ranked, scores), 1):
        print(f"{i}. Score: {score:.3f}")
        print(f"   {doc[:80]}...")
