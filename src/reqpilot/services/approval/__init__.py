"""Module M9 - approval and workflow service, gates G1-G8 (architecture M).

The only path by which anything becomes APPROVED. Gate authorisation is
evaluated per-gate against ``GATE_REQUIRED_ROLES``; there is no second copy of
the gate-role policy anywhere in the codebase.
"""

from reqpilot.services.approval.service import (
    ApprovalService,
    GateOutcome,
    required_roles,
    requires_all_roles,
)

__all__ = ["ApprovalService", "GateOutcome", "required_roles", "requires_all_roles"]
