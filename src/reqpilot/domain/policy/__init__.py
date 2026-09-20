"""Centralised authorization policy (architecture ADR-009).

Every authorization decision in ReqPilot flows through :func:`can`. Scattered
ad-hoc checks are what the architecture explicitly rules out, because they
cannot be tested as a matrix.
"""

from reqpilot.domain.policy.policy import (
    Actor,
    Decision,
    ResourceRef,
    can,
    require,
)

__all__ = ["Actor", "Decision", "ResourceRef", "can", "require"]
