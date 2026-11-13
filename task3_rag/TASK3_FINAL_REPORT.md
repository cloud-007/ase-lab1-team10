# Task 3 — RAG under the microscope

Group 10 · Agentic Software Engineering · Lab 1 · WS 2026/27

## Experimental setup

Corpus: 932 PaderQueue documents. Development set: 150 questions, 30 each in identifier, paraphrase, multi-hop, version and unanswerable categories. Retrieval metrics below use k=5 and exclude the 30 unanswerable questions (n=120). R1 and R3 use the same recursive 300-character chunks with 50-character overlap. Dense configurations use the same gateway embedding model. Results are development-set results, not test-set accuracy.

## Retrieval comparison (context recall / context precision @5)

| Configuration | Chunks | Identifier | Multi-hop | Paraphrase | Version | Overall |
|---|---:|---:|---:|---:|---:|---:|
| R1 BM25, recursive 300 | 2562 | .967 / .726 | .483 / .814 | .300 / .135 | .633 / .633 | .596 / .577 |
| R2 fixed 600, dense | 1202 | .567 / .468 | .000 / .000 | .900 / .717 | .667 / .576 | .533 / .440 |
| R3 recursive 300, dense | 2562 | .600 / .550 | .050 / .056 | .900 / .779 | .700 / .592 | .562 / .494 |
| R3 recursive 600, dense | 1208 | .567 / .484 | .033 / .028 | .867 / .647 | .700 / .569 | .542 / .432 |
| R3 recursive 1000, dense | 965 | .567 / .484 | .083 / .043 | .933 / .741 | .667 / .583 | .562 / .463 |
| R4 hybrid, BM25 weight .25 | 2562 | .767 / .684 | .233 / .467 | .833 / .686 | .733 / .622 | **.642 / .615** |
| R4 hybrid, BM25 weight .50 | 2562 | .867 / .716 | .433 / .622 | .533 / .386 | .700 / .647 | .633 / .592 |
| R4 hybrid, BM25 weight .75 | 2562 | .900 / .683 | .450 / .741 | .433 / .302 | .633 / .633 | .604 / .590 |
| R5 query rewriting, BM25 weight .25 | 2562 | .767 / .684 | .233 / .467 | .833 / .686 | .733 / .620 | .642 / .614 |

R4 weights were selected using the development questions; this tuning is disclosed. Weight .25 gives the highest overall recall and precision among the tested hybrids, although .50/.75 are better on multi-hop and identifier recall. R5 rewrote 32/150 questions and made 182 query embedding calls, but did not improve recall. This is a measured negative result, not evidence that rewriting is always ineffective.

### Chunk-size comparison

R3 recursive 300 and 1000 both reach .562 overall recall; recursive 300 has higher precision (.494 versus .463). Recursive 600 gives .542 recall. Differences are small, so we do not claim a decisive chunk-size winner. Recursive 300 was retained for R1/R3/R4 comparisons and gives more granular retrieval at greater indexing cost.

## Indexing and query latency

| Configuration | Indexing time | Median retrieval query time | Caveat |
|---|---:|---:|---|
| R1 BM25 | 0.162 s | 0.009 s | Local index |
| R2 fixed 600 dense | 18.539 s | Not recorded | Mean 0.511 s; rate-limit outliers |
| R3 recursive 300 dense | 62.766 s | 0.081 s | Uncached indexing run |
| R3 recursive 600 dense | 19.817 s | 0.083 s | Uncached indexing run |
| R3 recursive 1000 dense | 12.225 s | 0.092 s | Uncached indexing run |
| R4 hybrid .25/.50/.75 | 0.092 s BM25 + 114.413 s dense | 0.116 s | Shared index and query embeddings across weight sweep; dense uncached |
| R5 rewrite | 0.080 s BM25; dense index cached | 0.120 s | Dense index time not measured in this run |

Gateway rate-limit retries substantially increased some mean and maximum times. R4 indexing timing should not be counted three times for its three weight settings. R5's cached indexing cannot be compared directly to uncached indexing.

### Token usage and answer-generation cost

A complete test-prediction run processed 100 questions, made 200 gateway calls, and used 46,688 prompt tokens plus 5,592 completion tokens: **52,280 tokens total, or 522.8 per question**. Reported gateway charge: **$0.00746 total, or $0.0000746 per question**. Median **end-to-end** test-question latency was **2.476 s**, which includes retrieval and answer generation and is not comparable to retrieval-only median times above. These are measured for the test-generation run; token counts for all R1–R5 retrieval configurations and the separate judge runs were not measured and must not be presented as though they were.

## End-to-end answer evaluation

The same stratified 50-question development sample was used for R1 and R4, with `judge.py --sample 50`. Faithfulness excludes abstentions.

