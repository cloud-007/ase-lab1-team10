"""Generate 50 stratified dev answers for a saved Task 3 retrieval configuration.

Run from repository root, e.g.:
  python -m uv run python task3_rag/generate_answers.py --config r4_hybrid_bm25_025

The retrieval JSONL supplies ranked document IDs. For each document, the most
BM25-relevant recursive-300 chunk is selected as the context. This is a
separate, explicitly documented context-selection step, applied equally to
both configurations. No ground-truth answers are shown to the generator.
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
from judge import ABSTENTION, stratified_sample
from common.llm_client import LLMClient, count_tokens


def load_jsonl(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True, help='e.g. r4_hybrid_bm25_025 or r1_bm25')
    p.add_argument('--retrieval', type=Path, help='override retrieval JSONL path')
    p.add_argument('--out', type=Path, help='override answers JSONL path')
    p.add_argument('--sample', type=int, default=50)
    args = p.parse_args()
    retrieval_path = args.retrieval or HERE / 'results' / f'retrieval_{args.config}.jsonl'
    out = args.out or HERE / 'results' / f'answers_{args.config}.jsonl'
    if not retrieval_path.is_file():
        p.error(f'Retrieval file not found: {retrieval_path}')
    out.parent.mkdir(parents=True, exist_ok=True)
    questions = stratified_sample(load_jsonl(HERE / 'data' / 'questions_dev.jsonl'), args.sample)
    retrieval = {r['id']: r['retrieved_doc_ids'] for r in load_jsonl(retrieval_path)}
    missing = [q['id'] for q in questions if q['id'] not in retrieval]
    if missing:
        p.error(f'Missing retrieval rows for {missing[:5]}')
    chunks = recursive_chunks(load_corpus(), 300, 50)
    by_doc = defaultdict(list)
    for c in chunks:
        by_doc[c['doc_id']].append(c)
    completed = {}
    if out.exists():
        completed = {r['id']: r for r in load_jsonl(out)}
    llm = LLMClient(log_path=Path('logs') / f'generate_{args.config}.jsonl')
    prompt_tokens = 0
    for i, q in enumerate(questions, 1):
        if q['id'] in completed:
            print(f'[{i}/{len(questions)}] {q["id"]} already saved')
            continue
        doc_ids = list(dict.fromkeys(retrieval[q['id']]))[:5]
        contexts = []
        for doc_id in doc_ids:
            candidates = by_doc.get(doc_id, [])
            if not candidates:
                raise ValueError(f'Unknown document ID in retrieval output: {doc_id}')
            if len(candidates) == 1:
                chosen = candidates[0]
            else:
                ranker = BM25Okapi([tokenize(c['text']) for c in candidates])
                scores = ranker.get_scores(tokenize(q['question']))
                chosen = candidates[int(max(range(len(candidates)), key=lambda j: scores[j]))]
            contexts.append(chosen['text'])
        numbered = '\n\n'.join(f'[Context {j}]\n{text}' for j, text in enumerate(contexts, 1))
        system = ('Answer the user question using ONLY the supplied documentation. '
                  'Do not invent facts. If the documentation does not contain the answer, '
                  f'reply exactly: {ABSTENTION} '
                  'Keep the answer concise and mention relevant identifiers or versions.')
        user = f'Documentation:\n{numbered}\n\nQuestion: {q["question"]}'
        prompt_tokens += count_tokens(system + user)
        answer = llm.chat([{'role': 'system', 'content': system},
                           {'role': 'user', 'content': user}], max_tokens=1200).strip()
        if not answer:
            raise RuntimeError(f'Empty answer for {q["id"]}; rerun to resume')
        record = {'id': q['id'], 'answer': answer, 'contexts': contexts}
        with out.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        print(f'[{i}/{len(questions)}] {q["id"]} saved')
    print(f'Completed: {out}')
    print(f'Estimated prompt tokens in new calls: {prompt_tokens}')
    print('Gateway usage for this run:', llm.usage_summary())


if __name__ == '__main__':
    main()
