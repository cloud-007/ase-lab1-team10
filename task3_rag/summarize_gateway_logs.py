"""Summarize measured gateway usage without modifying or including private logs.

Usage:
    python task3_rag/summarize_gateway_logs.py logs/file1.jsonl [logs/file2.jsonl ...]
"""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    raise SystemExit("Provide at least one gateway JSONL log path.")

for filename in sys.argv[1:]:
    path = Path(filename)
    chat_calls = 0
    embedding_calls = 0
    embedded_texts = 0
    prompt_tokens = 0
    completion_tokens = 0
    chat_cost = 0.0
    embedding_cost = 0.0

    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            kind = record.get("type")

            if kind == "chat":
                chat_calls += 1
                usage = record.get("usage") or {}
                prompt_tokens += usage.get("prompt_tokens", 0)
                completion_tokens += usage.get("completion_tokens", 0)
                chat_cost += record.get("cost_usd", 0)
            elif kind == "embedding":
                embedding_calls += 1
                embedded_texts += record.get("n_texts", 0)
                embedding_cost += record.get("cost_usd", 0)

    print(f"\n{path}")
    print(f"Chat calls: {chat_calls}")
    print(f"Prompt tokens: {prompt_tokens}")
    print(f"Completion tokens: {completion_tokens}")
    print(f"Total chat tokens: {prompt_tokens + completion_tokens}")
    print(f"Chat cost (USD): ${chat_cost:.7f}")
    print(f"Embedding calls: {embedding_calls}")
    print(f"Embedded texts: {embedded_texts}")
    print(f"Embedding cost (USD): ${embedding_cost:.7f}")
    print(f"Combined reported cost (USD): ${chat_cost + embedding_cost:.7f}")
    print("Embedding token usage: unavailable in gateway logs")
