"""Resolving a model's quoted evidence to an exact source span (``FR-EXT-001``).

The model is never trusted to produce character offsets. It names a segment and
quotes the words it relied on; this module finds those words in the stored
segment text and returns offsets computed by code. A quote that cannot be found
does not resolve, and a proposal with no resolving quote is never persisted
(``FR-EXT-007`` as an invariant, not an instruction).

The stored quote is always the **source's** slice at the resolved offsets, never
the model's rendering of it. Matching is therefore allowed to be tolerant of
whitespace, typographic punctuation and letter case - differences a model
commonly introduces - without ever recording words the source did not contain.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

#: One-to-one character substitutions, so offsets survive canonicalisation.
_CANONICAL_CHARS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)

_WORD = re.compile(r"[A-Za-z0-9]+")


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _canonical_with_map(text: str, *, lower: bool) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space, keeping a map back to ``text``."""
    chars: list[str] = []
    index: list[int] = []
    previous_space = False
    translated = text.translate(_CANONICAL_CHARS)
    for position, char in enumerate(translated):
        if char.isspace():
            if previous_space:
                continue
            chars.append(" ")
            previous_space = True
        else:
            folded = char.lower() if lower else char
            # A few characters lower-case to two; keep the map one-to-one.
            chars.append(folded if len(folded) == 1 else char)
            previous_space = False
        index.append(position)
    return "".join(chars), index


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` of ``quote`` within ``text``, or ``None``.

    Tried in order, first match wins: verbatim; whitespace- and
    punctuation-canonical; then additionally case-insensitive. Offsets always
    index the original ``text``. The first occurrence is used, deterministically.
    """
    needle = quote.strip()
    if not needle:
        return None

    exact = text.find(needle)
    if exact >= 0:
        return exact, exact + len(needle)

    for lower in (False, True):
        haystack, index = _canonical_with_map(text, lower=lower)
        canonical_needle, _ = _canonical_with_map(needle, lower=lower)
        canonical_needle = canonical_needle.strip()
        if not canonical_needle:
            return None
        found = haystack.find(canonical_needle)
        if found >= 0:
            start = index[found]
            end = index[found + len(canonical_needle) - 1] + 1
            return start, end
    return None


@dataclass(frozen=True)
class ResolvedSpan:
    """A source span resolved by code: a segment, offsets, and the source's words.

    Offsets are within the **document** text, and ``quote`` is exactly
    ``document.text[char_start:char_end]``.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    char_start: int
    char_end: int
    quote: str
    speaker: str | None = None
    #: ``"source_chunk"`` (a document chunk) or ``"utterance"`` (P4: an interview
    #: answer; ``chunk_id`` is the utterance and ``document_id`` its session).
    source_kind: str = "source_chunk"

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError("a resolved span needs 0 <= char_start < char_end")
        if len(self.quote) != self.char_end - self.char_start:
            raise ValueError("a resolved span's quote must be exactly its source slice")

    def as_source_ref(self) -> dict[str, Any]:
        """The requirement-version ``source_refs`` entry for this span.

        ``kind``/``ref``/``span`` is the shape P1 established; ``document``,
        ``quote`` and ``speaker`` add what extraction knows. An utterance span
        names its ``session`` instead of a document, and its offsets are within
        the utterance (architecture F.3 ``EvidenceRef`` kind ``utterance``).
        """
        if self.source_kind == "utterance":
            return {
                "kind": "utterance",
                "ref": str(self.chunk_id),
                "span": [self.char_start, self.char_end],
                "session": str(self.document_id),
                "quote": self.quote,
                "speaker": self.speaker,
            }
        return {
            "kind": "source_chunk",
            "ref": str(self.chunk_id),
            "span": [self.char_start, self.char_end],
            "document": str(self.document_id),
            "quote": self.quote,
            "speaker": self.speaker,
        }

    @classmethod
    def from_source_ref(cls, ref: dict[str, Any]) -> ResolvedSpan:
        start, end = ref["span"]
        utterance = ref.get("kind") == "utterance"
        return cls(
            source_kind="utterance" if utterance else "source_chunk",
            chunk_id=uuid.UUID(str(ref["ref"])),
            document_id=uuid.UUID(str(ref["session"] if utterance else ref["document"])),
            char_start=int(start),
            char_end=int(end),
            quote=str(ref["quote"]),
            speaker=ref.get("speaker"),
        )

    @property
    def key(self) -> tuple[uuid.UUID, int, int]:
        return (self.document_id, self.char_start, self.char_end)


def resolve_in_segment(
    *,
    segment_text: str,
    segment_start: int,
    chunk_id: uuid.UUID,
    document_id: uuid.UUID,
    quote: str,
    speaker: str | None,
    source_kind: str = "source_chunk",
) -> ResolvedSpan | None:
    """Resolve ``quote`` within one stored segment, returning document offsets."""
    located = locate_quote(segment_text, quote)
    if located is None:
        return None
    start, end = located
    return ResolvedSpan(
        chunk_id=chunk_id,
        document_id=document_id,
        char_start=segment_start + start,
        char_end=segment_start + end,
        quote=segment_text[start:end],
        speaker=speaker,
        source_kind=source_kind,
    )


def original_wording(spans: list[ResolvedSpan]) -> str:
    """The stakeholder's own words for a requirement, from its resolved spans.

    Distinct spans in document order, joined with an ellipsis. Assembled by
    code from the source, so "original wording" can never be the model's
    paraphrase (``FR-EXT-003``).
    """
    seen: set[tuple[uuid.UUID, int, int]] = set()
    parts: list[str] = []
    for span in sorted(spans, key=lambda s: (str(s.document_id), s.char_start, s.char_end)):
        if span.key in seen:
            continue
        seen.add(span.key)
        parts.append(span.quote)
    return " \u2026 ".join(parts)
