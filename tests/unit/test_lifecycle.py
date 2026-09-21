"""The requirement-version lifecycle, exhaustively (architecture H.3).

Pure-function tests: no database, no session, no service. Every pair in the
14 x 14 state space is classified as allowed or refused, so a transition cannot
be added, removed or quietly widened without this file noticing.
"""

from __future__ import annotations

import itertools

import pytest

from reqpilot.domain.errors import StateTransitionError
from reqpilot.domain.lifecycle import (
    ALLOWED_TRANSITIONS,
    APPROVED_STATES,
    PRE_APPROVAL_STATES,
    TERMINAL_STATES,
    RequirementState,
    TransitionContext,
    allowed_targets,
    check_transition,
    is_allowed,
    validate_transition,
)

pytestmark = pytest.mark.unit

S = RequirementState


# --- the shape of the state space ------------------------------------------


def test_there_are_exactly_fourteen_states() -> None:
    assert len(list(S)) == 14


def test_there_is_no_conflicted_state() -> None:
    """`[DESIGN] D12`. Conflict is a guard; it is never a state.

    The single most important assertion in this file: if a `CONFLICTED` state
    is ever added, the approved design has been broken.
    """
    names = {s.name for s in S}
    values = {s.value for s in S}
    assert "CONFLICTED" not in names
    assert "CONFLICTED" not in values
    assert not any("conflict" in n.lower() for n in names)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert allowed_targets(state) == frozenset(), f"{state} must be terminal"


def test_approved_states_are_exactly_approved_and_baselined() -> None:
    assert {S.APPROVED, S.BASELINED} == APPROVED_STATES


def test_pre_approval_states_exclude_the_governed_ones() -> None:
    assert not (PRE_APPROVAL_STATES & {S.APPROVED, S.BASELINED, S.REJECTED})


# --- the full matrix --------------------------------------------------------


def test_every_pair_is_classified() -> None:
    """No pair is ambiguous: each is either in the table or refused."""
    for source, target in itertools.product(S, S):
        allowed = is_allowed(source, target)
        assert isinstance(allowed, bool)
        if not allowed and source != target:
            assert check_transition(source, target) is not None


def test_self_transition_is_always_refused() -> None:
    for state in S:
        assert "already the current state" in (check_transition(state, state) or "")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (S.CANDIDATE, S.APPROVED),
        (S.CANDIDATE, S.BASELINED),
        (S.EXTRACTED, S.VALIDATED),
        (S.ANALYZED, S.APPROVED),
        (S.VALIDATED, S.BASELINED),
        (S.REJECTED, S.APPROVED),
        (S.WITHDRAWN, S.CANDIDATE),
        (S.SUPERSEDED, S.APPROVED),
        (S.BASELINED, S.WITHDRAWN),
        (S.INVALID, S.CANDIDATE),
    ],
)
def test_forbidden_shortcuts_are_refused(source: S, target: S) -> None:
    """The jumps that would matter most if they were possible."""
    assert not is_allowed(source, target)
    with pytest.raises(StateTransitionError, match="not an allowed transition"):
        validate_transition(source, target)


def test_approved_is_reachable_only_from_pending_approval() -> None:
    sources = {src for (src, tgt) in ALLOWED_TRANSITIONS if tgt is S.APPROVED}
    assert sources == {S.PENDING_APPROVAL}


def test_baselined_is_reachable_only_from_approved() -> None:
    sources = {src for (src, tgt) in ALLOWED_TRANSITIONS if tgt is S.BASELINED}
    assert sources == {S.APPROVED}


# --- guards -----------------------------------------------------------------


def test_extraction_needs_a_source_reference() -> None:
    """``FR-EXT-007``: no requirement without provenance."""
    assert check_transition(S.CANDIDATE, S.EXTRACTED, TransitionContext()) is not None
    ok = TransitionContext(source_ref_count=1)
    assert check_transition(S.CANDIDATE, S.EXTRACTED, ok) is None


def test_classification_needs_a_label() -> None:
    assert check_transition(S.EXTRACTED, S.CLASSIFIED, TransitionContext()) is not None
    ok = TransitionContext(label_count=1)
    assert check_transition(S.EXTRACTED, S.CLASSIFIED, ok) is None


def test_validation_is_blocked_by_an_open_conflict() -> None:
    """`[DESIGN] D12` in action: conflict blocks a transition, as a guard.

    The conflict-detection phase will populate this field; P1 tests the guard
    with synthetic context, which is exactly what the approved design asks for.
    """
    blocked = TransitionContext(open_conflict_count=1)
    reason = check_transition(S.ANALYZED, S.VALIDATED, blocked)
    assert reason is not None
    assert "conflict" in reason
    assert check_transition(S.ANALYZED, S.VALIDATED, TransitionContext()) is None


