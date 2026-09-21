"""Requirement identifiers and the exact-version hash.

Both are pure functions, and both are load-bearing: the identifier is the stable
name a requirement keeps across versions, and the hash is what makes "approved
exactly this version" verifiable.
"""

from __future__ import annotations

import pytest

from reqpilot.domain.errors import RequirementIdError
from reqpilot.domain.requirement_ids import (
    RequirementKind,
    is_valid_requirement_id,
    next_requirement_id,
    normalise_domain,
    parse_requirement_id,
)
from reqpilot.domain.versioning import (
    canonical_version_payload,
    compute_version_hash,
    hashes_match,
)

pytestmark = pytest.mark.unit


# --- identifiers ------------------------------------------------------------


@pytest.mark.parametrize(
    "human_id",
    ["FR-LOAN-001", "FR-LOAN-014", "NFR-PAY-999", "FR-KYC2-007", "NFR-A1-1234"],
)
def test_valid_identifiers_are_accepted(human_id: str) -> None:
    assert is_valid_requirement_id(human_id)


@pytest.mark.parametrize(
    "human_id",
    [
        "",
        "FR-LOAN",  # no sequence
        "FR-LOAN-1",  # sequence too short
        "XR-LOAN-001",  # wrong prefix
        "fr-loan-001",  # lowercase
        "FR--001",  # empty domain
        "FR-LOAN-001-EXTRA",  # trailing junk
        "PREFIX FR-LOAN-001",  # not anchored
        "FR-LOAN-00A",  # non-numeric sequence
        "FR-L-001",  # domain too short
    ],
)
def test_invalid_identifiers_are_rejected(human_id: str) -> None:
    assert not is_valid_requirement_id(human_id)
    with pytest.raises(RequirementIdError):
        parse_requirement_id(human_id)


def test_parsing_returns_the_parts() -> None:
    parts = parse_requirement_id("FR-LOAN-014")
    assert parts.kind is RequirementKind.FUNCTIONAL
    assert parts.domain == "LOAN"
    assert parts.sequence == 14
    assert parts.render() == "FR-LOAN-014"


def test_non_functional_prefix_is_distinct() -> None:
    assert parse_requirement_id("NFR-LOAN-014").kind is RequirementKind.NON_FUNCTIONAL


def test_allocation_starts_at_one() -> None:
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "LOAN", []) == "FR-LOAN-001"


def test_allocation_continues_from_the_highest() -> None:
    existing = ["FR-LOAN-001", "FR-LOAN-007", "FR-LOAN-003"]
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "LOAN", existing) == "FR-LOAN-008"


def test_allocation_is_per_kind_and_per_domain() -> None:
    """Series must not interfere with one another."""
    existing = ["FR-LOAN-009", "NFR-LOAN-004", "FR-PAY-012"]
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "LOAN", existing) == "FR-LOAN-010"
    assert next_requirement_id(RequirementKind.NON_FUNCTIONAL, "LOAN", existing) == "NFR-LOAN-005"
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "PAY", existing) == "FR-PAY-013"
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "KYC", existing) == "FR-KYC-001"


def test_allocation_skips_unparseable_existing_ids() -> None:
    """A stored id that no longer parses must not block new requirements."""
    existing = ["FR-LOAN-002", "garbage", "", "FR-LOAN-legacy"]
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "LOAN", existing) == "FR-LOAN-003"


def test_allocation_beyond_three_digits_keeps_growing() -> None:
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "LOAN", ["FR-LOAN-999"]) == (
        "FR-LOAN-1000"
    )


def test_domains_are_normalised_to_one_canonical_form() -> None:
    """``loan`` and ``LOAN`` must not produce two parallel series."""
    assert normalise_domain("loan") == "LOAN"
    assert normalise_domain("  Loan  ") == "LOAN"
    assert next_requirement_id(RequirementKind.FUNCTIONAL, "loan", ["FR-LOAN-003"]) == (
        "FR-LOAN-004"
    )


@pytest.mark.parametrize("bad", ["", "L", "1LOAN", "LOAN-X", "A" * 20])
def test_invalid_domains_are_rejected(bad: str) -> None:
    with pytest.raises(RequirementIdError):
        normalise_domain(bad)


# --- version hashing --------------------------------------------------------


def base_kwargs(**overrides):
    kwargs = {
        "requirement_id": "11111111-1111-1111-1111-111111111111",
        "human_id": "FR-LOAN-001",
        "version_no": 1,
        "statement": "The system shall verify identity.",
        "category": "functional",
        "priority": "must",
        "justification": "regulatory",
        "dependencies": ["FR-LOAN-002"],
        "assumptions": ["applicant is an individual"],
        "source_refs": [{"kind": "utterance", "ref": "i-1"}],
    }
    kwargs.update(overrides)
    return kwargs


def test_hash_is_deterministic() -> None:
    assert compute_version_hash(**base_kwargs()) == compute_version_hash(**base_kwargs())


def test_hash_is_a_sha256_hex_digest() -> None:
    digest = compute_version_hash(**base_kwargs())
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("statement", "The system shall verify identity using a passport."),
        ("version_no", 2),
        ("category", "security"),
        ("priority", "should"),
        ("justification", "different reason"),
        ("dependencies", ["FR-LOAN-003"]),
        ("assumptions", ["applicant is a company"]),
        ("source_refs", [{"kind": "utterance", "ref": "i-2"}]),
        ("human_id", "FR-LOAN-002"),
    ],
)
def test_every_governed_field_changes_the_hash(field: str, value: object) -> None:
    """If a reviewer would have read it, changing it must break the binding."""
    assert compute_version_hash(**base_kwargs()) != compute_version_hash(
        **base_kwargs(**{field: value})
    )


def test_dependency_order_is_significant() -> None:
    """Order carries meaning in a dependency list, so it is part of the hash."""
    a = compute_version_hash(**base_kwargs(dependencies=["A", "B"]))
    b = compute_version_hash(**base_kwargs(dependencies=["B", "A"]))
    assert a != b


def test_canonical_payload_is_key_order_independent() -> None:
    """The serialisation must not depend on dict ordering."""
    first = canonical_version_payload(**base_kwargs())
    shuffled = dict(reversed(list(base_kwargs().items())))
    assert canonical_version_payload(**shuffled) == first


def test_empty_and_none_collections_are_equivalent() -> None:
    assert compute_version_hash(**base_kwargs(dependencies=None)) == compute_version_hash(
        **base_kwargs(dependencies=[])
    )


def test_hash_comparison_treats_missing_as_no_match() -> None:
    """An approval that recorded no binding covers nothing."""
    assert hashes_match("abc", "abc") is True
    assert hashes_match("abc", "def") is False
    assert hashes_match(None, None) is False
    assert hashes_match("", "") is False
    assert hashes_match("abc", None) is False
