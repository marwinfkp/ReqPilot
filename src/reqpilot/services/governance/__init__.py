"""Approval governance P8 completes (architecture M.3, M.5; ``FR-HIL-001``..``-006``).

* :mod:`~reqpilot.services.governance.gates` - G4, G5 and G7 as subjects of the
  one approval service (binding, authors, settlement, raising G4).
* :mod:`~reqpilot.services.governance.readiness` - what still blocks a version
  from G1, a baseline or an authoritative artefact (``FR-HIL-004``).
* :mod:`~reqpilot.services.governance.fanout` - raising the G4/G5/G7 tasks the
  persisted state requires; the analyst's architecture-critical flag.
* :mod:`~reqpilot.services.governance.queue` - the single review queue
  (``FR-HIL-006``). Presentation only; it decides nothing.
"""

from reqpilot.services.governance.fanout import FanOutResult, GovernanceFanOut
from reqpilot.services.governance.gates import (
    GOVERNANCE_GATES,
    ConflictGateRaiser,
    GovernanceGateService,
    conflict_gate_hash,
    is_governance_task,
)
from reqpilot.services.governance.queue import ReviewQueueView, UnifiedReviewQueue
from reqpilot.services.governance.readiness import (
    GovernanceReadinessService,
    Readiness,
    packaged_g5_threshold,
)

__all__ = [
    "GOVERNANCE_GATES",
    "ConflictGateRaiser",
    "FanOutResult",
    "GovernanceFanOut",
    "GovernanceGateService",
    "GovernanceReadinessService",
    "Readiness",
    "ReviewQueueView",
    "UnifiedReviewQueue",
    "conflict_gate_hash",
    "is_governance_task",
    "packaged_g5_threshold",
]
