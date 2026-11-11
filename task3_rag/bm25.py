import json
import re
import sys
import time
from pathlib import Path

from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from chunking import load_corpus, recursive_chunks  # noqa: E402

RESULTS = HERE / "results"
QUESTIONS = HERE / "data" / "questions_dev.jsonl"

# Keeps identifiers like PQ-E4663, INC-2024-183 and spool.buffer_bytes whole.
IDENT = re.compile(r"[A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*")


def tokenize(text):
    tokens = []
    for match in IDENT.findall(text):
        token = match.lower()
        tokens.append(token)
        parts = re.split(r"[-._]", token)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p)  # also allow partial matches
    return tokens


def main():
    start = time.time()
    chunks = recursive_chunks(load_corpus(), 300, 50)  # same chunks as R3 (recursive 300/50)
    bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
    index_seconds = time.time() - start

    questions = [json.loads(line) for line in open(QUESTIONS, encoding="utf-8") if line.strip()]
    latencies = []
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "retrieval_r1_bm25.jsonl", "w", encoding="utf-8") as f:
        for q in questions:
            t = time.time()
            scores = bm25.get_scores(tokenize(q["question"]))
            top = sorted(range(len(chunks)), key=lambda i: -scores[i])[:20]
            latencies.append(time.time() - t)
            f.write(json.dumps({"id": q["id"], "retrieved_doc_ids": [chunks[i]["doc_id"] for i in top]}) + "\n")

    timing = {"config": "r1_bm25", "chunks": len(chunks), "index_seconds": index_seconds,
              "median_query_seconds": sorted(latencies)[len(latencies) // 2]}
    (RESULTS / "timing_r1_bm25.json").write_text(json.dumps(timing, indent=2))
    print(json.dumps(timing, indent=2))


if __name__ == "__main__":
    main()