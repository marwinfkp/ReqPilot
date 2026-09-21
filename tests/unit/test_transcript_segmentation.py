"""Transcript documents segmented by speaker turn, never split (architecture J.3)."""

from __future__ import annotations

import pytest
from tests.p3_helpers import workshop_text

from reqpilot.domain.enums import ChunkStrategy
from reqpilot.retrieval.chunking import segment_transcript_document

pytestmark = pytest.mark.unit


def test_each_speaker_turn_is_one_segment_with_exact_offsets() -> None:
    text = "Header line\n\nPriya: We need exports.\nAlso monthly.\nSam: Fast pages."
    turns = segment_transcript_document(text)
    assert [t.speaker for t in turns] == [None, "Priya", "Sam"]
    assert turns[1].chunk.text == "We need exports.\nAlso monthly."
    for turn in turns:
        assert text[turn.chunk.char_start : turn.chunk.char_end] == turn.chunk.text
        assert turn.chunk.strategy is ChunkStrategy.UTTERANCE


def test_labels_with_roles_timestamps_and_bold_are_recognised() -> None:
    text = "Priya (Operations): one\n[00:03:10] **Arjun**: two\n  Meera: three"
    assert [t.speaker for t in segment_transcript_document(text)] == [
        "Priya (Operations)",
        "Arjun",
        "Meera",
    ]


def test_colons_that_are_not_speaker_labels_stay_inside_the_turn() -> None:
    text = (
        "Sam: See https://example.test for details.\n"
        "Note that the rule is this: fast.\n"
        "This is a long sentence with many words before its colon: still Sam."
    )
    turns = segment_transcript_document(text)
    assert [t.speaker for t in turns] == ["Sam", "Note that the rule is this"]
    assert "still Sam" in turns[-1].chunk.text


def test_a_text_without_speakers_is_not_a_transcript() -> None:
    assert segment_transcript_document("Just a paragraph.\n\nAnother one.") == []


def test_the_synthetic_workshop_segments_into_its_turns() -> None:
    text = workshop_text()
    turns = segment_transcript_document(text)
    speakers = [t.speaker for t in turns]
    assert speakers[0] is None, "the title line is preamble"
    assert "Priya (Operations)" in speakers and "Meera (Compliance)" in speakers
    assert all(text[t.chunk.char_start : t.chunk.char_end] == t.chunk.text for t in turns)
