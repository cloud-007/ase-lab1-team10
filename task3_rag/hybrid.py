"""R4: normalized weighted BM25 + dense retrieval on identical R3 chunks.
Run from repository root: uv run python task3_rag/hybrid.py --weights 0.25 0.5 0.75
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from bm25 import tokenize
from chunking import load_corpus, recursive_chunks
from dense import build_index, normalize
from common.llm_client import LLMClient


def minmax(values):
    values = np.asarray(values, dtype=np.float32)
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-12:
        return np.zeros_like(values)
    return (values - lo) / (hi - lo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', nargs='+', type=float, default=[0.25, 0.5, 0.75],
                        help='BM25 weights (dense weight is 1 - BM25 weight)')
    parser.add_argument('--k', type=int, default=20)
    parser.add_argument('--questions', type=Path, default=HERE / 'data/questions_dev.jsonl')
    args = parser.parse_args()
    if any(not 0 <= w <= 1 for w in args.weights):
        parser.error('Weights must be between 0 and 1')
    if args.k < 1:
        parser.error('--k must be positive')

    results = HERE / 'results'
    results.mkdir(exist_ok=True)
    start = time.perf_counter()
    chunks = recursive_chunks(load_corpus(), 300, 50)
    bm25 = BM25Okapi([tokenize(c['text']) for c in chunks])
    bm25_build = time.perf_counter() - start
    llm = LLMClient()
    # Reuses R3 embeddings; cache must match the corpus, model and chunking settings.
    matrix, dense_build = build_index('r3_rec300', chunks, llm)
    if len(matrix) != len(chunks):
        raise ValueError('Cached embedding count differs from chunk count; clear stale cache')
    questions = [json.loads(line) for line in args.questions.open(encoding='utf-8') if line.strip()]
    writers = {}
    latencies = []
    try:
        for w in args.weights:
            label = f'r4_hybrid_bm25_{int(round(w * 100)):03d}'
            writers[w] = (label, (results / f'retrieval_{label}.jsonl').open('w', encoding='utf-8'))
        for q in questions:
            t0 = time.perf_counter()
            bm = minmax(bm25.get_scores(tokenize(q['question'])))
            query_vec = normalize(np.asarray(llm.embed([q['question']]), dtype=np.float32))[0]
            dense = minmax(matrix @ query_vec)
            latencies.append(time.perf_counter() - t0)
            for w, (_, file) in writers.items():
                scores = w * bm + (1 - w) * dense
                top = np.argsort(-scores, kind='stable')[:args.k]
                file.write(json.dumps({'id': q['id'], 'retrieved_doc_ids':
                                       [chunks[int(i)]['doc_id'] for i in top]}) + '\n')
    finally:
        for _, file in writers.values():
            file.close()
    for w, (label, _) in writers.items():
        timing = {'config': label, 'chunks': len(chunks), 'bm25_index_seconds': bm25_build,
                  'dense_index_seconds': dense_build, 'embedding_index_cached': dense_build is None,
                  'median_query_seconds': float(np.median(latencies)),
                  'mean_query_seconds': float(np.mean(latencies)),
                  'query_embedding_calls': len(questions), 'bm25_weight': w,
                  'dense_weight': 1-w}
        (results / f'timing_{label}.json').write_text(json.dumps(timing, indent=2))
        print(json.dumps(timing, indent=2))


if __name__ == '__main__':
    main()
