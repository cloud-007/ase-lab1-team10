"""Small in-memory cosine-similarity search over the Task 2 style guide.

The default search index embeds every document, including deprecated material, so
document metadata and history are retained. Normal searches filter to ACTIVE
documents before ranking; deprecated rules are never presented as current guidance."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.llm_client import LLMClient, count_tokens  # noqa: E402

STYLE_GUIDE_DIR = Path(__file__).resolve().parent / "style_guide"
DEFAULT_CHUNK_TOKENS = 350
DEFAULT_OBSERVATION_TOKENS = 1_000

_ID_RE = re.compile(r"\b(SG-\d{2})\b", re.IGNORECASE)
_TITLE_RE = re.compile(r"^#\s+(?:SG-\d{2}:\s*)?(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_STATUS_RE = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_CATEGORY_RE = re.compile(r"^Category:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class Chunk:
    """A searchable text chunk with the identity and status of its source document."""

    text: str
    document_id: str
    title: str
    status: str
    category: str | None = None
    version: str | None = None
    replacement_document: str | None = None
    source_path: str | None = None
    heading: str | None = None
    historical_context: bool = False
    score: float | None = None

    @property
    def id(self) -> str:
        """Short alias convenient for consumers that expect a chunk ``id``."""
        return self.document_id

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation, including provenance metadata."""
        return {
            "document_id": self.document_id,
            "title": self.title,
            "status": self.status,
            "category": self.category,
            "version": self.version,
            "replacement_document": self.replacement_document,
            "source_path": self.source_path,
            "heading": self.heading,
            "historical_context": self.historical_context,
            "score": self.score,
            "text": self.text,
        }


@dataclass(frozen=True)
class _Document:
    document_id: str
    title: str
    status: str
    category: str | None
    version: str | None
    replacement_document: str | None
    source_path: str
    historical_context: bool
    text: str


def _metadata(path: Path, text: str) -> _Document:
    id_match = _ID_RE.search(path.name) or _ID_RE.search(text[:500])
    if not id_match:
        raise ValueError(f"Cannot determine style-guide document ID from {path.name}")
    document_id = id_match.group(1).upper()

    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem
    status_match = _STATUS_RE.search(text)
    status_text = status_match.group(1).strip() if status_match else "unclear"
    status = (
        "deprecated"
        if re.search(r"\bdeprecated\b", status_text, re.I)
        else ("active" if re.search(r"\bactive\b", status_text, re.I) else "unclear")
    )
    category_match = _CATEGORY_RE.search(text)
    category = category_match.group(1).strip() if category_match else None
    version_match = re.search(r"\bversion\s+(\d+)\b", status_text, re.I)
    if not version_match:
        version_match = re.search(r"\bversion\s+(\d+)\b", title, re.I)
    version = version_match.group(1) if version_match else None

    replacement_document = None
    if status == "deprecated":
        replacement_match = re.search(
            r"\breplaced\s+by\s+(SG-\d{2})\b", status_text, re.I
        )
        if replacement_match:
            replacement_document = replacement_match.group(1).upper()

    return _Document(
        document_id=document_id,
        title=title,
        status=status,
        category=category,
        version=version,
        replacement_document=replacement_document,
        source_path=str(path),
        historical_context=(
            document_id == "SG-05"
            or bool(
                re.search(
                    r"\b(deprecated|replaced by|old key|old footer|old palette|earlier|previously|version [12])\b",
                    text,
                    re.I,
                )
            )
        ),
        text=text.strip(),
    )


def _split_long_block(block: str, max_tokens: int) -> list[str]:
    """Split an oversized paragraph/list/code line without dropping its text."""
    if count_tokens(block) <= max_tokens:
        return [block]
    words = block.split()
    parts: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join((*current, word))
        if current and count_tokens(candidate) > max_tokens:
            parts.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        parts.append(" ".join(current))
    return parts


def _chunk_document(document: _Document, max_tokens: int) -> list[Chunk]:
    """Group nearby Markdown blocks while keeping the applicable heading in context."""
    chunks: list[Chunk] = []
    current_blocks: list[str] = []
    current_heading: str | None = None

    def flush() -> None:
        nonlocal current_blocks
        if not current_blocks:
            return
        chunks.append(
            Chunk(
                text="\n\n".join(current_blocks),
                document_id=document.document_id,
                title=document.title,
                status=document.status,
                category=document.category,
                version=document.version,
                replacement_document=document.replacement_document,
                source_path=document.source_path,
                heading=current_heading,
                historical_context=document.historical_context,
            )
        )
        current_blocks = []

    blocks = [
        block.strip() for block in re.split(r"\n\s*\n", document.text) if block.strip()
    ]
    for block in blocks:
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", block)
        if heading_match:
            flush()
            current_heading = heading_match.group(2).strip()

        for part in _split_long_block(block, max_tokens):
            candidate = "\n\n".join((*current_blocks, part))
            if current_blocks and count_tokens(candidate) > max_tokens:
                flush()
            current_blocks.append(part)
            if count_tokens(part) > max_tokens:
                # Defensive guard for a pathological single token or tokenizer fallback.
                flush()
    flush()
    return chunks


