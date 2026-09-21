"""Requirement-version lifecycle states (architecture H.1).

**State lives on the version, not on the requirement** (`[DESIGN] D13`). That is
load-bearing for G7: an approved version can stay approved and baselined while a
successor version is being worked on.

**There is deliberately no `CONFLICTED` state** (`[DESIGN] D12`, settled at P0
closure). A requirement may be simultaneously conflicted, awaiting clarification
and pending approval, so conflict is an orthogonal *transition guard* rather than
a mutually exclusive state. See :mod:`reqpilot.domain.lifecycle.transitions`.
"""

from __future__ import annotations

from enum import StrEnum


class RequirementState(StrEnum):
    """The fourteen states from architecture H.1.

    P1 implements the repository lifecycle. The states whose *entry conditions*
    are produced by later roadmap phases (extraction, classification, quality
    analysis) exist here because the architecture defines them and because the
    transition table must be complete; P1 reaches them through explicit,
    deterministic, human-driven operations rather than through analysis.
    """

    CANDIDATE = "CANDIDATE"
    EXTRACTED = "EXTRACTED"
    CLASSIFIED = "CLASSIFIED"
    ANALYZED = "ANALYZED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CLARIFIED = "CLARIFIED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BASELINED = "BASELINED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    INVALID = "INVALID"


#: States from which no further progress is possible. A version in one of these
#: is history: it is retained for audit and excluded from baselines.
TERMINAL_STATES: frozenset[RequirementState] = frozenset(
    {
        RequirementState.SUPERSEDED,
        RequirementState.WITHDRAWN,
        RequirementState.INVALID,
    }
)

#: States in which a version counts as governed-and-accepted. Only these may
#: enter a baseline (architecture H.4).
APPROVED_STATES: frozenset[RequirementState] = frozenset(
    {
        RequirementState.APPROVED,
        RequirementState.BASELINED,
    }
)

#: States that require a recorded human approval decision to have been entered.
#: Used by the baseline service to refuse anything that reached its state by
#: another route.
REQUIRES_APPROVAL_DECISION: frozenset[RequirementState] = frozenset(
    {
        RequirementState.APPROVED,
        RequirementState.REJECTED,
        RequirementState.BASELINED,
    }
)

#: A version in one of these is still editable in the sense that creating a
#: successor does not require a change gate. Editing an APPROVED or BASELINED
#: version requires G7 (architecture H.3).
PRE_APPROVAL_STATES: frozenset[RequirementState] = frozenset(
    {
        RequirementState.CANDIDATE,
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
        RequirementState.CLARIFICATION_REQUIRED,
        RequirementState.CLARIFIED,
        RequirementState.VALIDATED,
        RequirementState.PENDING_APPROVAL,
    }
)
