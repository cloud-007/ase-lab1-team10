# Task 3: R4 judge review and failure analysis

Source: uploaded `task3_final_results.zip`. Review performed against the supplied ground truth, generated answer, judge reason, and the five actual generation contexts. This is an assistant-assisted review; a team member should independently confirm ten decisions to meet the manual-check requirement.

## Ten judge decisions to check

| ID | Category | Judge correct | Review | Reason |
|---|---|---:|---|---|
| dev004 | paraphrase | 1 | Agree | Correct setting `dlq.max_retries` appears in context. |
| dev008 | identifier | 0 | Agree | Wrong: answer denies default; 4.x context contains `interval_s = 10` (example setting, not an explicit default row). |
| dev013 | unanswerable | 1 | Agree | Correct exact required abstention for an unanswerable question. |
| dev025 | multi-hop | 0 | Agree | Wrong abstention: incident references PQ-E1915 but error-catalogue fix absent. |
| dev030 | multi-hop | 0 | Agree | Wrong abstention: requested incident INC-2025-175 not in the five contexts. |
| dev042 | multi-hop | 0 | Agree | Wrong abstention: forum mentions PQ-E2051 but no 4.x compactor default. |
| dev062 | identifier | 0 | Agree | Wrong: answer relies on forum/incident and omits required connection-leak fix. |
| dev068 | identifier | 0 | Agree | Wrong: error catalogue fix missing, only forum and incidents. |
| dev073 | identifier | 1 | Agree | Correct default 85; percent sign represents unit. |
| dev087 | paraphrase | 0 | Agree | Wrong: wrong component snapshotter instead of scheduler; relevant setting missing. |

### Evidence for each check

#### dev004 — paraphrase
- Question: Which setting controls how many times the mechanism that parks messages nobody could process tries again before it gives up?
- Ground truth: The setting `dlq.max_retries`.
- Generated answer: `dlq.max_retries` in PaderQueue 3.x controls retries by the dead-letter handler before it gives up.
- Judge: correctness 1.0; It identifies `dlq.max_retries` as the setting controlling retries before giving up.
- Manual-review rationale: Correct setting `dlq.max_retries` appears in context.

#### dev008 — identifier
- Question: In PaderQueue 4.x, what does `metrics.interval_s` default to?
- Ground truth: `10`
- Generated answer: In PaderQueue 4.x, the documentation does not specify the default for `metrics.interval_s`.
- Judge: correctness 0.0; It says the default is not specified, but the ground truth is 10.
- Manual-review rationale: Wrong: answer denies default; 4.x context contains `interval_s = 10` (example setting, not an explicit default row).

#### dev013 — unanswerable
- Question: What is the default value of `spool.queue_capacity`?
- Ground truth: The documentation does not contain this information.
- Generated answer: The documentation does not contain this information.
- Judge: correctness 1.0; It clearly states that the documentation does not contain the information.
- Manual-review rationale: Correct exact required abstention for an unanswerable question.

#### dev025 — multi-hop
- Question: Which permanent fix does the documentation recommend for the error that caused incident INC-2024-171?
- Ground truth: The incident was caused by PQ-E1915. Fix: Throttling protects the node. Add capacity or reduce load. As a last resort, raise `metrics.threshold_percent`.
- Generated answer: The documentation does not contain this information.
- Judge: correctness 0.0; The documentation gives a fix for PQ-E1915: add capacity or reduce load, and only as a last resort raise `metrics.threshold_percent`.
- Manual-review rationale: Wrong abstention: incident references PQ-E1915 but error-catalogue fix absent.

#### dev030 — multi-hop
- Question: Which permanent fix does the documentation recommend for the error that caused incident INC-2025-175?
- Ground truth: The incident was caused by PQ-E2617. Fix: Check the logs for the underlying failure first. If the failures are transient, increase `dlq.max_retries`.
- Generated answer: The documentation does not contain this information.
- Judge: correctness 0.0; The documentation recommends checking the logs for the underlying failure and, if failures are transient, increasing `dlq.max_retries`.
- Manual-review rationale: Wrong abstention: requested incident INC-2025-175 not in the five contexts.

#### dev042 — multi-hop
- Question: What is the current default value of the setting that the error catalogue recommends changing for PQ-E2051?
- Ground truth: `compactor.compression` defaults to `lz4` in 4.x.
- Generated answer: The documentation does not contain this information.
- Judge: correctness 0.0; It says the information is unavailable, but the current default is `lz4` for `compactor.compression` in 4.x.
- Manual-review rationale: Wrong abstention: forum mentions PQ-E2051 but no 4.x compactor default.