| Measure | R1 BM25 | R4 hybrid .25 |
|---|---:|---:|
| Correctness | .560 (28/50) | **.620 (31/50)** |
| Faithfulness, non-abstentions | .988 | .950 |
| Answer relevance | .557 | **.595** |
| Abstention rate | .600 | **.500** |

| Category | R1 correct / 10 | R4 correct / 10 |
|---|---:|---:|
| Identifier | 8 | 5 |
| Paraphrase | 3 | 9 |
| Multi-hop | 0 | 0 |
| Version | 7 | 7 |
| Unanswerable | 10 | 10 |

Both configurations abstained on all 10 multi-hop questions. High faithfulness on non-abstained answers does not imply high overall correctness. R4 improves paraphrases markedly but sacrifices identifier correctness relative to R1.

## Failure analysis and judge verification

See `task3_manual_review_and_failures.md` for the ten judge checks and five detailed cases. Summary:

| Question | Likely cause | Observation |
|---|---|---|
| dev008 | Generation/context interpretation | Retrieved value 10, but answer was incorrect |
| dev025 | Retrieval / missing second hop | Incident retrieved, linked error's permanent fix not supplied |
| dev030 | Retrieval | Other incidents retrieved instead of target incident |
| dev068 | Retrieval | Related discussion retrieved, authoritative error fix missing |
| dev087 | Retrieval and generation | Setting from wrong component used in answer |

The team's manual verification and sign-off on ten judge decisions remains necessary; a prepared analysis file alone does not prove a human team member reviewed it.

## Deployment recommendation

Deploy **R4 hybrid with BM25 weight .25 and dense weight .75** among the tested configurations: highest observed overall retrieval recall (.642) and precision (.615), and higher answer correctness (.620) than R1 (.560). R1 is preferable for some identifier-focused queries; future improvements could use query-type routing or more reliable multi-hop retrieval. R5 as implemented adds complexity and query embedding calls without measurable recall improvement. Deployment should acknowledge the 0/10 multi-hop correctness result, not assume readiness for all query types.

## Test predictions

The test-generation script reported validation of 100 records in `task3_rag/predictions_test.jsonl`, with `id`, `answer` and `retrieved_doc_ids`. Test ground truth is unavailable, so no test correctness or recall score is claimed. Confirm the file is present in the merged branch and the final ZIP.


## Final checklist

- [x] Add the final report and `task3_manual_review_and_failures.md` to the repository. Both files are present in `main`.
- [ ] Have a teammate independently verify ten judge decisions and record sign-off. The analysis file exists, but sign-off has not been confirmed.
- [ ] Confirm R5 meets the allowed query-rewriting option and check its second retrieval step.
- [ ] Confirm whether additional token estimates are required. The measured test-generation tokens and costs are reported above; other quantities are not fully measured.
- [ ] Merge and verify the remaining Task 3 changes. The original Task 3 files are in `main`, but the corrected R4 evaluation from issue #21 is still awaiting PR approval.
- [ ] Check the final submission ZIP for required code, results, and `predictions_test.jsonl`. Exclude secrets (`.env`), virtual environments, embedding caches, and temporary ZIP files. Optional local debugging logs may be excluded, but **keep the required Task 2 complete-run logs and Task 4 execution/verification traces**.
- [ ] Record the Task 3 video segment and explain how AI assistance was used.

## Two-minute video script (approximate)

“For Task 3, we built and compared five retrieval configurations over 932 fictional PaderQueue documents. The development set had 150 questions, covering identifiers, paraphrases, multi-hop, version questions and unanswerable questions. We evaluated retrieval at k equals five.

R1 uses BM25; R2 uses fixed-size chunks and dense embeddings; and R3 uses recursive chunks and dense embeddings. We compared three R3 chunk sizes: 300, 600 and 1000. Their overall recall was similar, with 300 giving the best precision among the R3 settings.

We then combined BM25 and dense retrieval in R4 using normalized weighted scores. We tested BM25 weights of 0.25, 0.50 and 0.75. The 0.25 weight gave our highest overall recall, 0.642, and precision, 0.615. Our R5 query-rewriting method rewrote 32 questions but did not improve retrieval recall.

For end-to-end evaluation, we generated answers for the same 50 development questions using R1 and R4 and scored them with the provided LLM judge. R4 achieved 62 percent correctness compared with 56 percent for R1. R4 was much better on paraphrases, but R1 remained stronger on exact identifiers.

Our main failure pattern was multi-hop reasoning: both systems got zero of ten multi-hop questions correct. We examined cases where the first incident was retrieved but the linked error documentation was missing, as well as cases where the model used the wrong setting. We recommend R4 overall, while acknowledging this limitation and the need for stronger multi-step retrieval. We generated and validated 100 test predictions. We used AI assistance for coding and analysis, and checked the results using the provided evaluation scripts.”
