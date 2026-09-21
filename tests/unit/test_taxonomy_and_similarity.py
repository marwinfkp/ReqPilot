"""The thirteen-category taxonomy and the near-duplicate measures (FR-CLS-001, FR-EXT-005)."""

from __future__ import annotations

import pytest

from reqpilot.domain.classification import (
    PROBLEM_STATEMENT_LABELS,
    display_label,
    normalise_category,
)
from reqpilot.domain.enums import RequirementCategory as C
from reqpilot.domain.similarity import is_exact_duplicate, normalised_tokens, token_jaccard

pytestmark = pytest.mark.unit


def test_the_taxonomy_is_exactly_the_thirteen_approved_categories() -> None:
    assert len(C) == 13
    assert set(PROBLEM_STATEMENT_LABELS) == set(C)
    assert sorted(PROBLEM_STATEMENT_LABELS.values()) == sorted(
        [
            "business",
            "stakeholder",
            "functional",
            "security",
            "privacy",
            "regulatory",
            "performance",
            "availability/reliability",
            "usability",
            "data-management",
            "integration",
            "audit/reporting",
            "operational/maintenance",
        ]
    )


@pytest.mark.parametrize("category", list(C))
def test_every_category_normalises_from_its_value_and_its_problem_statement_spelling(
    category: C,
) -> None:
    assert normalise_category(category.value) is category
    assert normalise_category(display_label(category)) is category
    assert normalise_category(display_label(category).upper()) is category
    assert normalise_category(f"  {display_label(category)}  ") is category


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Availability/Reliability", C.AVAILABILITY),
        ("reliability", C.AVAILABILITY),
        ("availability and reliability", C.AVAILABILITY),
        ("Data Management", C.DATA_MANAGEMENT),
        ("data_management", C.DATA_MANAGEMENT),
        ("audit", C.AUDIT_REPORTING),
        ("Reporting", C.AUDIT_REPORTING),
        ("maintenance", C.OPERATIONAL),
    ],
)
def test_spellings_of_approved_categories_normalise(raw: str, expected: C) -> None:
    assert normalise_category(raw) is expected


@pytest.mark.parametrize(
    "raw", ["compliance", "legal", "non-functional", "risk", "security & privacy", "", "   "]
)
def test_anything_else_is_unknown_and_never_guessed(raw: str) -> None:
    assert normalise_category(raw) is None


def test_exact_duplicates_ignore_case_punctuation_and_the_shared_opening() -> None:
    a = "The system shall show the applicant the status of their application."
    b = "the system SHALL show the applicant the status of their application"
    assert is_exact_duplicate(a, b)
    assert normalised_tokens(a)[:2] == ("show", "the")


def test_a_negation_is_never_an_exact_duplicate_even_when_nearly_identical() -> None:
    a = "The system shall encrypt customer data at rest."
    b = "The system shall not encrypt customer data at rest."
    assert not is_exact_duplicate(a, b)
    assert token_jaccard(a, b) >= 0.8, "similar enough to ask a human, never to merge"


def test_similarity_is_a_symmetric_value_in_unit_range() -> None:
    a, b = "The system shall export statements.", "The system shall print reports."
    assert token_jaccard(a, b) == token_jaccard(b, a)
    assert 0.0 <= token_jaccard(a, b) < 0.5
    assert token_jaccard("", "") == 0.0
    assert not is_exact_duplicate("The system shall", "The system shall")
