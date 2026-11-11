# Task 3 – RAG under the microscope: scope and status

Group 10 · Agentic Software Engineering WS 2026/27 · Lab 1 · Status written by Akshay (role A) · Branch: `task3-chunking-embeddings`

> The lab's own instructions are in `README.md` in this folder. This file is the group's overview and progress tracker.

## 1. What Task 3 is (14 points)

We compare five ways of finding the right documents for a question, measure how well each one works, and explain why. Task 3 does not depend on Tasks 1, 2 or 4.

**Corpus.** PaderQueue, a fictional message queue: 932 documents (170 config pages, 77 error pages, 36 CLI pages, 14 changelogs, 120 incident postmortems, 500 forum threads, 15 runbooks). No model has seen them in training.

**Questions.** 150 dev questions with ground truth, 30 each in five categories: identifier, paraphrase, multi-hop, version, unanswerable. Plus 100 test questions without ground truth. Each dev question lists *groups* of relevant documents; a group is one piece of needed information, and any document in the group covers it.

**Scripts we were given.** `evaluate.py` scores retrieval (context recall and precision at k, no LLM). `judge.py` scores end-to-end answers with an LLM judge (faithfulness, answer relevance, correctness). There is no starter code for the retrievers.

## 2. What the lab sheet requires

1. Implement and evaluate five retrieval configurations:
   - R1: BM25, on the same chunks as R3
   - R2: fixed-size chunking + dense embeddings
   - R3: recursive chunking (headings, paragraphs, sentences) + dense embeddings
   - R4: hybrid of R1 and R3, with normalized and weighted scores; the weight must be justified
   - R5: R4 plus ONE of: cross-encoder re-ranking, query rewriting or HyDE, semantic chunking
2. Chunk size: compare three settings for R3 with the same overlap.
3. Retrieval evaluation with `evaluate.py`, overall and per category.
4. End-to-end evaluation: for the two best configurations, generate answers for 50 dev questions, score them with `judge.py --sample 50`, and check 10 judge decisions by hand.
5. Cost and latency per configuration: indexing time, latency per query, approximate tokens per query.
6. Failure analysis: 5 questions the best system gets wrong; retrieval failure or generation failure, and why.
7. Recommendation: which configuration we would deploy, and why.
8. Predictions for the 100 test questions with the best configuration, in `task3_rag/predictions_test.jsonl`.

**Grading.** 6 points for R1–R5 implemented correctly; 3 for the evaluation (per-category table, chunk size comparison, cost and latency, judge check); 3 for failure analysis and recommendation (reasoning is graded, not leaderboard rank); 2 for test predictions in the right format and better than R1. About 2 minutes of the video are about Task 3: a table of configurations by metrics per category, the failure analysis and the recommendation.

## 3. Status at a glance

| Lab-sheet item | Status | Notes |
|---|---|---|
| R1 BM25 | Done (dev) | `bm25.py`, tokenizer keeps identifiers whole |
| R2 fixed chunks + embeddings | Done (dev) | fixed 600/50 |
| R3 recursive chunks + embeddings | Done (dev) | three sizes run: 300, 600, 1000 (overlap 50) |
| R4 hybrid | Script drafted, not yet run | `hybrid.py` with weight sweep |
| R5 extra technique | Not started | not yet chosen |
| Chunk size comparison | Done (dev) | see section 5 |
| Retrieval evaluation, per category | Done for R1–R3 | still needed for R4 and R5 |
| End-to-end judge on 50 questions | Not started | two best configs, plus 10 hand checks |
| Cost and latency | Partly | indexing time and median query time for R1–R3; tokens per query not measured; R4 and R5 missing |
| Failure analysis (5 questions) | Started | one confirmed multi-hop failure pattern, 0 of 5 written up |
| Recommendation | Not started | |
| Test predictions | Not started | |
| Video segment (about 2 min) | Not started | |

Rough picture: five retrieval configurations, three are done, one is drafted, one is open. The evaluation, judging, failure analysis, recommendation and test predictions are still ahead of us.

## 4. What exists in this folder

