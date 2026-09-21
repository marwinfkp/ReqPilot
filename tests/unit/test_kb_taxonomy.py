"""The knowledge-base vocabulary: C.1 taxonomy, licences, versions, chunk identity.

These are the rules that stop the knowledge base from blurring a standard into
a law, a paraphrase into a copied text, or one KB version into another.
"""

from __future__ import annotations

import uuid

import pytest

from reqpilot.domain.enums import (
    LICENCE_PERMITTED_ORIGINS,
    SOURCE_TYPE_BINDING,
    SYNTHETIC_PERMITTED_TYPES,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    SupersessionKind,
    TextOrigin,
)
from reqpilot.domain.ids import chunk_id_for
from reqpilot.domain.models.knowledge import EMBEDDING_DIMENSION, KnowledgeItem

pytestmark = pytest.mark.unit


def test_the_taxonomy_is_exactly_the_eight_c1_types() -> None:
    """Approved Phase 0 C.1, spelled as architecture G.5 spells it."""
    assert {t.value for t in NormativeSourceType} == {
        "statute",
        "regulatory_direction",
        "regulatory_guidance",
        "org_policy",
        "contractual_scheme",
        "industry_standard",
        "control_framework",
        "best_practice",
    }


def test_there_is_no_generic_regulation_type() -> None:
    assert not any(t.value == "regulation" for t in NormativeSourceType)


def test_every_type_states_how_binding_it_is() -> None:
    assert set(SOURCE_TYPE_BINDING) == set(NormativeSourceType)


def test_standards_and_schemes_are_never_described_as_law() -> None:
    """C.1: a scheme binds by contract, a standard is voluntary, a framework is a reference."""
    assert SOURCE_TYPE_BINDING[NormativeSourceType.CONTRACTUAL_SCHEME] == (
        "Binding by contract, not law"
    )
    for voluntary in (NormativeSourceType.INDUSTRY_STANDARD, NormativeSourceType.CONTROL_FRAMEWORK):
        assert "Voluntary" in SOURCE_TYPE_BINDING[voluntary]
    assert SOURCE_TYPE_BINDING[NormativeSourceType.STATUTE] == "Legally binding"


def test_paraphrase_only_sources_never_admit_verbatim_text() -> None:
    """D.2: copyrighted standards take team-written paraphrases only."""
    assert LICENCE_PERMITTED_ORIGINS[LicenceClass.PARAPHRASE_ONLY] == {TextOrigin.TEAM_PARAPHRASE}


def test_synthetic_text_and_synthetic_sources_go_together() -> None:
    assert LICENCE_PERMITTED_ORIGINS[LicenceClass.SYNTHETIC] == {TextOrigin.SYNTHETIC}
    for licence in (LicenceClass.EXTRACT_PERMITTED, LicenceClass.PARAPHRASE_ONLY):
        assert TextOrigin.SYNTHETIC not in LICENCE_PERMITTED_ORIGINS[licence]


def test_every_licence_has_a_rule() -> None:
    assert set(LICENCE_PERMITTED_ORIGINS) == set(LicenceClass)


def test_only_policies_and_practice_notes_may_be_synthetic() -> None:
    """A synthetic statute would be a fake law."""
    assert {
        NormativeSourceType.ORG_POLICY,
        NormativeSourceType.BEST_PRACTICE,
    } == SYNTHETIC_PERMITTED_TYPES


def test_status_has_exactly_the_two_g5_values() -> None:
    """G.5: active/superseded. Retiring is a kind of supersession, not a third status."""
    assert {s.value for s in KnowledgeItemStatus} == {"active", "superseded"}
    assert {k.value for k in SupersessionKind} == {"versioned", "replaced", "retired"}


def test_embedding_dimension_is_the_approved_models() -> None:
    """ADR-005: bge-small-en-v1.5 is 384-dimensional."""
    assert EMBEDDING_DIMENSION == 384


def _item(kb_version: int, superseded_in: int | None) -> KnowledgeItem:
    return KnowledgeItem(kb_version=kb_version, superseded_in_kb_version=superseded_in)


def test_an_item_is_active_from_its_kb_version_onwards() -> None:
    item = _item(3, None)
    assert not item.active_at(2)
    assert item.active_at(3)
    assert item.active_at(99)


def test_a_superseded_item_stays_visible_to_earlier_pins() -> None:
    """J.6: a project pinned before the supersession still sees the item."""
    item = _item(3, 7)
    assert item.active_at(3)
    assert item.active_at(6)
    assert not item.active_at(7)
    assert not item.active_at(8)


def test_chunk_identity_is_derived_not_drawn() -> None:
    item = uuid.uuid4()
    first = chunk_id_for(item, 0, 0, 40, "a" * 64)
    assert first == chunk_id_for(item, 0, 0, 40, "a" * 64)


@pytest.mark.parametrize(
    "changed",
    [
        (1, 0, 40, "a" * 64),
        (0, 1, 40, "a" * 64),
        (0, 0, 41, "a" * 64),
        (0, 0, 40, "b" * 64),
    ],
)
def test_chunk_identity_changes_with_any_of_its_parts(changed: tuple[int, int, int, str]) -> None:
    item = uuid.uuid4()
    assert chunk_id_for(item, 0, 0, 40, "a" * 64) != chunk_id_for(item, *changed)
    assert chunk_id_for(item, 0, 0, 40, "a" * 64) != chunk_id_for(uuid.uuid4(), 0, 0, 40, "a" * 64)
