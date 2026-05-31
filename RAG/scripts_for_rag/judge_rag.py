#!/usr/bin/env python3
"""
judge_rag.py - Test and evaluate the RAG system using Gemma 4 4B.

This script runs a set of test questions against the RAG system and
uses Gemma 4 4B to evaluate the quality of retrieved context and answers.
"""

import os
import time
import json
import requests
from pathlib import Path
from typing import Dict, List, Any

# Set Hugging Face cache paths BEFORE imports
project_root = Path(__file__).parent.parent.parent
os.environ.setdefault("HF_HOME", str(project_root / ".cache" / "huggingface"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(project_root / ".cache" / "huggingface" / "transformers"))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(project_root / ".cache" / "huggingface" / "sentence-transformers"))

# Add project to path
import sys
sys.path.insert(0, str(project_root / "RAG" / "scripts_for_rag"))

from query_rag import query, display_results

# Gemma 4 4B server endpoint
GEMMA_URL = "http://localhost:8080/v1/chat/completions"
GEMMA_MODEL = "gemma-4-e2b"


# Test questions about biomethane MCP server
TEST_QUESTIONS = [
    {
        "id": 1,
        "question": "What does the EnKF predict?",
        "expected_topics": ["S2", "VFA", "X2", "methanogens", "state estimation"],
    },
    {
        "id": 2,
        "question": "What are the AD4 state variables?",
        "expected_topics": ["S1", "S2", "X1", "X2", "COD", "VFA"],
    },
    {
        "id": 3,
        "question": "What is the souring threshold for VFA?",
        "expected_topics": ["150", "mmol/L", "souring", "S2"],
    },
    {
        "id": 4,
        "question": "How do I calibrate k6?",
        "expected_topics": ["k6", "fit", "ad4_fit_k6", "calibration"],
    },
    {
        "id": 5,
        "question": "What is temperature correction in AD4?",
        "expected_topics": ["theta", "1.035", "Arrhenius", "mu2_max"],
    },
    {
        "id": 6,
        "question": "What is the critical dilution rate?",
        "expected_topics": ["washout", "D_crit", "mu2_max", "HRT"],
    },
    {
        "id": 7,
        "question": "How do I interpret the ad4_simulate output — what does each field mean?",
        "expected_topics": ["steady_state", "S2_status", "interpretation", "washout", "healthy"],
    },
    {
        "id": 8,
        "question": "What are the EnKF risk levels and what triggers WARNING or CRITICAL?",
        "expected_topics": ["HEALTHY", "WARNING", "CRITICAL", "souring_probability", "washout_probability"],
    },
    {
        "id": 9,
        "question": "When should I use ad4_simulate versus enkf_update?",
        "expected_topics": ["simulation", "state estimation", "what-if", "current state"],
    },
    {
        "id": 10,
        "question": "Why is my digester less productive in winter and what should I do?",
        "expected_topics": ["mu2_max", "Arrhenius", "D_crit", "temperature", "seasonal"],
    },
    {
        "id": 11,
        "question": "My biogas dropped 20 percent this week — what should I check first?",
        "expected_topics": ["S2", "VFA", "enkf_status", "trend", "lagging"],
    },
    {
        "id": 12,
        "question": "Is it safe to increase my feeding rate right now?",
        "expected_topics": ["D_crit", "safety_margin", "ad4_critical_dilution_rate", "OLR"],
    },
    {
        "id": 13,
        "question": "Which model parameters are not calibrated to my specific plant?",
        "expected_topics": ["Ki2", "mu2_max", "literature_default", "Benyahia", "unvalidated"],
    },
    {
        "id": 14,
        "question": "What does souring_probability 0.35 mean and should I be worried?",
        "expected_topics": ["souring_probability", "ensemble", "S2", "WARNING", "action"],
    },
    {
        "id": 15,
        "question": "How do I use a FOS TAC measurement with the system?",
        "expected_topics": ["fos_mg_per_l", "enkf_update", "S2 proxy", "60", "tighten"],
    },
]


