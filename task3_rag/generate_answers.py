
"""Generate development answers for Task 3 retrieval configurations.

For R4, use the same chunk selection and answer generation as predict_test.py.
For R1, preserve the original BM25-based development evaluation.

Run from repository root:
  uv run python task3_rag/generate_answers.py --config r4_hybrid_bm25_025
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from bm25 import tokenize
from chunking import load_corpus, recursive_chunks
from dense import build_index
from judge import ABSTENTION, stratified_sample
from rag_pipeline import select_contexts, generate_answer
from common.llm_client import LLMClient, count_tokens


def load_jsonl(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def generate_original_answer(question, doc_ids, by_doc, llm):
    """Preserve the original BM25-based generation path for R1."""
    contexts = []

    for doc_id in list(dict.fromkeys(doc_ids))[:5]:
        candidates = by_doc.get(doc_id, [])

        if not candidates:
            raise ValueError(
                f"Unknown document ID in retrieval output: {doc_id}"
            )

        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            ranker = BM25Okapi(
                [tokenize(c["text"]) for c in candidates]
            )
            scores = ranker.get_scores(tokenize(question))
            chosen = candidates[
                int(max(range(len(candidates)), key=lambda j: scores[j]))
            ]

        contexts.append(chosen["text"])

    numbered = "\n\n".join(
        f"[Context {j}]\n{text}"
        for j, text in enumerate(contexts, 1)
    )

    system = (
        "Answer the user question using ONLY the supplied documentation. "
        "Do not invent facts. If the documentation does not contain the answer, "
        f"reply exactly: {ABSTENTION} "
        "Keep the answer concise and mention relevant identifiers or versions."
    )

    user = f"Documentation:\n{numbered}\n\nQuestion: {question}"

    answer = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=1200,
    ).strip()

    if not answer:
        raise RuntimeError("Empty answer; rerun to resume")

    return answer, contexts, count_tokens(system + user)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--retrieval", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--sample", type=int, default=50)
    args = parser.parse_args()

    is_r4 = args.config == "r4_hybrid_bm25_025"

    retrieval_path = (
        args.retrieval
        or HERE / "results" / f"retrieval_{args.config}.jsonl"
    )

    default_name = (
        "answers_r4_aligned.jsonl"
        if is_r4
        else f"answers_{args.config}.jsonl"
    )
    out = args.out or HERE / "results" / default_name

    # Prevent accidental overwriting of the original R4 evaluation.
    original_r4 = HERE / "results" / "answers_r4_hybrid_bm25_025.jsonl"
    if is_r4 and out.resolve() == original_r4.resolve():
        parser.error("Use a different output path for aligned R4 answers")

    if not is_r4 and not retrieval_path.is_file():
        parser.error(f"Retrieval file not found: {retrieval_path}")

    out.parent.mkdir(parents=True, exist_ok=True)

    questions = stratified_sample(
        load_jsonl(HERE / "data" / "questions_dev.jsonl"),
        args.sample,
    )

    chunks = recursive_chunks(load_corpus(), 300, 50)
    llm = LLMClient(
        log_path=Path("logs") / f"generate_{args.config}_aligned.jsonl"
    )

    if is_r4:
        # Exactly the same retrieval setup as predict_test.py.
        bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
        matrix, built = build_index("r3_rec300", chunks, llm)

        if len(matrix) != len(chunks):
            raise ValueError(
                "Cached embeddings do not match the current chunks"
            )
    else:
        retrieval_rows = {
            r["id"]: r["retrieved_doc_ids"]
            for r in load_jsonl(retrieval_path)
        }

        missing = [
            q["id"] for q in questions
            if q["id"] not in retrieval_rows
        ]

        if missing:
            parser.error(
                f"Missing retrieval rows for {missing[:5]}"
            )

        by_doc = defaultdict(list)
        for chunk in chunks:
            by_doc[chunk["doc_id"]].append(chunk)

    completed = {}
    if out.exists():
        completed = {
            r["id"]: r
            for r in load_jsonl(out)
        }

    prompt_tokens = 0

    for i, q in enumerate(questions, 1):
        if q["id"] in completed:
            print(
                f'[{i}/{len(questions)}] {q["id"]} already saved'
            )
            continue

        if is_r4:
            selected = select_contexts(
                q["question"], chunks, bm25, matrix, llm
            )

            answer = generate_answer(
                q["question"], selected, llm
            )

            contexts = [chunk["text"] for chunk in selected]
            doc_ids = [chunk["doc_id"] for chunk in selected]

        else:
            answer, contexts, used_tokens = generate_original_answer(
                q["question"],
                retrieval_rows[q["id"]],
                by_doc,
                llm,
            )
            prompt_tokens += used_tokens
            doc_ids = list(dict.fromkeys(retrieval_rows[q["id"]]))[:5]

        record = {
            "id": q["id"],
            "answer": answer,
            "contexts": contexts,
            "retrieved_doc_ids": doc_ids,
        }

        with out.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

        print(f'[{i}/{len(questions)}] {q["id"]} saved')

    print(f"Completed: {out}")
    if not is_r4:
        print(f"Estimated prompt tokens in new calls: {prompt_tokens}")
    print("Gateway usage for this run:", llm.usage_summary())


if __name__ == "__main__":
    main()
