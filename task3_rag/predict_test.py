
"""Generate Task 3 test predictions using the shared R4 pipeline.

Run from repository root:
    uv run python task3_rag/predict_test.py
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from bm25 import tokenize
from chunking import load_corpus, recursive_chunks
from dense import build_index
from rag_pipeline import select_contexts, generate_answer
from common.llm_client import LLMClient

TEST = HERE / "data/questions_test.jsonl"
OUTPUT = HERE / "predictions_test.jsonl"


def load_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main():
    questions = load_jsonl(TEST)

    if len(questions) != 100 or len({q["id"] for q in questions}) != 100:
        raise ValueError("Expected exactly 100 distinct test questions")

    completed = {}
    if OUTPUT.exists():
        for record in load_jsonl(OUTPUT):
            completed[record["id"]] = record

    chunks = recursive_chunks(load_corpus(), 300, 50)
    bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

    llm = LLMClient()
    matrix, built = build_index("r3_rec300", chunks, llm)

    if len(matrix) != len(chunks):
        raise ValueError("Cached embeddings do not match the current chunks")

    timings = []

    for number, q in enumerate(questions, 1):
        if q["id"] in completed:
            print(f'[{number}/100] {q["id"]} already done', flush=True)
            continue

        start = time.perf_counter()

        selected = select_contexts(
            q["question"], chunks, bm25, matrix, llm
        )

        answer = generate_answer(
            q["question"], selected, llm
        )

        record = {
            "id": q["id"],
            "answer": answer,
            "retrieved_doc_ids": [c["doc_id"] for c in selected],
        }

        with OUTPUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        timings.append(time.perf_counter() - start)

        print(
            f'[{number}/100] {q["id"]}: '
            f'{len(answer)} chars, {timings[-1]:.2f}s',
            flush=True,
        )

    records = load_jsonl(OUTPUT)
    ids = [r["id"] for r in records]
    expected = {q["id"] for q in questions}

    assert len(records) == 100
    assert len(set(ids)) == 100
    assert set(ids) == expected

    assert all(
        isinstance(r["answer"], str)
        and r["answer"]
        and len(r["retrieved_doc_ids"]) == 5
        and len(set(r["retrieved_doc_ids"])) == 5
        for r in records
    )

    print(f"Validated {len(records)} predictions: {OUTPUT}")

    if timings:
        print(
            "Median end-to-end seconds per new question: "
            f"{np.median(timings):.3f}"
        )

    print("Gateway usage this run:", llm.usage_summary())


if __name__ == "__main__":
    main()
