"""Structure-aware chunking with exact character offsets (architecture J.3).

One invariant holds for every chunk this module produces::

    chunk.text == source_text[chunk.char_start:chunk.char_end]

It is what makes span-level traceability and exact citation resolution possible
at all (``FR-EXT-001``, ``FR-RAG-003``), so it is checked in
:class:`TextChunk` itself rather than trusted to each strategy.

The three strategies are the architecture's, not a generic fixed-size splitter:

* **Knowledge items** - one chunk per clause or control where the source has that
  structure; otherwise ~500 tokens with 80-token overlap.
* **Project documents** - ~700 tokens with 100-token overlap, split on headings
  and paragraphs first.
* **Transcripts** - one chunk per utterance; a speaker turn is never split.

Everything here is pure and deterministic: the same text and parameters always
produce the same chunks, which is what lets chunk identities be derived rather
than drawn at random.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.domain.enums import ChunkStrategy

#: A "token" for sizing purposes: a run of word characters, or one punctuation
#: mark. Deterministic and independent of any installed model, so chunk
#: boundaries do not move when the embedding model's tokenizer changes. It
#: slightly undercounts a WordPiece tokenizer, which the 500-token size leaves
#: room for below bge-small's 512-token input limit.
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]")

#: A clause or heading marker at the start of a line. Deliberately narrow: a
#: bare number is a clause only when dotted ("1.", "3.1"), so a line that merely
#: starts with a quantity ("5 days...") does not split a clause.
CLAUSE_PATTERN = re.compile(
    r"^[ \t]*(?P<label>"
    r"#{1,6}[ \t]+\S[^\n]*"
    r"|(?:Section|Article|Clause|Rule|Regulation|Chapter|Paragraph|Part|Schedule|Annex)"
    r"[ \t]+[0-9IVXL]{1,6}[A-Z]?\b"
    r"|[A-Z]\.[0-9]{1,3}(?:\.[0-9]{1,3})*\b"
    r"|[0-9]{1,3}\.(?:[0-9]{1,3}\.?)*(?=[ \t])"
    r")",
    re.MULTILINE,
)

_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def token_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every token in ``text``."""
    return [(m.start(), m.end()) for m in TOKEN_PATTERN.finditer(text)]


def count_tokens(text: str) -> int:
    return sum(1 for _ in TOKEN_PATTERN.finditer(text))


@dataclass(frozen=True)
class WindowSpec:
    """A token window: maximum size and the overlap between consecutive windows."""

    max_tokens: int
    overlap_tokens: int

    def __post_init__(self) -> None:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if not 0 <= self.overlap_tokens < self.max_tokens:
            raise ValueError("overlap_tokens must be at least 0 and less than max_tokens")


@dataclass(frozen=True)
class TextChunk:
    """A span of a source text. Its ``text`` is exactly the source slice it names."""

    ordinal: int
    char_start: int
    char_end: int
    text: str
    strategy: ChunkStrategy
    structure_label: str | None
    token_count: int

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError(f"invalid span [{self.char_start}, {self.char_end})")
        if len(self.text) != self.char_end - self.char_start:
            raise ValueError("chunk text length does not match its span")

    def matches(self, source_text: str) -> bool:
        """Whether this chunk is still exactly the slice of ``source_text`` it names."""
        return source_text[self.char_start : self.char_end] == self.text


