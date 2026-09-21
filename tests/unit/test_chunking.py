"""Structure-aware chunking with exact offsets (architecture J.3).

The invariant under test everywhere: ``chunk.text == source[char_start:char_end]``.
Citation resolution to an exact span depends on it (``FR-RAG-003``).
"""

from __future__ import annotations

import itertools
import random
import uuid

import pytest

from reqpilot.domain.enums import ChunkStrategy
from reqpilot.retrieval.chunking import (
    TextChunk,
    WindowSpec,
    chunk_knowledge_item,
    chunk_project_document,
    chunk_transcript,
    count_tokens,
)

pytestmark = pytest.mark.unit

KB = WindowSpec(max_tokens=500, overlap_tokens=80)
DOC = WindowSpec(max_tokens=700, overlap_tokens=100)

CLAUSED = """Acme Bank Access Policy (fictional)

1. Purpose
This policy governs access to customer data.

2. Least privilege
2.1 Access is granted on a least-privilege basis.
2.2 Access rights are reviewed quarterly.

A.5.15 Access control paraphrase written by the team.
"""


def words(n: int, seed: int = 0) -> str:
    rng = random.Random(seed)
    vocab = ["loan", "data", "access", "review", "customer", "record", "fee", "policy", "bank"]
    return " ".join(rng.choice(vocab) for _ in range(n))


def assert_exact(chunks: list[TextChunk], source: str) -> None:
    for chunk in chunks:
        assert chunk.text == source[chunk.char_start : chunk.char_end]
        assert chunk.matches(source)
        assert chunk.text == chunk.text.strip(), "chunks never begin or end in whitespace"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


# --- knowledge items ---------------------------------------------------------


def test_one_chunk_per_clause_where_the_source_has_clauses() -> None:
    chunks = chunk_knowledge_item(CLAUSED, KB)
    assert_exact(chunks, CLAUSED)
    assert [c.structure_label for c in chunks] == ["preamble", "1", "2", "2.1", "2.2", "A.5.15"]
    assert all(c.strategy is ChunkStrategy.CLAUSE for c in chunks)


def test_clause_chunks_cover_every_word_of_the_item() -> None:
    chunks = chunk_knowledge_item(CLAUSED, KB)
    covered = "".join(c.text for c in chunks)
    assert "".join(CLAUSED.split()) == "".join(covered.split())


def test_statute_style_and_heading_markers_are_clauses() -> None:
    text = "Section 4\nObligations apply.\n\nSection 5\nRights apply.\n\n## Definitions\nTerms."
    labels = [c.structure_label for c in chunk_knowledge_item(text, KB)]
    assert labels == ["Section 4", "Section 5", "Definitions"]


def test_a_line_starting_with_a_quantity_is_not_a_clause() -> None:
    """Only dotted numbers are clause markers, so "5 days" never splits a clause."""
    text = "Complaints are handled promptly.\n5 days is the target.\n2024 was the pilot year."
    chunks = chunk_knowledge_item(text, KB)
    assert len(chunks) == 1
    assert chunks[0].strategy is ChunkStrategy.WINDOW


def test_unstructured_text_uses_500_token_windows_with_80_overlap() -> None:
    text = words(1200)
    chunks = chunk_knowledge_item(text, KB)
    assert_exact(chunks, text)
    assert all(c.strategy is ChunkStrategy.WINDOW for c in chunks)
    assert [c.token_count for c in chunks] == [500, 500, 360]
    # Consecutive windows share exactly the overlap.
    for left, right in itertools.pairwise(chunks):
        shared = text[right.char_start : left.char_end]
        assert count_tokens(shared) == 80


def test_a_clause_too_long_to_embed_is_windowed_within_itself() -> None:
    text = "1. Short clause.\n2. " + words(1100, seed=3) + "\n3. Another short clause."
    chunks = chunk_knowledge_item(text, KB)
    assert_exact(chunks, text)
    assert all(c.token_count <= 500 for c in chunks)
    long_parts = [c for c in chunks if c.structure_label == "2"]
    assert len(long_parts) > 1
    assert all(c.strategy is ChunkStrategy.WINDOW for c in long_parts)


