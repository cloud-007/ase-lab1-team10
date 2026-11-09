# Task 2 – Custom coding agent

## Project overview

This project implements a small, custom JSON-protocol coding agent that reads the
Task 1 member profiles, retrieves applicable requirements from the UPB Agentic SE
Web Style Guide, generates a standalone team website, and asks the provided site
checker to validate it. The model creates the HTML; Python embeds images, saves the
page, and returns checker feedback so the model can revise it. Completion is only
accepted after a passing full checker report.

The main files are:

| File                | Purpose                                                                             |
| ------------------- | ----------------------------------------------------------------------------------- |
| `agent.py`          | LLM conversation loop, JSON protocol parsing, four action handlers, CLI, and logs   |
| `rag.py`            | Style-guide loading, metadata-aware chunking, gateway embeddings, and cosine search |
| `style_guide/`      | The 55 Markdown style-guide documents                                               |
| `check_site.py`     | Thin CLI wrapper around the shared site checker                                     |
| `embed_images.py`   | Thin CLI wrapper around the shared image-placeholder helper                         |
| `site/index.html`   | Generated website output (default path)                                             |
| `logs/run_XX.jsonl` | Per-run model/action/observation logs                                               |

## Agent architecture

`create_agent()` initializes an `LLMClient`, builds the in-memory `StyleGuideRAG`
index using that same client, then creates `ActionHandlers` and an `AgentLoop`. The
loop sends the system prompt and website task to the model with the plain `chat()`
API. It does not use native function calling or a tools API.

For each step, the loop parses one JSON action, calls its handler, appends the
observation as a user message, and continues. A `write_site` response may append a
fenced HTML document immediately after the JSON. Checker failures are returned to
the model for correction. The loop stops after an accepted `finish`, empty human
feedback, or the configured step limit.

The system prompt and task description explain the protocol and desired site but
contain no style-guide rules. The model must request those through
`search_style_guide`.

## Installation and dependencies

From the repository root, install the project dependencies with `uv`:

```bash
uv sync
```

Python 3.11 or newer is required. Runtime dependencies used by Task 2 are:

- Python standard library
- `numpy` for the embedding matrix and cosine calculations
- `openai` and `python-dotenv` through the provided `common.llm_client` helper
- `tiktoken` through `common.llm_client.count_tokens` for chunk sizing and the observation budget

The repository also declares `rank-bm25`, `jsonschema`, `pydantic`, and `pytest`,
but Task 2's RAG and agent implementation does not use them. No LangChain,
LlamaIndex, Haystack, agent SDK, or vector database is used. Local embedding models
are not used by this implementation.

Set `UPB_AI_GATEWAY_KEY`, `UPB_AI_GATEWAY_BASE_URL`, `LAB_CHAT_MODEL`, and
`LAB_EMBEDDING_MODEL` as required by the shared client. The client also loads the
repository `.env` file when `python-dotenv` is installed. The selected chat model
can alternatively be passed with `--model`.

## Running the agent

Run from the repository root. The exact Task 1 website command is:

```bash
uv run python task2_own_agent/agent.py --content task1_opencode/content --out task2_own_agent/site/index.html
```

With no arguments, content defaults to `task1_opencode/content` and output defaults
to `task2_own_agent/site/index.html`.

### Command-line arguments

| Argument         | Default                                  | Description                                                                                    |
| ---------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `--content PATH` | `task1_opencode/content`                 | Directory with `<firstname>.md` profiles and its `images/` directory                           |
| `--out PATH`     | `task2_own_agent/site/index.html`        | Generated HTML output path                                                                     |
| `--images PATH`  | `<content>/images`                       | Optional override for the image directory                                                      |
| `--model MODEL`  | `LAB_CHAT_MODEL`                         | Chat-model override                                                                            |
| `--max-steps N`  | `25`                                     | Maximum number of model/action steps                                                           |
| `--max-tokens N` | `24000`                                  | Maximum completion tokens per chat call; set high for the full HTML response                   |
| `--k N`          | `5`                                      | Maximum number of retrieved style-guide chunks per search, further limited by the token budget |
| `--log PATH`     | next `task2_own_agent/logs/run_XX.jsonl` | Explicit log path; if already present, a numbered suffix is selected                           |
| `--task TEXT`    | Built-in website task                    | Replace the user task description                                                              |

## RAG design

### Document loading and metadata

`StyleGuideRAG` loads every `*.md` file in `style_guide/`, sorted by filename.
For each document it extracts the `SG-XX` ID, title, status, category, any version
label, and an explicit `replaced by SG-XX` pointer where present. This metadata is
attached to all chunks from that document.

### Chunking strategy, size, and overlap

Markdown is split at blank-line boundaries, with headings starting their own
sections. Nearby blocks are grouped until the configured chunk limit is reached;
oversized blocks are divided at word boundaries. The default target is **350
tokens per chunk**, measured with `count_tokens()` from the shared helper. Every
chunk carries the current heading context and source document metadata.

Chunks do **not** overlap: each Markdown block is assigned to a single chunk. A
heading is kept as metadata and also included in the embedding input, together with
the document title and chunk text. Returned chunk text remains the source Markdown.

