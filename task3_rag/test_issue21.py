
"""Check that shared R4 selection matches the original test logic."""

import numpy as np
from rank_bm25 import BM25Okapi

from bm25 import tokenize
from dense import normalize
from hybrid import minmax
from rag_pipeline import select_contexts, generate_answer

chunks = [
    {"doc_id": "A", "text": "queue timeout configuration"},
    {"doc_id": "A", "text": "worker retry policy"},
    {"doc_id": "B", "text": "database connection"},
    {"doc_id": "C", "text": "message delivery"},
    {"doc_id": "D", "text": "error handling"},
    {"doc_id": "E", "text": "service deployment"},
]

question = "How does the queue retry policy work?"

matrix = normalize(np.array([
    [1.0, 0.0],
    [0.9, 0.1],
    [0.2, 0.8],
    [0.3, 0.7],
    [0.4, 0.6],
    [0.5, 0.5],
], dtype=np.float32))

bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])


class FakeLLM:
    def embed(self, texts):
        return [[0.8, 0.2]]

    def chat(self, messages, max_tokens):
        self.messages = messages
        self.max_tokens = max_tokens
        return "Test answer"


llm = FakeLLM()

# Original R4 chunk selection.
bm = minmax(bm25.get_scores(tokenize(question)))
vec = normalize(
    np.asarray(llm.embed([question]), dtype=np.float32)
)[0]
dense = minmax(matrix @ vec)
scores = 0.25 * bm + 0.75 * dense

original = []
seen = set()

for idx in np.argsort(-scores, kind="stable"):
    chunk = chunks[int(idx)]

    if chunk["doc_id"] not in seen:
        original.append(chunk)
        seen.add(chunk["doc_id"])

    if len(original) == 5:
        break

# New shared chunk selection.
shared = select_contexts(question, chunks, bm25, matrix, llm)

assert shared == original
print("PASS: Chunk selection matches original")

# Check generation prompt and token limit.
answer = generate_answer(question, shared, llm)

expected_context = "\n\n".join(
    f'[Document {i+1}: {c["doc_id"]}]\n{c["text"]}'
    for i, c in enumerate(original)
)

expected_system = (
    "Answer the user question using only the supplied "
    "PaderQueue documentation. Do not invent facts. "
    "If the documentation does not contain the answer, "
    "reply exactly: The documentation does not contain this information."
)

assert answer == "Test answer"
assert llm.max_tokens == 400
assert llm.messages == [
    {"role": "system", "content": expected_system},
    {
        "role": "user",
        "content": (
            "Documentation:\n"
            + expected_context
            + "\n\nQuestion: "
            + question
        ),
    },
]

print("PASS: Generation prompt matches original")
print("PASS: Maximum answer tokens = 400")
print("PASS: No AI Gateway calls made")