- `chunking.py`: `load_corpus()`, `fixed_chunks(docs, size, overlap)` for R2 and `recursive_chunks(docs, size, overlap)` for R3. Every chunk starts with a header line holding the document title, type and version, and carries its `doc_id`. This matters for the version questions, where old and new config pages look almost the same.
- `dense.py <config>`: embeds the chunks with the gateway model, caches the vectors in `task3_rag/cache/` (ignored by git, never submit it), embeds each dev question, retrieves the top 20 chunks by cosine similarity, writes `results/retrieval_<config>.jsonl` and `results/timing_<config>.json`. Configs: `r2_fixed600`, `r3_rec300`, `r3_rec600`, `r3_rec1000`.
- `bm25.py`: R1. BM25 (`rank_bm25`) on the recursive 300/50 chunks. The tokenizer keeps identifiers such as `PQ-E4663`, `INC-2024-183` and `spool.buffer_bytes` as one token and also adds their parts.
- `stats.py`: document length statistics per type, used to choose chunk sizes.
- `results/`: retrieval JSONL files, `metrics_*.json` from `evaluate.py`, and timing files.

Not committed on purpose: `.env` (our API key), `cache/` (large embedding files), local run logs.

## 5. Results so far (dev set, k = 5)

Each cell is context recall / context precision. Each category has 30 questions, so one question moves a category's recall by 0.033. The 30 unanswerable questions have no relevant documents and are not part of these metrics; the judge covers them later.

| Configuration | Chunks | identifier | multi-hop | paraphrase | version | overall |
|---|---|---|---|---|---|---|
| R1 BM25 (recursive 300) | 2562 | 0.967 / 0.726 | 0.483 / 0.814 | 0.300 / 0.135 | 0.633 / 0.633 | 0.596 / 0.577 |
| R2 fixed 600/50, dense | 1202 | 0.567 / 0.468 | 0.000 / 0.000 | 0.900 / 0.717 | 0.667 / 0.576 | 0.533 / 0.440 |
| R3 recursive 300/50, dense | 2562 | 0.600 / 0.550 | 0.050 / 0.056 | 0.900 / 0.779 | 0.700 / 0.592 | 0.562 / 0.494 |
| R3 recursive 600/50, dense | 1208 | 0.567 / 0.484 | 0.033 / 0.028 | 0.867 / 0.647 | 0.700 / 0.569 | 0.542 / 0.432 |
| R3 recursive 1000/50, dense | 965 | 0.567 / 0.484 | 0.083 / 0.043 | 0.933 / 0.741 | 0.667 / 0.583 | 0.562 / 0.463 |

### Cost and latency so far

| Configuration | Indexing time | Median query time |
|---|---|---|
| R1 BM25 | 0.16 s | 0.009 s |
| R2 fixed 600 | 18.5 s | about 0.09 s (estimated: the median was not recorded for this run) |
| R3 300 | 62.8 s | 0.081 s |
| R3 600 | 19.8 s | 0.083 s |
| R3 1000 | 12.2 s | 0.092 s |

The gateway rate-limited several runs and the client retried with growing waits. That inflates indexing times a little and produced single queries of 62 s and 131 s, so always report the **median** query time and mention the retries. Dense query time is dominated by the embedding API call, not by the vector search. The gateway also caches identical requests, so a second run looks much faster; measure latency on a first run.

## 6. What the results tell us

- **The chunk size differences are small.** Overall recall is 0.533 to 0.562 across the dense runs, which is two to three questions out of 120. We should not claim a clear winner. Recursive 300 has the best overall precision (0.494) and ties for best recall, at the price of 2.7 times as many chunks and the longest indexing time. Recursive 1000 is nearly whole-document retrieval, because only the 14 changelogs are longer than 1000 characters.
- **R2 versus R3 at 600 characters is almost identical** (recall 0.533 versus 0.542). Most documents are under 800 characters, so structure-aware splitting changes little.
- **BM25 and dense search win in opposite places.** BM25 is far better on identifiers (0.967 versus 0.600) because exact tokens like `PQ-E4663` match. Dense search is far better on paraphrases (0.900 versus 0.300) because the question words differ from the document words. This is the case for the hybrid (R4).
- **Multi-hop fails for dense search.** Recall is between 0.000 and 0.083. We looked at question dev015 (incident INC-2024-183): dense retrieval returned other incident postmortems (INC-2024-180, -106, -138, ...) instead of INC-2024-183, because identifiers that differ in one digit look almost the same to an embedding model. The second hop (the error page PQ-E4663) is only mentioned inside the incident text, so a single query cannot reach it. BM25 does much better (0.483 recall, 0.814 precision); our reading is that it finds the incident from its ID but often not the error page. We have verified only this one example in detail.
- **Version questions stay at 0.63 to 0.70** for every method. Old and new config pages are near-duplicates. This is a good candidate for the failure analysis and for R5.
- **For R5**, query rewriting with a second search step (find the incident, read the error code, search for it) fits the multi-hop weakness best. A cross-encoder re-ranker is the cheaper alternative and runs locally.