### Embeddings and cosine similarity

All chunks are embedded at RAG initialization through `LLMClient.embed()` using the
configured gateway embedding model (`LAB_EMBEDDING_MODEL`; the run logs record the
resolved model name). The vectors are stored in a `numpy.float32` matrix in memory.
For a query, the same gateway model embeds the query. Both query and stored vectors
are L2-normalized, then matrix multiplication computes cosine similarity. Results
are sorted by descending similarity.

### Retrieval K and justification

`--k` defaults to **5**. Five candidate chunks usually provide a focused answer
while leaving room in the agent's conversation for the original task, profile
content, prior actions, and checker reports. A caller can adjust `k`; chunks that
do not fit the observation budget are omitted, so fewer than `k` may be returned.

### Deprecated document handling

All Markdown documents, including deprecated ones, are loaded and embedded so their
status and replacement metadata are retained. Ordinary search filters to documents
whose status is `ACTIVE` before ranking. Deprecated and unclear-status chunks are
therefore not returned as current guidance. Deprecated files record their named
replacement in metadata. Search does not currently provide a separate history mode
or automatically retrieve replacements in response to historical-rule questions.

### 1,000-token observation limit

The RAG search process tentatively adds ranked active chunks and measures the
formatted result with `count_tokens()`. A chunk is included only if the resulting
observation stays at or below **1,000 tokens**. `ActionHandlers.search_style_guide`
applies the same budget defensively, including when an alternate RAG implementation
is injected. Its observation includes source IDs and status labels.

## Custom JSON action protocol

Each model response must contain exactly one JSON object with these fields:

```json
{ "thought": "short reasoning", "action": "action_name", "args": {} }
```

The response may contain one ` ```html ` code block immediately after the JSON
object only when the action is `write_site`. The four actions are:

1. **`search_style_guide` — `{"query": "..."}`**
   Calls `rag.search(query, k=...)` and returns relevant active chunks with source
   IDs, within the 1,000-token observation cap.
2. **`read_profiles` — `{}`**
   Reads each `content/*.md` profile except `_template.md`, and returns the profile
   texts and filenames of supported images in the configured image directory.
3. **`write_site` — `{}` plus HTML code block, or `{"html": "..."}`**
   Extracts the HTML, resolves `{{image:filename}}` placeholders with the shared
   `embed_images()` helper, writes the result to `--out`, and calls
   `check_site(html, mode="full", ...)`. The complete checker report is returned.
   Missing/unsupported images, unresolved placeholders, malformed HTML input, and
   file/checker errors are returned as error observations.
4. **`finish` — `{"summary": "..."}`**
   Is accepted only if the latest checker report passed. Otherwise it is rejected
   and the failure list is returned to the model.

The agent does not implement native function calling. Handler argument validation
returns useful errors for missing, incorrectly typed, or unexpected arguments.

## Invalid output, step limit, and finish validation

Invalid JSON, missing or extra JSON fields, wrong field types, empty responses,
incomplete JSON/HTML fences, and unexpected trailing text produce an error
observation and consume a step. Unknown actions and bad action arguments are handled
by the action handlers and also produce observations. Unexpected handler exceptions
and chat errors are caught and returned to the conversation rather than terminating
the loop.

The default maximum is **25 steps**, configurable with `--max-steps`. Reaching it
prints a clear stop message and records a `step_limit` event in the log.

Every new `write_site` attempt clears prior validation state. `finish` is accepted
only when the most recent write's full checker report passed; a failed or absent
report rejects completion. After acceptance, the terminal prompts for feedback.
Non-empty feedback is appended as a user message and begins another interaction
cycle (which must write and validate the site again). Empty feedback ends the run.

## Logging

`create_agent()` creates `task2_own_agent/logs/` and reserves the first available
`run_01.jsonl`, `run_02.jsonl`, and subsequent filename. Reservation uses exclusive
file creation to avoid overwriting existing logs, including when runs start close
together. An existing explicit `--log` path is given a numeric suffix instead of
being overwritten.

`LLMClient(log_path=...)` writes the gateway's request/response, usage, and cost
records. The loop uses `llm.log_event()` for each run start, every message sent,
complete conversation request, model response, action execution/result, appended
observation, invalid response/error, human feedback, step-limit, and finish event.
Together, these records can reconstruct the custom protocol execution; the shared
client's `chat` records also capture gateway-level response metadata.

## Known limitations

- The RAG index is rebuilt and embeddings are requested each time the agent starts;
  there is no persistent embedding cache.
- Search is semantic cosine retrieval only. It does not use BM25, a reranker, or
  automatic cross-reference expansion.
- Chunks do not overlap, and a long rule split at a word boundary may lose context
  available only in a neighboring chunk.
- Deprecated documents are excluded from ordinary search. There is no dedicated
  archive/history search mode, despite replacement metadata being retained.
- The agent depends on gateway availability, configured credentials/models, and
  valid gateway embeddings. No local embedding fallback is implemented.
- Website quality is judged by the provided HTML checker; it does not perform
  browser rendering, visual regression, or comprehensive accessibility testing.
- The terminal feedback loop can continue until the configured maximum step count
  is reached.
