"""Tests pinning the approved architectural baseline into executable form.

These assert facts from the approved Phase 0 analysis and architecture that
must not drift as code is added: the thirteen agent roles, their 4+5+1+3
implementation split, the eight platform gates, and the forbidden-state rule.

If one of these fails, either the code drifted or the baseline changed. Neither
should happen silently.
"""

from __future__ import annotations

from typing import TypedDict

import pytest

from reqpilot.domain.enums import (
    GATE_REQUIRED_ROLES,
    ROLE_IMPLEMENTATION_KIND,
    AgentRole,
    Gate,
    ImplementationKind,
    Role,
)
from reqpilot.domain.ids import GraphRunId, new_graph_run_id, thread_id_for
from reqpilot.domain.refs import EvidenceKind, EvidenceRef, ReviewSignal
from reqpilot.graph.state import FORBIDDEN_STATE_FIELDS, assert_state_shape

pytestmark = pytest.mark.unit


# --- the 13 agent roles --------------------------------------------------


def test_there_are_exactly_thirteen_agent_roles() -> None:
    assert len(list(AgentRole)) == 13


def test_every_role_has_an_implementation_kind() -> None:
    assert set(ROLE_IMPLEMENTATION_KIND) == set(AgentRole)


def test_implementation_split_is_four_five_one_three() -> None:
    """Architecture E.0: 4 LLM-driven, 5 hybrid, 1 assembler, 3 deterministic."""
    counts: dict[ImplementationKind, int] = {}
    for kind in ROLE_IMPLEMENTATION_KIND.values():
        counts[kind] = counts.get(kind, 0) + 1

    assert counts[ImplementationKind.LLM_DRIVEN] == 4
    assert counts[ImplementationKind.HYBRID] == 5
    assert counts[ImplementationKind.LLM_ASSISTED_ASSEMBLER] == 1
    assert counts[ImplementationKind.DETERMINISTIC] == 3
    assert sum(counts.values()) == 13


def test_ten_roles_use_llm_capabilities() -> None:
    """Architecture E.0: ten of thirteen invoke or use LLM capabilities."""
    llm_using = {
        role
        for role, kind in ROLE_IMPLEMENTATION_KIND.items()
        if kind is not ImplementationKind.DETERMINISTIC
    }
    assert len(llm_using) == 10


def test_governance_roles_are_deterministic() -> None:
    """Coordinator, Validation and Human Approval must never be LLM-driven."""
    for role in (AgentRole.COORDINATOR, AgentRole.VALIDATION, AgentRole.HUMAN_APPROVAL):
        assert ROLE_IMPLEMENTATION_KIND[role] is ImplementationKind.DETERMINISTIC


def test_authority_bearing_roles_are_hybrid_not_llm_driven() -> None:
    """Risk and SDLC hold deterministic authority; they are hybrid, never LLM-driven."""
    for role in (AgentRole.RISK_ANALYSIS, AgentRole.SDLC_SELECTION):
        assert ROLE_IMPLEMENTATION_KIND[role] is ImplementationKind.HYBRID


# --- the 8 platform gates ------------------------------------------------


def test_there_are_exactly_eight_platform_gates() -> None:
    assert len(list(Gate)) == 8


def test_production_readiness_is_not_a_platform_gate() -> None:
    """It belongs inside the generated project's workflow, not here."""
    names = {g.name.lower() for g in Gate}
    assert not any("production" in n or "readiness" in n for n in names)


def test_every_gate_names_required_roles() -> None:
    assert set(GATE_REQUIRED_ROLES) == set(Gate)
    for gate, roles in GATE_REQUIRED_ROLES.items():
        assert roles, f"gate {gate} has no required role"


def test_g6_requires_multiple_roles() -> None:
    """SDLC selection is approved by several roles, not one."""
    assert len(GATE_REQUIRED_ROLES[Gate.G6_SDLC_SELECTION]) >= 3


def test_g3_and_g8_require_security_review() -> None:
    """The two deterministic-severity gates route to the security reviewer."""
    assert Role.SECURITY_REVIEWER in GATE_REQUIRED_ROLES[Gate.G3_HIGH_RISK_SECURITY]
    assert Role.SECURITY_REVIEWER in GATE_REQUIRED_ROLES[Gate.G8_HIGH_SEVERITY_RISK]


# --- the 7 human roles ---------------------------------------------------


def test_there_are_exactly_seven_human_roles() -> None:
    assert len(list(Role)) == 7


# --- identifiers ---------------------------------------------------------


def test_thread_id_is_derived_from_the_run_id() -> None:
    """One thread per run, derived from the run id (architecture C.7)."""
    run_id: GraphRunId = new_graph_run_id()
    assert thread_id_for(run_id) == str(run_id)


def test_run_ids_are_unique() -> None:
    ids = {new_graph_run_id() for _ in range(100)}
    assert len(ids) == 100


# --- provenance types ----------------------------------------------------


def test_evidence_ref_rejects_a_half_specified_span() -> None:
    with pytest.raises(ValueError, match="together"):
        EvidenceRef(
            kind=EvidenceKind.SOURCE_CHUNK,
            target_id=new_graph_run_id(),
            char_start=5,
        )


def test_evidence_ref_rejects_an_inverted_span() -> None:
    with pytest.raises(ValueError, match="greater than"):
        EvidenceRef(
            kind=EvidenceKind.SOURCE_CHUNK,
            target_id=new_graph_run_id(),
            char_start=10,
            char_end=5,
        )


def test_evidence_ref_is_immutable() -> None:
    ref = EvidenceRef(kind=EvidenceKind.UTTERANCE, target_id=new_graph_run_id())
    with pytest.raises(ValueError):
        ref.target_id = new_graph_run_id()  # type: ignore[misc]


def test_review_signal_carries_its_caveat() -> None:
    """A review signal must never travel without its interpretation.

    Approved Phase 0 H.1: these are review-prioritisation signals, not
    calibrated probabilities.
    """
    signal = ReviewSignal(value=0.42)
    assert "not a calibrated probability" in signal.interpretation


def test_review_signal_is_bounded() -> None:
    with pytest.raises(ValueError):
        ReviewSignal(value=1.5)


# --- graph state discipline ----------------------------------------------


def test_forbidden_state_fields_are_detected() -> None:
    """Content-bearing fields must not be declared in graph state."""

    class BadState(TypedDict, total=False):
        run_id: str
        chunk_text: str  # content belongs in the database, not in a checkpoint

    with pytest.raises(ValueError, match="forbidden content-bearing"):
        assert_state_shape(BadState)


def test_well_formed_state_passes() -> None:
    class GoodState(TypedDict, total=False):
        run_id: str
        requirement_ids: list[str]
        needs_clarification: bool

    assert_state_shape(GoodState)


def test_forbidden_field_list_covers_the_obvious_leaks() -> None:
    for field in ("prompt", "chunk_text", "api_key", "secret"):
        assert field in FORBIDDEN_STATE_FIELDS