## 7. How to reproduce what exists

Setup: VPN on, `uv sync`, copy `.env.example` to `.env` and put your own gateway key in it. Never commit `.env`. Every teammate uses their own key. Then, from the repo root:

```
uv run python task3_rag\dense.py r3_rec300
uv run python task3_rag\bm25.py
uv run python task3_rag\evaluate.py task3_rag\results\retrieval_r3_rec300.jsonl --k 5 --json task3_rag\results\metrics_r3_rec300.json
```

The shared interface: every configuration writes `results/retrieval_<name>.jsonl` with lines like `{"id": "dev001", "retrieved_doc_ids": [...]}` in rank order. Chunks carry their `doc_id`; `evaluate.py` removes duplicates. For building on our chunks, use `recursive_chunks(load_corpus(), 300, 50)`, which is what we call R3 for now.

The embedding cache is not in git. Anyone who needs dense vectors embeds locally (the gateway embedding model is free). The local alternative `sentence-transformers/all-MiniLM-L6-v2` (`uv sync --extra local`) is faster, but for a fair comparison all dense configurations must use the same embedding model.

## 8. Open work and proposed owners

Owners are a proposal for the group to confirm. Akshay has already done R1, R2, R3 and the chunk size runs.

| Work package | Proposed owner | Depends on |
|---|---|---|
| R4 hybrid: run `hybrid.py`, check the weight sweep, write the justification for the weight | to be assigned | R1 and R3 results (done) |
| R5: pick one technique, implement and evaluate it | to be assigned | R4 |
| Per-category table for R4 and R5, one combined results table | to be assigned | R4, R5 |
| Cost and latency for R4 and R5, plus approximate tokens per query for all configurations | to be assigned | R4, R5 |
| End-to-end evaluation: answers for 50 dev questions with the two best configurations, `judge.py --sample 50`, 10 judge decisions checked by hand | to be assigned | best two configs |
| Failure analysis: 5 wrong answers, each labelled retrieval or generation failure, with reasons | to be assigned | judge results |
| Recommendation | to be assigned | all of the above |
| `predictions_test.jsonl` for 100 test questions | to be assigned | best config |
| Video segment (about 2 min) | to be assigned | tables and analysis |

Format reminders. Answers for the judge go in JSONL as `{"id", "answer", "contexts"}`, where `contexts` are exactly the chunk texts given to the generator; if the system cannot answer it must reply exactly `The documentation does not contain this information.` Test predictions are `{"id", "answer", "retrieved_doc_ids"}` with five document IDs per question; we are graded on recall@5 and answer correctness.

## 9. Rules and cautions

- Every group member must be able to explain every part of Task 3. Plan a short walkthrough before submission.
- Say openly that we compared chunk sizes and chose the hybrid weight on the dev questions, because the README asks us not to tune on reported results without saying so.
- The gateway budget is $20 per student per month. Embeddings are free, but answer generation and the judge cost money, so spread those runs across people and watch the cost log.
- Do not submit the embedding cache, `.env` or keys. The ZIP must not contain caches over 50 MB.
- Task 3 allows AI assistants and coding agents. The video needs a short statement of which tools we used and for what.

## 10. Deadline

Everything is due Saturday, Nov 14, 23:59 on PANDA: the ZIP (including `task3_rag/` code, `results/` and `predictions_test.jsonl`) and, separately, the video.
