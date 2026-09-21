"""The requirement-version transition table and its guards (architecture H.3).

Pure functions over explicit inputs: no database, no I/O, no LangGraph, no LLM.
That is what makes the whole transition matrix - including every *rejected*
transition - exhaustively unit-testable.

Two design points worth stating, because both are load-bearing:

**Conflict is a guard, not a state** (`[DESIGN] D12`). There is no `CONFLICTED`
state. An open conflict is one field of :class:`TransitionContext` that blocks
`ANALYZED → VALIDATED` and `VALIDATED → PENDING_APPROVAL`. Conflict *detection*
belongs to a later roadmap phase; this module only provides the guard the
detector will later feed, and P1 tests it with synthetic context values.

**Approval is not a transition input.** `PENDING_APPROVAL → APPROVED` is guarded
by ``has_valid_approval_decision``, which the approval service sets only after a
real decision row exists. The lifecycle cannot approve anything by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reqpilot.domain.errors import StateTransitionError
from reqpilot.domain.lifecycle.states import RequirementState as S


@dataclass(frozen=True)
class TransitionContext:
    """Everything a guard may consult, supplied by the caller.

    Fields are grouped by which roadmap phase populates them. P1 supplies real
    values for what exists and leaves the rest at their safe defaults - zero
    open problems - because the entities that would raise them do not exist yet.
    A later phase populates its own field without touching this module.
    """

    # --- populated by P1 (the requirements repository) -------------------
    source_ref_count: int = 0
    label_count: int = 0
    has_current_validation: bool = False
    has_valid_approval_decision: bool = False
    has_rejection_justification: bool = False
    all_baseline_members_approved: bool = False
    successor_is_approved: bool = False
    is_baselined: bool = False

    # --- populated by later roadmap phases; default = nothing open -------
    #: Quality analysis phase. An open defect blocks VALIDATED.
    open_defect_count: int = 0
    #: Conflict-detection phase. `[DESIGN] D12` - a guard, never a state.
    open_conflict_count: int = 0
    #: Risk-analysis phase. An unreviewed High risk blocks VALIDATED.
    unreviewed_high_risk_count: int = 0
    #: Compliance / security / architecture gates (G2, G3, G5) still open.
    blocking_gate_task_count: int = 0
    #: Clarification phase.
    clarification_answer_present: bool = False

    #: Free-form notes a caller may attach for error messages. Never consulted.
    notes: tuple[str, ...] = field(default=())


def _guard_extracted(ctx: TransitionContext) -> str | None:
    if ctx.source_ref_count < 1:
        return "a requirement version must carry at least one source reference"
    return None


def _guard_classified(ctx: TransitionContext) -> str | None:
    if ctx.label_count < 1:
        return "at least one classification label is required"
    return None


def _guard_clarified(ctx: TransitionContext) -> str | None:
    if not ctx.clarification_answer_present:
        return "a non-empty clarification answer is required"
    return None


def _guard_validated(ctx: TransitionContext) -> str | None:
    """The gate that protects the whole approval path (architecture H.3).

    Every blocking condition the architecture names is checked here. The ones
    whose producers do not exist yet read zero, which is correct rather than
    permissive: there genuinely are no open defects when nothing can raise one.
    """
    if ctx.open_defect_count:
        return f"{ctx.open_defect_count} open quality defect(s) must be resolved"
    if ctx.open_conflict_count:
        # D12: conflict blocks the transition; it is not a state of its own.
        return f"{ctx.open_conflict_count} open conflict(s) must be resolved"
    if ctx.unreviewed_high_risk_count:
        return f"{ctx.unreviewed_high_risk_count} unreviewed high-severity risk(s) must be reviewed"
    if ctx.blocking_gate_task_count:
        return f"{ctx.blocking_gate_task_count} blocking approval task(s) must be resolved"
    return None


def _guard_pending_approval(ctx: TransitionContext) -> str | None:
    if not ctx.has_current_validation:
        return "the validation result must be current for this version"
    if ctx.open_conflict_count:
        # D12 again: the guard applies at both transitions the architecture names.
        return f"{ctx.open_conflict_count} open conflict(s) block submission for approval"
    return None


def _guard_approved(ctx: TransitionContext) -> str | None:
    if not ctx.has_valid_approval_decision:
        return (
            "a recorded, role-appropriate approval decision bound to this exact "
            "version is required; the lifecycle cannot approve anything by itself"
        )
    return None


def _guard_rejected(ctx: TransitionContext) -> str | None:
    if not ctx.has_rejection_justification:
        return "a rejection must carry a justification"
    return None


def _guard_baselined(ctx: TransitionContext) -> str | None:
    if not ctx.all_baseline_members_approved:
        return "every baseline member must be APPROVED with a matching decision"
    return None


def _guard_superseded(ctx: TransitionContext) -> str | None:
    if not ctx.successor_is_approved:
        return "a version is superseded only once its successor is approved"
    return None


def _guard_withdrawn(ctx: TransitionContext) -> str | None:
    if ctx.is_baselined:
        return "a baselined version cannot be withdrawn"
    return None


#: The approved transition table (architecture H.3). A pair absent from this
#: mapping is refused - there is no permissive fallthrough, and no transition
#: may be performed by assigning to the state column directly.
ALLOWED_TRANSITIONS: dict[tuple[S, S], object] = {
    (S.CANDIDATE, S.EXTRACTED): _guard_extracted,
    (S.EXTRACTED, S.CLASSIFIED): _guard_classified,
    (S.CLASSIFIED, S.ANALYZED): None,
    (S.ANALYZED, S.CLARIFICATION_REQUIRED): None,
    (S.CLARIFICATION_REQUIRED, S.CLARIFIED): _guard_clarified,
    (S.CLARIFIED, S.ANALYZED): None,
    (S.ANALYZED, S.VALIDATED): _guard_validated,
    (S.VALIDATED, S.PENDING_APPROVAL): _guard_pending_approval,
    (S.VALIDATED, S.ANALYZED): None,  # re-analysis after a change
    (S.PENDING_APPROVAL, S.APPROVED): _guard_approved,
    (S.PENDING_APPROVAL, S.REJECTED): _guard_rejected,
    (S.REJECTED, S.CLARIFICATION_REQUIRED): None,  # remediation
    (S.APPROVED, S.BASELINED): _guard_baselined,
    (S.APPROVED, S.SUPERSEDED): _guard_superseded,
    (S.BASELINED, S.SUPERSEDED): _guard_superseded,
    # Withdrawal and invalidation are available from the pre-approval states.
    (S.CANDIDATE, S.WITHDRAWN): _guard_withdrawn,
    (S.EXTRACTED, S.WITHDRAWN): _guard_withdrawn,
    (S.CLASSIFIED, S.WITHDRAWN): _guard_withdrawn,
    (S.ANALYZED, S.WITHDRAWN): _guard_withdrawn,
    (S.CLARIFICATION_REQUIRED, S.WITHDRAWN): _guard_withdrawn,
    (S.CLARIFIED, S.WITHDRAWN): _guard_withdrawn,
    (S.VALIDATED, S.WITHDRAWN): _guard_withdrawn,
    (S.PENDING_APPROVAL, S.WITHDRAWN): _guard_withdrawn,
    (S.REJECTED, S.WITHDRAWN): _guard_withdrawn,
    (S.CANDIDATE, S.INVALID): None,
    (S.EXTRACTED, S.INVALID): None,
}


def is_allowed(source: S, target: S) -> bool:
    """Return whether the pair appears in the approved transition table."""
    return (source, target) in ALLOWED_TRANSITIONS


def check_transition(
    source: S,
    target: S,
    ctx: TransitionContext | None = None,
) -> str | None:
    """Return ``None`` if the transition is permitted, else the reason it is not.

    Separated from :func:`validate_transition` so callers that want to *display*
    why an action is unavailable do not have to catch an exception.
    """
    if source == target:
        return f"{source} is already the current state"
    if (source, target) not in ALLOWED_TRANSITIONS:
        return f"{source} -> {target} is not an allowed transition"
    guard = ALLOWED_TRANSITIONS[(source, target)]
    if guard is None:
        return None
    return guard(ctx or TransitionContext())  # type: ignore[operator]


def validate_transition(
    source: S,
    target: S,
    ctx: TransitionContext | None = None,
) -> None:
    """Raise :class:`StateTransitionError` unless the transition is permitted."""
    reason = check_transition(source, target, ctx)
    if reason is not None:
        raise StateTransitionError(f"{source} -> {target}: {reason}")


def allowed_targets(source: S) -> frozenset[S]:
    """Every state reachable from ``source`` before guards are evaluated."""
    return frozenset(target for (src, target) in ALLOWED_TRANSITIONS if src == source)