#### dev062 — identifier
- Question: What should I do when PaderQueue logs PQ-E3177?
- Ground truth: Check for connection leaks in clients (connections that are never closed). If the load is legitimate, increase `scheduler.max_connections`.
- Generated answer: A PaderQueue 4.1 forum user reported that changing `scheduler.max_retries` fixed a similar issue, but this is anecdotal advice. The 4.x incident reports confirm repeated PQ-E3177 errors and processing failures, but don’t specify a fix.
- Judge: correctness 0.0; It omits the recommended checks for unclosed client connections and the instruction to increase `scheduler.max_connections` if the load is legitimate.
- Manual-review rationale: Wrong: answer relies on forum/incident and omits required connection-leak fix.

#### dev068 — identifier
- Question: What is the recommended fix for PQ-E4007?
- Ground truth: Set `broker.compression` to the same value on all nodes, then restart them one after another.
- Generated answer: The documentation says to apply the fix recommended in the PQ-E4007 error catalogue, but it doesn’t include that fix.
- Judge: correctness 0.0; It omits the recommended fix: set `broker.compression` to the same value on all nodes, then restart them one after another.
- Manual-review rationale: Wrong: error catalogue fix missing, only forum and incidents.

#### dev073 — identifier
- Question: In PaderQueue 4.x, what does `coordinator.threshold_percent` default to?
- Ground truth: `85`
- Generated answer: `coordinator.threshold_percent` defaults to `85%` in PaderQueue 4.x.
- Judge: correctness 1.0; It gives the default as 85, with the percent sign merely expressing the threshold’s unit.
- Manual-review rationale: Correct default 85; percent sign represents unit.

#### dev087 — paraphrase
- Question: Which setting limits how many clients can talk to the part that holds back messages until a later point in time in parallel?
- Ground truth: The setting `scheduler.max_connections`.
- Generated answer: `snapshotter.max_connections` (PaderQueue 2.x) limits the number of connections to the snapshotter.
- Judge: correctness 0.0; The answer names `snapshotter.max_connections`, but the setting is `scheduler.max_connections`.
- Manual-review rationale: Wrong: wrong component snapshotter instead of scheduler; relevant setting missing.

## Five incorrect answers: failure attribution

The classification is based on whether the required answer evidence appears in the five *generation contexts*, not solely on whether a related document was retrieved.

| ID | Failure type | Evidence | Proposed fix |
|---|---|---|---|
| dev008 | Generation / context interpretation | 4.x context contains `[metrics] interval_s = 10`, but generator says default unspecified; 2.x context shows 300, creating version ambiguity. | Keep full default-property rows; prompt for explicit version-specific evidence and avoid declaring unknown when 4.x value is visible. |
| dev025 | Retrieval (second hop) | Correct incident INC-2024-171 and linked PQ-E1915 are in context, but PQ-E1915 catalogue fix is absent. | Follow extracted error code with a second exact-code retrieval and include its catalogue entry. |
| dev030 | Retrieval (first hop) | No supplied context contains INC-2025-175; five unrelated incidents were retrieved. | Exact-match identifier boosting for incident ID, then follow the referenced error code. |
| dev068 | Retrieval (authoritative source) | Forum and postmortems mention PQ-E4007 but error-catalogue fix is not in context. | Prioritize error catalogue for fix questions and fetch exact PQ-E4007 page. |
| dev087 | Retrieval + wrong-answer generation | Contexts omit scheduler.max_connections; generator selects snapshotter.max_connections from PaderQueue 2.x. | Retrieve correct scheduler setting using component-aware reranking; reject answers about mismatched components. |

## End-to-end comparison (50 questions each)

| Metric | R1 BM25 | R4 hybrid |
|---|---:|---:|
| Correctness | 0.560 | 0.620 |
| Faithfulness, non-abstentions only | 0.988 | 0.950 |
| Relevance | 0.557 | 0.595 |
| Abstention rate | 0.600 | 0.500 |

Category correctness: identifier R1 0.8 / R4 0.5; paraphrase 0.3 / 0.9; version 0.7 / 0.7; unanswerable 1.0 / 1.0; multi-hop 0.0 / 0.0.

## Interpretation and recommendation

Recommend R4 (0.25 BM25 + 0.75 dense) for overall performance: higher development recall and higher end-to-end correctness than R1. R1 remains stronger for exact identifier questions. R5 query rewriting was evaluated but did not improve overall recall (0.642 for both R4 and R5), so its additional embedding calls are not justified for deployment. Multi-hop performance remains unacceptable for either candidate and requires explicit second-hop retrieval before production use.

## Remaining deliverables

- Confirm the ten judge decisions manually as a team member; record reviewer and date.
- Complete token-per-query estimates and cost/latency table; distinguish cached and uncached indexing.
- Generate and validate 100 test predictions in the exact prescribed schema.
- Update submission README/STATUS and record brief video explanation.
