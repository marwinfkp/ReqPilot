"""Requirement-version lifecycle (architecture H).

State lives on the **version**, not the requirement (`[DESIGN] D13`). There is no
`CONFLICTED` state - conflict is a transition guard (`[DESIGN] D12`).

Nothing in this package performs I/O. The transition table and its guards are
pure functions so the whole matrix, including every refused transition, is
unit-testable without a database.
"""

from reqpilot.domain.lifecycle.states import (
    APPROVED_STATES,
    PRE_APPROVAL_STATES,
    REQUIRES_APPROVAL_DECISION,
    TERMINAL_STATES,
    RequirementState,
)
from reqpilot.domain.lifecycle.transitions import (
    ALLOWED_TRANSITIONS,
    TransitionContext,
    allowed_targets,
    check_transition,
    is_allowed,
    validate_transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "APPROVED_STATES",
    "PRE_APPROVAL_STATES",
    "REQUIRES_APPROVAL_DECISION",
    "TERMINAL_STATES",
    "RequirementState",
    "TransitionContext",
    "allowed_targets",
    "check_transition",
    "is_allowed",
    "validate_transition",
]
