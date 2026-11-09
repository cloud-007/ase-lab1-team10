import json, statistics as st
from collections import defaultdict

lengths = defaultdict(list)
for line in open("task3_rag/data/corpus.jsonl", encoding="utf-8"):
    d = json.loads(line)
    lengths[d["type"]].append(len(d["text"]))

print("type, count, avg chars, max chars")
for t, v in lengths.items():
    print(t, len(v), int(st.mean(v)), max(v))