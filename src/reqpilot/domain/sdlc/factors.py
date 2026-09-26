"""The thirteen SDLC decision factors and their scale (``[PS §13]``; architecture L.2).

The factor list is the problem statement's §13 list, in its order, and is not
configurable: a ruleset that omits or adds a factor is refused at load time. The
scale is 1-5 (``[DESIGN] D11``), independent of the 3x3 risk scale.
"""

from __future__ import annotations

from enum import StrEnum


class FactorId(StrEnum):
    """The ``[PS §13]`` factors, in the problem statement's order."""

    REQUIREMENT_STABILITY = "requirement_stability"
    REGULATORY_CRITICALITY = "regulatory_criticality"
    SECURITY_RISK = "security_risk"
    PROJECT_COMPLEXITY = "project_complexity"
    SYSTEM_SIZE = "system_size"
    LEGACY_SYSTEM_DEPENDENCE = "legacy_system_dependence"
    EXPECTED_FREQUENCY_OF_CHANGE = "expected_frequency_of_change"
    NEED_FOR_CONTINUOUS_DELIVERY = "need_for_continuous_delivery"
    STAKEHOLDER_AVAILABILITY = "stakeholder_availability"
    TESTING_AND_DOCUMENTATION_REQUIREMENTS = "testing_and_documentation_requirements"
    BUDGET_AND_SCHEDULE_CONSTRAINTS = "budget_and_schedule_constraints"
    NEED_FOR_FORMAL_VERIFICATION = "need_for_formal_verification"
    CONSEQUENCES_OF_FAILURE = "consequences_of_failure"


#: The §13 order, used wherever a profile is listed.
FACTOR_ORDER: tuple[FactorId, ...] = tuple(FactorId)

#: The factors architecture I.6 derives from the risk register (``FR-SDL-002``,
#: ``FR-RSK-009``). Their score is a deterministic formula; a model proposal for
#: one of them is refused rather than weighed.
RISK_DERIVED_FACTORS: frozenset[FactorId] = frozenset(
    {
        FactorId.SECURITY_RISK,
        FactorId.CONSEQUENCES_OF_FAILURE,
        FactorId.REGULATORY_CRITICALITY,
        FactorId.PROJECT_COMPLEXITY,
    }
)

SCORE_MIN = 1
SCORE_MAX = 5
SCORE_MID = 3


class FactorSource(StrEnum):
    """Where a factor's effective score came from."""

    #: The deterministic derivation from approved facts (this module's rules).
    DERIVED = "derived"
    #: The I.6 risk-register formula (``FR-SDL-002``).
    RISK_AGGREGATE = "risk_aggregate"
    #: A model proposal that passed deterministic validation (role #10).
    MODEL_PROPOSAL = "model_proposal"
    #: A human override with a recorded reason (``FR-SDL-003``).
    HUMAN_OVERRIDE = "human_override"


class ProposalStatus(StrEnum):
    """What deterministic validation made of a factor proposal."""

    NOT_REQUESTED = "not_requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    #: The model returned no proposal for this factor.
    MISSING = "missing"


class EvidenceState(StrEnum):
    """Whether a factor score rests on recorded evidence."""

    #: Rows in the approved scope support the score.
    SUPPORTED = "supported"
    #: The approved scope was examined and holds no signal for this factor; the
    #: absence is the evidence (the baseline examined is cited).
    ABSENT_IN_SCOPE = "absent_in_scope"
    #: The project records nothing this factor could rest on; a neutral 3 is used
    #: and flagged for human review. Never filled from a model's memory.
    NOT_RECORDED = "not_recorded"


def is_valid_score(value: object) -> bool:
    """An integer on the 1-5 scale (``bool`` is not an integer here)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


def norm(score: int) -> float:
    """Architecture L.3: map 1..5 onto -1..+1 (1 -> -1, 3 -> 0, 5 -> +1)."""
    if not is_valid_score(score):
        raise ValueError(f"a factor score is an integer 1..5, not {score!r}")
    return (score - SCORE_MID) / 2


def factor_id(value: str) -> FactorId | None:
    """The factor a string names, or ``None`` for anything that is not one."""
    try:
        return FactorId(str(value).strip())
    except ValueError:
        return None
