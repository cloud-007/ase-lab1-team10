
"""Shared R4 context selection and answer generation for Task 3."""

import numpy as np

from bm25 import tokenize
from dense import normalize
from hybrid import minmax

BM25_WEIGHT = 0.25
ABSTENTION = "The documentation does not contain this information."
MAX_ANSWER_TOKENS = 400


def select_contexts(question, chunks, bm25, matrix, llm):
    """Select the highest R4-ranked chunk from five unique documents."""
    bm = minmax(bm25.get_scores(tokenize(question)))

    query_vec = normalize(
        np.asarray(llm.embed([question]), dtype=np.float32)
    )[0]

    dense = minmax(matrix @ query_vec)
    scores = BM25_WEIGHT * bm + (1 - BM25_WEIGHT) * dense

    order = np.argsort(-scores, kind="stable")
    selected = []
    seen = set()

    for idx in order:
        chunk = chunks[int(idx)]

        if chunk["doc_id"] not in seen:
            selected.append(chunk)
            seen.add(chunk["doc_id"])

        if len(selected) == 5:
            break

    if len(selected) != 5:
        raise RuntimeError(
            f"Expected five unique documents, found {len(selected)}"
        )

    return selected


def generate_answer(question, selected, llm):
    """Generate an answer using the original test prompt and token limit."""
    context_text = "\n\n".join(
        f'[Document {i + 1}: {chunk["doc_id"]}]\n{chunk["text"]}'
        for i, chunk in enumerate(selected)
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Answer the user question using only the supplied "
                "PaderQueue documentation. Do not invent facts. "
                "If the documentation does not contain the answer, "
                "reply exactly: " + ABSTENTION
            ),
        },
        {
            "role": "user",
            "content": (
                "Documentation:\n"
                + context_text
                + "\n\nQuestion: "
                + question
            ),
        },
    ]

    return llm.chat(
        messages, max_tokens=MAX_ANSWER_TOKENS
    ).strip() or ABSTENTION
