"""Reproducible offline token estimates for Task 3, Issue #18.

Uses cl100k_base via common.llm_client.count_tokens.
These are estimates, not gateway-reported token measurements.
No API calls are made.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from common.llm_client import count_tokens
from chunking import load_corpus, fixed_chunks, recursive_chunks

docs = load_corpus()
questions = [
    json.loads(line)
    for line in (HERE / "data/questions_dev.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]

configs = {
    "r2_fixed600": fixed_chunks(docs, 600, 50),
    "r3_rec300": recursive_chunks(docs, 300, 50),
    "r3_rec600": recursive_chunks(docs, 600, 50),
    "r3_rec1000": recursive_chunks(docs, 1000, 50),
}

query_tokens = sum(count_tokens(q["question"]) for q in questions)

print(f"Development questions: {len(questions)}")
print(f"Original-query tokens (estimated): {query_tokens}")
print(f"Average original-query tokens: {query_tokens / len(questions):.2f}")
print()

for name, chunks in configs.items():
    tokens = sum(count_tokens(chunk["text"]) for chunk in chunks)
    print(f"{name}: {len(chunks)} chunks, {tokens} estimated indexing tokens")

print()
print("R1 BM25: 0 embedding API tokens (local retrieval)")
print(
    f"R2/R3/R4 original queries: {query_tokens} estimated "
    f"embedding tokens each per 150-question run"
)

r4_index_tokens = sum(
    count_tokens(chunk["text"]) for chunk in configs["r3_rec300"]
)
print(
    f"R4 shared dense index: {r4_index_tokens} estimated tokens "
    "(count once across all three weights)"
)
print(
    f"R4 shared query embeddings: {query_tokens} estimated tokens "
    "(count once across all three weights)"
)

r5_timing = json.loads(
    (HERE / "results/timing_r5_rewrite.json").read_text(encoding="utf-8")
)
print(
    f"R5: {query_tokens} estimated original-query tokens; "
    f"{r5_timing['rewritten_questions']} rewritten queries "
    "with historical token count unavailable"
)
print("R5 dense indexing: cached; no new index embedding calls in this run")

# Candidate-based approximation, not a reconstruction of historical rewrites.
# R5 produces one of these two templates using identifiers found in the corpus.
from r5 import ERROR, SETTING

candidate_rewrites = set()
for chunk in configs["r3_rec300"]:
    content = chunk["text"]
    for code in ERROR.findall(content):
        candidate_rewrites.add(
            f"{code} error catalogue recommended permanent fix resolution"
        )
    for setting in SETTING.findall(content):
        candidate_rewrites.add(
            f"{setting} current default configuration version"
        )

candidate_counts = sorted(count_tokens(q) for q in candidate_rewrites)
if not candidate_counts:
    raise ValueError("No R5 rewrite candidates found")

rewrite_count = r5_timing["rewritten_questions"]
median_candidate = candidate_counts[len(candidate_counts) // 2]
estimated_extra = rewrite_count * median_candidate

print()
print(f"R5 candidate rewrite texts: {len(candidate_counts)}")
print(
    f"R5 candidate token range: "
    f"{candidate_counts[0]}-{candidate_counts[-1]} per rewrite"
)
print(f"R5 candidate median: {median_candidate} tokens")
print(
    f"R5 illustrative additional tokens: "
    f"{rewrite_count} * {median_candidate} = {estimated_extra}"
)
print(
    f"R5 illustrative total query tokens: "
    f"{query_tokens + estimated_extra}"
)
print(
    f"R5 illustrative average per question: "
    f"{(query_tokens + estimated_extra) / len(questions):.2f}"
)
print("R5 actual historical rewritten-query tokens: unavailable")
