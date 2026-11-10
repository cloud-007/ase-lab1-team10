"""R5: R4 (25% BM25 + 75% dense) with evidence-based query rewriting.

From repository root:
  python -m uv run python task3_rag/r5.py
  python -m uv run python task3_rag/evaluate.py task3_rag/results/retrieval_r5_rewrite.jsonl --k 5 --json task3_rag/results/metrics_r5_rewrite.json

A first hybrid search retrieves evidence. If the question contains an exact incident
or error identifier, inspect the corresponding retrieved document and rewrite the
query using linked error codes or configuration keys. A second hybrid search then
finds documentation for that linked identifier. No development-set labels are used.
"""
import argparse
import json
import re
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
from dense import build_index, normalize
from hybrid import minmax
from common.llm_client import LLMClient

INCIDENT = re.compile(r'\bINC-20\d\d-\d+\b', re.I)
ERROR = re.compile(r'\bPQ-E\d+\b', re.I)
# Dotted/underscored configuration keys (avoid URLs and prose).
SETTING = re.compile(r'\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b')


def unique_docs(indices, chunks, limit):
    seen, out = set(), []
    for i in indices:
        doc = chunks[int(i)]['doc_id']
        if doc not in seen:
            seen.add(doc)
            out.append(doc)
            if len(out) == limit:
                break
    return out


def linked_query(question, ranked_indices, chunks):
    """Rewrite only when a retrieved source matches the identifier in the question."""
    incidents = {s.upper() for s in INCIDENT.findall(question)}
    errors = {s.upper() for s in ERROR.findall(question)}
    if not (incidents or errors):
        return None
    for idx in ranked_indices[:20]:
        chunk = chunks[int(idx)]
        content = chunk['text']
        # Require the document title/header to identify the requested source,
        # rather than following arbitrary references in unrelated results.
        title = content.split('\n', 1)[0].upper()
        if incidents and any(s in title for s in incidents):
            linked = [s for s in ERROR.findall(content) if s.upper() not in errors]
            if linked:
                return f'{linked[0]} error catalogue recommended permanent fix resolution'
        if errors and any(s in title for s in errors):
            linked = SETTING.findall(content)
            if linked:
                return f'{linked[0]} current default configuration version'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--questions', type=Path, default=HERE / 'data/questions_dev.jsonl')
    parser.add_argument('--weight', type=float, default=0.25)
    parser.add_argument('--k', type=int, default=20)
    parser.add_argument('--out', type=Path, default=HERE / 'results/retrieval_r5_rewrite.jsonl')
    args = parser.parse_args()
    if not 0 <= args.weight <= 1 or args.k < 5:
        parser.error('Weight must be 0..1 and k must be at least 5')

    chunks = recursive_chunks(load_corpus(), 300, 50)
    t = time.perf_counter()
    bm25 = BM25Okapi([tokenize(c['text']) for c in chunks])
    bm25_seconds = time.perf_counter() - t
    llm = LLMClient()
    matrix, dense_seconds = build_index('r3_rec300', chunks, llm)
    if len(matrix) != len(chunks):
        raise ValueError('Stale embedding cache: number of vectors differs from chunks')

    def search(query):
        bm = minmax(bm25.get_scores(tokenize(query)))
        vec = normalize(np.asarray(llm.embed([query]), dtype=np.float32))[0]
        dense = minmax(matrix @ vec)
        return args.weight * bm + (1 - args.weight) * dense

    questions = [json.loads(x) for x in args.questions.read_text(encoding='utf-8').splitlines() if x.strip()]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    elapsed, rewrites = [], 0
    with args.out.open('w', encoding='utf-8') as out:
        for q in questions:
            start = time.perf_counter()
            first = search(q['question'])
            ranked = np.argsort(-first, kind='stable')
            rewrite = linked_query(q['question'], ranked, chunks)
            if rewrite:
                rewrites += 1
                second = search(rewrite)
                # Keep original-query evidence while promoting relevant second-hop docs.
                # 0.7 is a fixed design choice, not fitted to ground-truth labels.
                combined = np.maximum(first, 0.7 * second)
                order = np.argsort(-combined, kind='stable')
            else:
                order = ranked
            ids = unique_docs(order, chunks, args.k)
            out.write(json.dumps({'id': q['id'], 'retrieved_doc_ids': ids}) + '\n')
            elapsed.append(time.perf_counter() - start)
    timing = {
        'config': 'r5_rewrite', 'chunks': len(chunks), 'bm25_weight': args.weight,
        'dense_weight': 1 - args.weight, 'bm25_index_seconds': bm25_seconds,
        'dense_index_seconds': dense_seconds,
        'embedding_index_cached': dense_seconds is None,
        'questions': len(questions), 'rewritten_questions': rewrites,
        'query_embedding_calls': len(questions) + rewrites,
        'median_query_seconds': float(np.median(elapsed)),
        'mean_query_seconds': float(np.mean(elapsed)),
        'approx_query_tokens': 'not measured; count original and rewritten queries separately',
    }
    (args.out.parent / 'timing_r5_rewrite.json').write_text(json.dumps(timing, indent=2), encoding='utf-8')
    print(json.dumps(timing, indent=2))


if __name__ == '__main__':
    main()