class StyleGuideRAG:
    """Load, embed, and search style-guide chunks using cosine similarity."""

    def __init__(
        self,
        style_guide_dir: str | Path = STYLE_GUIDE_DIR,
        llm: LLMClient | None = None,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        observation_tokens: int = DEFAULT_OBSERVATION_TOKENS,
    ) -> None:
        if chunk_tokens < 1:
            raise ValueError("chunk_tokens must be positive")
        if observation_tokens < 1:
            raise ValueError("observation_tokens must be positive")

        self.style_guide_dir = Path(style_guide_dir)
        if not self.style_guide_dir.is_dir():
            raise FileNotFoundError(
                f"Style-guide directory not found: {self.style_guide_dir}"
            )
        self.llm = llm or LLMClient()
        self.observation_tokens = observation_tokens

        documents = [
            _metadata(path, path.read_text(encoding="utf-8"))
            for path in sorted(self.style_guide_dir.glob("*.md"))
        ]
        if not documents:
            raise ValueError(f"No Markdown documents found in {self.style_guide_dir}")

        self.documents = documents
        self.chunks = [
            chunk
            for document in documents
            for chunk in _chunk_document(document, chunk_tokens)
        ]

        # Include title and section in embedding input to improve short-rule retrieval,
        # but keep returned chunk.text as source Markdown rather than synthetic text.
        embedding_inputs = [
            "\n".join(part for part in (chunk.title, chunk.heading, chunk.text) if part)
            for chunk in self.chunks
        ]
        matrix = np.asarray(self.llm.embed(embedding_inputs), dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(self.chunks):
            raise ValueError(
                "Embedding client returned an unexpected shape: "
                f"{matrix.shape}; expected ({len(self.chunks)}, embedding dimension)"
            )
        self.embeddings = matrix
        self._normalized_embeddings = self._normalize_rows(matrix)

    @staticmethod
    def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms != 0)

    @staticmethod
    def _observation_piece(chunk: Chunk) -> str:
        heading = f" — {chunk.heading}" if chunk.heading else ""
        replacement = (
            f"; replaced by {chunk.replacement_document}"
            if chunk.replacement_document
            else ""
        )
        score = f"; cosine={chunk.score:.3f}" if chunk.score is not None else ""
        history = (
            "; historical/version context, not current guidance"
            if chunk.historical_context
            else ""
        )
        return (
            f"[{chunk.document_id} | {chunk.status.upper()}{history} | {chunk.title}{heading}{replacement}{score}]\n"
            f"{chunk.text}"
        )

    def format_observation(self, chunks: list[Chunk]) -> str:
        """Format results with provenance while enforcing the configured token cap."""
        parts: list[str] = []
        for chunk in chunks:
            if chunk.status != "active":
                continue
            piece = self._observation_piece(chunk)
            candidate = "\n\n---\n\n".join((*parts, piece))
            if count_tokens(candidate) <= self.observation_tokens:
                parts.append(piece)
        return "\n\n---\n\n".join(parts)

    def search(self, query: str, k: int = 5) -> list[Chunk]:
        """Return up to ``k`` most relevant ACTIVE chunks within the token budget.

        Deprecated/unclear documents are loaded, chunked, and embedded with their
        metadata, but are intentionally excluded from ordinary current-guidance search.
        The caller can pass the result to :meth:`format_observation`; both the formatted
        observation and its style-guide text stay within ``observation_tokens``.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if k < 1:
            return []

        active_indices = [
            i for i, chunk in enumerate(self.chunks) if chunk.status == "active"
        ]
        if not active_indices:
            return []

        query_vector = np.asarray(self.llm.embed([query]), dtype=np.float32)
        if query_vector.ndim != 2 or query_vector.shape[0] != 1:
            raise ValueError(
                f"Embedding client returned an unexpected query shape: {query_vector.shape}"
            )
        if query_vector.shape[1] != self.embeddings.shape[1]:
            raise ValueError(
                "Query embedding dimension does not match style-guide embeddings"
            )
        normalized_query = self._normalize_rows(query_vector)[0]
        scores = self._normalized_embeddings[active_indices] @ normalized_query
        ranked = sorted(
            zip(active_indices, scores.tolist()), key=lambda pair: pair[1], reverse=True
        )

        results: list[Chunk] = []
        for index, score in ranked:
            if len(results) >= k:
                break
            candidate = self.chunks[index]
            candidate_result = Chunk(
                text=candidate.text,
                document_id=candidate.document_id,
                title=candidate.title,
                status=candidate.status,
                category=candidate.category,
                version=candidate.version,
                replacement_document=candidate.replacement_document,
                source_path=candidate.source_path,
                heading=candidate.heading,
                historical_context=candidate.historical_context,
                score=float(score),
            )
            trial = [*results, candidate_result]
            if count_tokens(self.format_observation(trial)) <= self.observation_tokens:
                results.append(candidate_result)
        return results


_DEFAULT_RAG: StyleGuideRAG | None = None


def search(query: str, k: int = 5) -> list[Chunk]:
    """Module-level convenience API required by the Task 2 agent protocol."""
    global _DEFAULT_RAG
    if _DEFAULT_RAG is None:
        _DEFAULT_RAG = StyleGuideRAG()
    return _DEFAULT_RAG.search(query, k)


def format_observation(chunks: list[Chunk]) -> str:
    """Format module-level search results within the standard 1,000-token budget."""
    if _DEFAULT_RAG is not None:
        return _DEFAULT_RAG.format_observation(chunks)
    parts: list[str] = []
    for chunk in chunks:
        if chunk.status != "active":
            continue
        piece = StyleGuideRAG._observation_piece(chunk)
        if (
            count_tokens("\n\n---\n\n".join((*parts, piece)))
            <= DEFAULT_OBSERVATION_TOKENS
        ):
            parts.append(piece)
    return "\n\n---\n\n".join(parts)
