import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common.llm_client import LLMClient  # noqa: E402
from chunking import load_corpus, fixed_chunks, recursive_chunks  # noqa: E402

CACHE = HERE / "cache"
RESULTS = HERE / "results"
QUESTIONS = HERE / "data" / "questions_dev.jsonl"

CONFIGS = {
    "r2_fixed600": lambda docs: fixed_chunks(docs, 600, 50),
    "r3_rec300": lambda docs: recursive_chunks(docs, 300, 50),
    "r3_rec600": lambda docs: recursive_chunks(docs, 600, 50),
    "r3_rec1000": lambda docs: recursive_chunks(docs, 1000, 50),
}


def normalize(m):
    return m / np.linalg.norm(m, axis=-1, keepdims=True)


def build_index(name, chunks, llm):
    CACHE.mkdir(exist_ok=True)
    (CACHE / ".gitignore").write_text("*\n")  # keeps the cache out of git
    path = CACHE / f"{name}.npy"
    if path.exists():
        return np.load(path), None
    start = time.time()
    parts = []
    for i in range(0, len(chunks), 32):
        batch = [c["text"] for c in chunks[i:i + 32]]
        parts.append(np.asarray(llm.embed(batch), dtype=np.float32))
        print(f"  embedded {min(i + 32, len(chunks))}/{len(chunks)}", end="\r")
    matrix = normalize(np.vstack(parts))
    np.save(path, matrix)
    return matrix, time.time() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", choices=CONFIGS)
    parser.add_argument("--k", type=int, default=20, help="chunks to retrieve per question")
    args = parser.parse_args()

    RESULTS.mkdir(exist_ok=True)
    chunks = CONFIGS[args.config](load_corpus())
    llm = LLMClient()
    matrix, built = build_index(args.config, chunks, llm)

    timing_path = RESULTS / f"timing_{args.config}.json"
    previous = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    index_seconds = built if built is not None else previous.get("index_seconds")

    questions = [json.loads(line) for line in open(QUESTIONS, encoding="utf-8") if line.strip()]
    latencies = []
    out = RESULTS / f"retrieval_{args.config}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for q in questions:
            start = time.time()
            vec = normalize(np.asarray(llm.embed([q["question"]]), dtype=np.float32))[0]
            top = np.argsort(-(matrix @ vec))[:args.k]
            latencies.append(time.time() - start)
            ids = [chunks[i]["doc_id"] for i in top]
            f.write(json.dumps({"id": q["id"], "retrieved_doc_ids": ids}) + "\n")

    timing = {
        "config": args.config,
        "chunks": len(chunks),
        "index_seconds": index_seconds,
        "mean_query_seconds": sum(latencies) / len(latencies),
        "median_query_seconds": sorted(latencies)[len(latencies) // 2],
        "max_query_seconds": max(latencies),
    }
    timing_path.write_text(json.dumps(timing, indent=2))
    print(json.dumps(timing, indent=2))


if __name__ == "__main__":
    main()