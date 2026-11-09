import json
import re
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "data" / "corpus.jsonl"


def load_corpus():
    with open(CORPUS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def make_chunk(doc, i, piece):
    header = f"{doc['title']} [{doc['type']}, version {doc['version']}]\n"
    return {
        "chunk_id": f"{doc['doc_id']}#{i}",
        "doc_id": doc["doc_id"],
        "type": doc["type"],
        "version": doc["version"],
        "text": header + piece.strip(),
    }


def fixed_chunks(docs, size=600, overlap=50):
    """R2: cut every document into pieces of `size` characters."""
    chunks = []
    for doc in docs:
        text = doc["text"]
        start, i = 0, 0
        while start < len(text):
            piece = text[start:start + size]
            if piece.strip():
                chunks.append(make_chunk(doc, i, piece))
                i += 1
            if start + size >= len(text):
                break
            start += size - overlap
    return chunks


# Split points, from coarse to fine: headings, paragraphs, lines, sentences, words.
SEPARATORS = [r"(?=\n#{1,3} )", r"(?<=\n\n)", r"(?<=\n)", r"(?<=[.!?] )", r"(?<= )"]


def split_recursive(text, size, seps):
    if len(text) <= size:
        return [text]
    if not seps:
        return [text[i:i + size] for i in range(0, len(text), size)]
    parts = [p for p in re.split(seps[0], text) if p]
    if len(parts) == 1:
        return split_recursive(text, size, seps[1:])
    out = []
    for part in parts:
        out.extend(split_recursive(part, size, seps[1:]) if len(part) > size else [part])
    return out


def merge_pieces(pieces, size, overlap):
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) > size:
            chunks.append(current)
            current = (current[-overlap:] if overlap else "") + piece
        else:
            current += piece
    if current.strip():
        chunks.append(current)
    return chunks


def recursive_chunks(docs, size=600, overlap=50):
    """R3: split on headings, then paragraphs, lines, sentences, words."""
    chunks = []
    for doc in docs:
        pieces = split_recursive(doc["text"], size, SEPARATORS)
        for i, piece in enumerate(merge_pieces(pieces, size, overlap)):
            chunks.append(make_chunk(doc, i, piece))
    return chunks


if __name__ == "__main__":
    docs = load_corpus()
    print("documents:", len(docs))
    print("fixed 600/50:", len(fixed_chunks(docs, 600, 50)))
    for size in (300, 600, 1000):
        print(f"recursive {size}/50:", len(recursive_chunks(docs, size, 50)))
    sample = [c for c in recursive_chunks(docs, 600, 50) if c["type"] == "changelog"][:2]
    for c in sample:
        print("----", c["chunk_id"], len(c["text"]))
        print(c["text"][:300])