def query_gemma(prompt: str, context: str = "") -> str:
    """Query Gemma 4 4B for answer evaluation."""
    full_prompt = f"""You are an expert judge evaluating a RAG system.
The retrieved context BELOW is from a biomethane operations FAQ system.
Your task is to evaluate whether this context could help answer the question.

QUESTION: {prompt}

RETRIEVED CONTEXT:
{context}

Rate 0-10: 10 = contains exact answer, 5 = partially relevant, 0 = wrong topic

Respond in this EXACT format (no other text):
RELEVANCE: <number>
EXPLANATION: <1-2 sentences>

Examples:
- If context has the answer: "RELEVANCE: 10\\nEXPLANANCE: The context directly explains the answer..."
- If partial: "RELEVANCE: 5\\nEXPLANATION: The context mentions some relevant details..."
- If wrong topic: "RELEVANCE: 0\\nEXPLANATION: "

Now you respond:"""

    headers = {"Content-Type": "application/json"}
    payload = {
        "model": GEMMA_MODEL,
        "messages": [
            {"role": "user", "content": full_prompt}
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
    }

    try:
        response = requests.post(GEMMA_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ERROR: {str(e)}"


def evaluate_with_gemma(question: str, retrieved_docs: List[Dict]) -> Dict[str, Any]:
    """Use Gemma to evaluate the retrieved context."""
    if not retrieved_docs:
        return {"relevance": 0, "explanation": "No documents retrieved"}

    # Combine top retrieved documents as context
    context = "\n\n---\n\n".join([
        f"[{doc['metadata'].get('source_file', 'unknown')}]:\n{doc['content'][:800]}"
        for doc in retrieved_docs[:3]
    ])

    evaluation = query_gemma(question, context)

    # Parse evaluation
    relevance = 0
    explanation = ""
    for line in evaluation.split('\n'):
        if line.startswith("RELEVANCE:"):
            try:
                relevance = int(line.split(":")[1].strip())
            except:
                pass
        elif line.startswith("EXPLANATION:"):
            explanation = line.split(":", 1)[1].strip()

    return {
        "relevance": relevance,
        "explanation": explanation,
        "full_evaluation": evaluation,
    }


def run_judge():
    """Run the RAG judge evaluation."""
    print("=" * 70)
    print("RAG EVALUATION WITH GEMMA 4 4B")
    print("=" * 70)
    print()

    # Check Gemma connection
    try:
        test_req = requests.get("http://localhost:8080/v1/models", timeout=5)
        print("✓ Gemma 4 4B server connected\n")
    except Exception as e:
        print(f"✗ Cannot connect to Gemma server: {e}")
        print("  Start with: bash bin/start_gemma_llama.sh")
        return

    results = []

    for i, test in enumerate(TEST_QUESTIONS, 1):
        start_time = time.time()
        print(f"[{i}/{len(TEST_QUESTIONS)}] Question: {test['question']}")

        # Query RAG
        rag_results = query(test["question"], n_results=3)
        docs = rag_results.get("text", [])

        # Get average relevance score
        avg_relevance = sum(d["score"] for d in docs) / len(docs) if docs else 0

        # Evaluate with Gemma
        gemma_eval = evaluate_with_gemma(test["question"], docs)

        result = {
            "id": test["id"],
            "question": test["question"],
            "docs_retrieved": len(docs),
            "avg_rag_score": avg_relevance,
            "gemma_relevance": gemma_eval["relevance"],
            "gemma_explanation": gemma_eval["explanation"][:200] if gemma_eval.get("explanation") else "",
        }
        results.append(result)
        
        elapsed = time.time() - start_time
        result["elapsed_seconds"] = elapsed

        print(f"  Docs: {len(docs)}, RAG relevance: {avg_relevance:.1%}, Gemma: {gemma_eval['relevance']}/10")
        print(f"  → {gemma_eval.get('explanation', 'N/A')[:150]}... ({elapsed:.1f}s)")
        print()

    # Summary
    print("=" * 70)
    print("EVALUATION SUMMARY")
    print("=" * 70)

    avg_rag = sum(r["avg_rag_score"] for r in results) / len(results)
    avg_gemma = sum(r["gemma_relevance"] for r in results) / len(results)

    print(f"\nTotal questions: {len(results)}")
    print(f"Average RAG relevance: {avg_rag:.1%}")
    print(f"Average Gemma evaluation: {avg_gemma:.1f}/10")

    # Show scores by question
    print(f"\n{'ID':<4} {'Question':<45} {'RAG':<8} {'Gemma':<8}")
    print("-" * 70)
    for r in results:
        q_short = r["question"][:42] + "..." if len(r["question"]) > 45 else r["question"]
        print(f"{r['id']:<4} {q_short:<45} {r['avg_rag_score']:.1%}     {r['gemma_relevance']}/10")

    # Save results
    output_path = project_root / "RAG" / "rag_evaluation_results.json"
    total_time = sum(r.get("elapsed_seconds", 0) for r in results)
    with open(output_path, "w") as f:
        json.dump({
            "summary": {
                "total_questions": len(results),
                "avg_rag_relevance": avg_rag,
                "avg_gemma_score": avg_gemma,
                "total_elapsed_seconds": total_time,
                "avg_seconds_per_question": total_time / len(results) if results else 0,
            },
            "results": results,
        }, f, indent=2)

    print(f"\n✓ Results saved to: {output_path}")
    print()

    # Grade
    if avg_gemma >= 8:
        print("🎉 EXCELLENT - RAG system is performing very well!")
    elif avg_gemma >= 6:
        print("✅ GOOD - RAG system is working adequately")
    elif avg_gemma >= 4:
        print("⚠️  FAIR - Some room for improvement")
    else:
        print("❌ NEEDS WORK - RAG system needs attention")


if __name__ == "__main__":
    run_judge()