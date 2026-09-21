"""Quote resolution: offsets computed by code, never taken from a model (FR-EXT-001/007)."""

from __future__ import annotations

import random
import uuid

import pytest

from reqpilot.domain.source_spans import (
    ResolvedSpan,
    locate_quote,
    original_wording,
    resolve_in_segment,
    word_count,
)

pytestmark = pytest.mark.unit

TEXT = "Applicants must be able to upload their income documents when they apply online."

LEFT_QUOTE, RIGHT_QUOTE, EN_DASH, ELLIPSIS = "\u2018", "\u2019", "\u2013", "\u2026"


def test_an_exact_quote_resolves_to_its_offsets() -> None:
    start, end = locate_quote(TEXT, "upload their income documents")
    assert TEXT[start:end] == "upload their income documents"


def test_whitespace_differences_are_tolerated_and_offsets_index_the_source() -> None:
    text = "Applicants must be able\n  to upload   their documents."
    start, end = locate_quote(text, "able to upload their documents")
    assert text[start:end] == "able\n  to upload   their documents"


def test_typographic_punctuation_and_case_are_tolerated() -> None:
    text = f"The {LEFT_QUOTE}portal{RIGHT_QUOTE} has to respond {EN_DASH} fast."
    start, end = locate_quote(text, "the 'portal' HAS to respond - fast")
    assert text[start:end] == f"The {LEFT_QUOTE}portal{RIGHT_QUOTE} has to respond {EN_DASH} fast"


@pytest.mark.parametrize(
    "quote",
    ["upload their tax documents", "Applicants should upload", "", "   "],
)
def test_words_the_source_does_not_contain_do_not_resolve(quote: str) -> None:
    assert locate_quote(TEXT, quote) is None


def test_a_resolved_span_records_the_source_words_not_the_quote() -> None:
    chunk, document = uuid.uuid4(), uuid.uuid4()
    span = resolve_in_segment(
        segment_text=TEXT,
        segment_start=100,
        chunk_id=chunk,
        document_id=document,
        quote="UPLOAD THEIR INCOME DOCUMENTS",
        speaker="Priya (Operations)",
    )
    assert span is not None
    assert span.quote == "upload their income documents"
    assert (span.char_start, span.char_end) == (
        100 + TEXT.index("upload"),
        100 + TEXT.index(" when"),
    )
    assert span.as_source_ref()["kind"] == "source_chunk"
    assert ResolvedSpan.from_source_ref(span.as_source_ref()) == span


def test_a_span_whose_quote_is_not_its_slice_cannot_exist() -> None:
    with pytest.raises(ValueError):
        ResolvedSpan(uuid.uuid4(), uuid.uuid4(), 0, 10, "short")
    with pytest.raises(ValueError):
        ResolvedSpan(uuid.uuid4(), uuid.uuid4(), 5, 5, "")


def test_original_wording_is_the_sources_words_in_order_without_repeats() -> None:
    doc = uuid.uuid4()
    later = ResolvedSpan(uuid.uuid4(), doc, 50, 55, "later")
    earlier = ResolvedSpan(uuid.uuid4(), doc, 10, 15, "first")
    assert original_wording([later, earlier, later]) == f"first {ELLIPSIS} later"


def test_word_count() -> None:
    assert word_count("the income documents") == 3
    assert word_count("  -- ") == 0


def test_any_verbatim_slice_resolves_to_an_equal_slice() -> None:
    """A seeded property check over 300 generated texts and slices."""
    rng = random.Random(20260921)
    alphabet = "abcdefg hij\n"
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(10, 200)))
        start = rng.randint(0, len(text) - 1)
        quote = text[start : start + rng.randint(3, 40)].strip()
        if not quote:
            continue
        located = locate_quote(text, quote)
        assert located is not None
        begin, end = located
        assert text[begin:end] == quote