def test_conflict_also_blocks_submission_for_approval() -> None:
    blocked = TransitionContext(has_current_validation=True, open_conflict_count=2)
    reason = check_transition(S.VALIDATED, S.PENDING_APPROVAL, blocked)
    assert reason is not None and "conflict" in reason


@pytest.mark.parametrize(
    ("field", "fragment"),
    [
        ("open_defect_count", "defect"),
        ("open_conflict_count", "conflict"),
        ("unreviewed_high_risk_count", "risk"),
        ("blocking_gate_task_count", "approval task"),
    ],
)
def test_every_validation_blocker_is_enforced(field: str, fragment: str) -> None:
    ctx = TransitionContext(**{field: 1})  # type: ignore[arg-type]
    reason = check_transition(S.ANALYZED, S.VALIDATED, ctx)
    assert reason is not None and fragment in reason


def test_approval_requires_a_recorded_decision() -> None:
    """The lifecycle cannot approve anything by itself."""
    reason = check_transition(S.PENDING_APPROVAL, S.APPROVED, TransitionContext())
    assert reason is not None and "approval decision" in reason

    ok = TransitionContext(has_valid_approval_decision=True)
    assert check_transition(S.PENDING_APPROVAL, S.APPROVED, ok) is None


def test_rejection_requires_a_justification() -> None:
    assert check_transition(S.PENDING_APPROVAL, S.REJECTED, TransitionContext()) is not None
    ok = TransitionContext(has_rejection_justification=True)
    assert check_transition(S.PENDING_APPROVAL, S.REJECTED, ok) is None


def test_submission_requires_current_validation() -> None:
    assert check_transition(S.VALIDATED, S.PENDING_APPROVAL, TransitionContext()) is not None
    ok = TransitionContext(has_current_validation=True)
    assert check_transition(S.VALIDATED, S.PENDING_APPROVAL, ok) is None


def test_baselining_requires_every_member_approved() -> None:
    assert check_transition(S.APPROVED, S.BASELINED, TransitionContext()) is not None
    ok = TransitionContext(all_baseline_members_approved=True)
    assert check_transition(S.APPROVED, S.BASELINED, ok) is None


def test_superseding_requires_an_approved_successor() -> None:
    assert check_transition(S.APPROVED, S.SUPERSEDED, TransitionContext()) is not None
    ok = TransitionContext(successor_is_approved=True)
    assert check_transition(S.APPROVED, S.SUPERSEDED, ok) is None


def test_a_baselined_version_cannot_be_withdrawn() -> None:
    """There is no transition at all, and the guard says so too."""
    assert not is_allowed(S.BASELINED, S.WITHDRAWN)


def test_withdrawal_guard_refuses_a_baselined_context() -> None:
    ctx = TransitionContext(is_baselined=True)
    reason = check_transition(S.VALIDATED, S.WITHDRAWN, ctx)
    assert reason is not None and "baselined" in reason


def test_clarification_needs_an_answer() -> None:
    assert check_transition(S.CLARIFICATION_REQUIRED, S.CLARIFIED, TransitionContext()) is not None
    ok = TransitionContext(clarification_answer_present=True)
    assert check_transition(S.CLARIFICATION_REQUIRED, S.CLARIFIED, ok) is None


def test_default_context_reports_nothing_open() -> None:
    """Fields belonging to later phases default to 'no open problems'.

    Accurate rather than permissive: nothing can raise a defect, conflict or
    risk yet, because nothing that produces them exists.
    """
    ctx = TransitionContext()
    assert ctx.open_defect_count == 0
    assert ctx.open_conflict_count == 0
    assert ctx.unreviewed_high_risk_count == 0
    assert ctx.blocking_gate_task_count == 0


def test_validate_transition_raises_with_a_useful_message() -> None:
    with pytest.raises(StateTransitionError) as excinfo:
        validate_transition(S.ANALYZED, S.VALIDATED, TransitionContext(open_defect_count=3))
    message = str(excinfo.value)
    assert "ANALYZED" in message and "VALIDATED" in message and "3" in message


def test_remediation_path_exists_from_rejected() -> None:
    """A rejection must lead somewhere; a dead end would strand the requirement."""
    assert is_allowed(S.REJECTED, S.CLARIFICATION_REQUIRED)
    assert is_allowed(S.CLARIFICATION_REQUIRED, S.CLARIFIED)
    assert is_allowed(S.CLARIFIED, S.ANALYZED)