@dataclass(frozen=True)
class UtteranceChunk:
    """A transcript chunk: exactly one utterance, with offsets within it."""

    utterance_id: uuid.UUID
    chunk: TextChunk


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _trimmed(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Shrink ``[start, end)`` to exclude surrounding whitespace, or ``None`` if empty."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def _windows(text: str, start: int, end: int, spec: WindowSpec) -> list[tuple[int, int, int]]:
    """Token windows over ``text[start:end]`` as ``(char_start, char_end, tokens)``.

    Windows start and end on token boundaries, so no word is ever cut in half,
    and consecutive windows share exactly ``overlap_tokens`` tokens.
    """
    spans = [(start + a, start + b) for a, b in token_spans(text[start:end])]
    if not spans:
        return []
    step = spec.max_tokens - spec.overlap_tokens
    out: list[tuple[int, int, int]] = []
    first = 0
    while True:
        last = min(first + spec.max_tokens, len(spans))
        out.append((spans[first][0], spans[last - 1][1], last - first))
        if last == len(spans):
            return out
        first += step


def _clause_label(raw: str) -> str:
    label = raw.strip()
    if label.startswith("#"):
        label = label.lstrip("#").strip()
    return label.rstrip(".")[:200]


def _clause_segments(text: str) -> list[tuple[int, int, str | None]]:
    """Split at clause markers: ``(start, end, label)``, including any preamble."""
    starts = [
        (m.start("label"), _clause_label(m.group("label"))) for m in CLAUSE_PATTERN.finditer(text)
    ]
    segments: list[tuple[int, int, str | None]] = []
    if starts and starts[0][0] > 0:
        segments.append((0, starts[0][0], None))
    for index, (start, label) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        segments.append((start, end, label))
    return segments


class _Builder:
    """Accumulates chunks with consecutive ordinals."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.chunks: list[TextChunk] = []

    def add(
        self, start: int, end: int, strategy: ChunkStrategy, label: str | None, tokens: int
    ) -> None:
        self.chunks.append(
            TextChunk(
                ordinal=len(self.chunks),
                char_start=start,
                char_end=end,
                text=self.text[start:end],
                strategy=strategy,
                structure_label=label,
                token_count=tokens,
            )
        )

    def add_span(
        self, start: int, end: int, spec: WindowSpec, strategy: ChunkStrategy, label: str | None
    ) -> None:
        """Add one chunk for the span, or windows if it is longer than ``spec`` allows."""
        span = _trimmed(self.text, start, end)
        if span is None:
            return
        tokens = count_tokens(self.text[span[0] : span[1]])
        if tokens == 0:
            return
        if tokens <= spec.max_tokens:
            self.add(span[0], span[1], strategy, label, tokens)
            return
        for w_start, w_end, w_tokens in _windows(self.text, span[0], span[1], spec):
            self.add(w_start, w_end, ChunkStrategy.WINDOW, label, w_tokens)


# ---------------------------------------------------------------------------
# The three J.3 strategies
# ---------------------------------------------------------------------------


def chunk_knowledge_item(
    text: str, spec: WindowSpec, *, min_clause_boundaries: int = 2
) -> list[TextChunk]:
    """Chunk a knowledge item: one chunk per clause, else token windows (J.3).

    A clause longer than ``spec.max_tokens`` is windowed within itself and keeps
    its clause label, so no chunk outgrows the embedding model's input.
    """
    builder = _Builder(text)
    if not text.strip():
        return builder.chunks

    boundaries = sum(1 for _ in CLAUSE_PATTERN.finditer(text))
    if boundaries >= min_clause_boundaries:
        for start, end, label in _clause_segments(text):
            builder.add_span(start, end, spec, ChunkStrategy.CLAUSE, label or "preamble")
        return builder.chunks

    span = _trimmed(text, 0, len(text))
    if span is not None:
        for w_start, w_end, w_tokens in _windows(text, span[0], span[1], spec):
            builder.add(w_start, w_end, ChunkStrategy.WINDOW, None, w_tokens)
    return builder.chunks


def _paragraph_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Paragraphs of ``text[start:end]``: blocks separated by blank lines, trimmed."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for separator in _BLANK_LINE.finditer(text, start, end):
        trimmed = _trimmed(text, cursor, separator.start())
        if trimmed is not None:
            spans.append(trimmed)
        cursor = separator.end()
    trimmed = _trimmed(text, cursor, end)
    if trimmed is not None:
        spans.append(trimmed)
    return spans


def chunk_project_document(text: str, spec: WindowSpec) -> list[TextChunk]:
    """Chunk a project document: headings, then paragraphs, then windows (J.3).

    Paragraphs are packed into chunks up to ``spec.max_tokens`` without crossing a
    heading. A paragraph longer than that is windowed with ``spec.overlap_tokens``.
    """
    builder = _Builder(text)
    if not text.strip():
        return builder.chunks

    for sec_start, sec_end, label in _clause_segments(text) or [(0, len(text), None)]:
        pending: tuple[int, int, int] | None = None  # (start, end, tokens)
        for p_start, p_end in _paragraph_spans(text, sec_start, sec_end):
            p_tokens = count_tokens(text[p_start:p_end])
            if p_tokens > spec.max_tokens:
                if pending is not None:
                    builder.add(pending[0], pending[1], ChunkStrategy.SECTION, label, pending[2])
                    pending = None
                builder.add_span(p_start, p_end, spec, ChunkStrategy.SECTION, label)
                continue
            if pending is None:
                pending = (p_start, p_end, p_tokens)
            elif pending[2] + p_tokens <= spec.max_tokens:
                pending = (pending[0], p_end, count_tokens(text[pending[0] : p_end]))
            else:
                builder.add(pending[0], pending[1], ChunkStrategy.SECTION, label, pending[2])
                pending = (p_start, p_end, p_tokens)
        if pending is not None:
            builder.add(pending[0], pending[1], ChunkStrategy.SECTION, label, pending[2])
    return builder.chunks


def chunk_transcript(utterances: Sequence[tuple[uuid.UUID, str]]) -> list[UtteranceChunk]:
    """One chunk per utterance, never splitting a speaker turn (J.3).

    Offsets are relative to each utterance's own text, because the utterance -
    not a concatenated transcript - is the traceability root (architecture G.3).
    An utterance longer than the embedding input is still one chunk; its vector
    covers the leading part, and keyword search still covers all of it.
    """
    out: list[UtteranceChunk] = []
    for utterance_id, text in utterances:
        span = _trimmed(text, 0, len(text))
        if span is None:
            continue
        out.append(
            UtteranceChunk(
                utterance_id=utterance_id,
                chunk=TextChunk(
                    ordinal=len(out),
                    char_start=span[0],
                    char_end=span[1],
                    text=text[span[0] : span[1]],
                    strategy=ChunkStrategy.UTTERANCE,
                    structure_label=None,
                    token_count=count_tokens(text[span[0] : span[1]]),
                ),
            )
        )
    return out
