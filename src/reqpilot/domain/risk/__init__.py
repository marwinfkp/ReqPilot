"""Risk-analysis domain logic (roadmap phase P7; architecture section I).

Pure functions and value objects, no I/O:

* :mod:`~reqpilot.domain.risk.matrix` - the approved 3x3 likelihood x impact
  matrix and the only path to an authoritative severity (``FR-RSK-004``);
* :mod:`~reqpilot.domain.risk.scope` - the ``FR-RSK-011`` scope guard, which
  refuses a proposal that reads as borrower credit risk;
* :mod:`~reqpilot.domain.risk.claims` - what validation hands to persistence,
  shaped so that a severity cannot travel with a proposal;
* :mod:`~reqpilot.domain.risk.hashing` - the content hash a G8 decision binds to.
"""

from reqpilot.domain.risk.claims import (
    AcceptedRisk,
    DroppedRisk,
    ProposedMitigation,
    RiskDropReason,
    title_key,
)
from reqpilot.domain.risk.hashing import P7_HASH_VERSION, risk_hash
from reqpilot.domain.risk.matrix import (
    APPROVED_CELLS,
    ESCALATING_SEVERITY,
    IMPACT_RANK,
    LIKELIHOOD_RANK,
    SEVERITY_RANK,
    RiskMatrix,
    SeverityComputation,
    compute_severity,
    parse_impact,
    parse_likelihood,
)
from reqpilot.domain.risk.scope import (
    SCOPE_REFUSAL_NOTICE,
    SCOPE_RULES_VERSION,
    ScopeHit,
    check_fields,
    find_out_of_scope,
)

__all__ = [
    "APPROVED_CELLS",
    "ESCALATING_SEVERITY",
    "IMPACT_RANK",
    "LIKELIHOOD_RANK",
    "P7_HASH_VERSION",
    "SCOPE_REFUSAL_NOTICE",
    "SCOPE_RULES_VERSION",
    "SEVERITY_RANK",
    "AcceptedRisk",
    "DroppedRisk",
    "ProposedMitigation",
    "RiskDropReason",
    "RiskMatrix",
    "ScopeHit",
    "SeverityComputation",
    "check_fields",
    "compute_severity",
    "find_out_of_scope",
    "parse_impact",
    "parse_likelihood",
    "risk_hash",
    "title_key",
]