def test_chunking_is_deterministic() -> None:
    assert chunk_knowledge_item(CLAUSED, KB) == chunk_knowledge_item(CLAUSED, KB)
    text = words(2000, seed=9)
    assert chunk_knowledge_item(text, KB) == chunk_knowledge_item(text, KB)


def test_empty_text_produces_no_chunks() -> None:
    assert chunk_knowledge_item("", KB) == []
    assert chunk_knowledge_item("  \n\t ", KB) == []


@pytest.mark.parametrize("seed", range(25))
def test_offsets_hold_on_arbitrary_text(seed: int) -> None:
    """Property check over generated text with clauses, blank lines and punctuation."""
    rng = random.Random(seed)
    parts = []
    for _ in range(rng.randint(1, 12)):
        marker = rng.choice(["", "1. ", "2.3 ", "Section 7\n", "## Heading\n", "A.8.2 "])
        parts.append(
            marker
            + words(rng.randint(1, 400), seed=rng.randint(0, 10_000))
            + rng.choice([".", ";", "!"])
        )
    text = rng.choice(["", "  ", "\n"]) + rng.choice(["\n\n", "\n", " \n \n"]).join(parts)
    spec = WindowSpec(rng.randint(20, 500), 0)
    spec = WindowSpec(spec.max_tokens, rng.randint(0, spec.max_tokens - 1))
    for chunks in (chunk_knowledge_item(text, spec), chunk_project_document(text, spec)):
        assert_exact(chunks, text)
        assert all(c.token_count <= spec.max_tokens for c in chunks)


# --- project documents -------------------------------------------------------


def test_project_documents_split_on_headings_before_size() -> None:
    text = "# Scope\nThe system covers onboarding.\n\n# Risks\nFraud is a risk."
    chunks = chunk_project_document(text, DOC)
    assert_exact(chunks, text)
    assert [c.structure_label for c in chunks] == ["Scope", "Risks"]
    assert all(c.strategy is ChunkStrategy.SECTION for c in chunks)


def test_project_document_paragraphs_pack_up_to_700_tokens() -> None:
    paragraphs = [words(300, seed=i) for i in range(4)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_project_document(text, DOC)
    assert_exact(chunks, text)
    assert [c.token_count for c in chunks] == [600, 600]


def test_a_long_project_paragraph_is_windowed_with_100_overlap() -> None:
    text = words(1500, seed=4)
    chunks = chunk_project_document(text, DOC)
    assert_exact(chunks, text)
    assert chunks[0].token_count == 700
    shared = text[chunks[1].char_start : chunks[0].char_end]
    assert count_tokens(shared) == 100


# --- transcripts -------------------------------------------------------------


def test_one_chunk_per_utterance_and_no_turn_is_split() -> None:
    long_turn = words(3000, seed=1)
    utterances = [(uuid.uuid4(), "  I need faster approvals. "), (uuid.uuid4(), long_turn)]
    chunks = chunk_transcript(utterances)
    assert len(chunks) == 2
    for (utterance_id, text), chunk in zip(utterances, chunks, strict=True):
        assert chunk.utterance_id == utterance_id
        assert chunk.chunk.matches(text)
        assert chunk.chunk.strategy is ChunkStrategy.UTTERANCE
    assert chunks[1].chunk.token_count == 3000


def test_blank_utterances_are_skipped() -> None:
    assert chunk_transcript([(uuid.uuid4(), "   ")]) == []


# --- the value objects themselves -----------------------------------------------


def test_a_chunk_whose_text_does_not_fit_its_span_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not match its span"):
        TextChunk(0, 0, 5, "abc", ChunkStrategy.WINDOW, None, 1)


@pytest.mark.parametrize(("start", "end"), [(-1, 3), (5, 5), (6, 2)])
def test_a_chunk_span_must_be_ordered(start: int, end: int) -> None:
    with pytest.raises(ValueError, match="invalid span"):
        TextChunk(0, start, end, "x" * max(end - start, 0), ChunkStrategy.WINDOW, None, 1)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, -1)])
def test_window_spec_rejects_impossible_windows(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        WindowSpec(size, overlap)
