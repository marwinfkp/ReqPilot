"""What deterministic risk validation hands to persistence (P7; architecture F.2).

``agents/validation/risk.py`` reads a model's typed proposal and produces these
value objects; :class:`~reqpilot.services.risk.engine.RiskEngine` records them.

The shape enforces the phase's central rule. :class:`AcceptedRisk` carries the
two **ordinal ratings** and their rationales - and no severity. There is no
field on the proposal, no field here, and no parameter on the recording method
through which a severity could travel: it is computed from the matrix at the
moment of recording and nowhere else (``FR-RSK-004``; architecture I.3,
``[DESIGN] D4``).

A :class:`DroppedRisk` records why a proposal did not survive, as a stable
reason code plus rule ids, for the ``RISK_DROPPED`` audit event and the review
queue. A dropped proposal is never stored as a risk.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from reqpilot.domain.compliance.claims import CitationFacts
from reqpilot.domain.enums import (
    FindingDetector,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
)


def title_key(title: str) -> str:
    """The normalised title used to recognise the same risk twice.

    Domain normalisation rather than validation: the engine uses it to find an
    existing live risk, the database indexes it, and validation uses it to spot
    a duplicate within one call. One definition, so those three never disagree.
    """
    return " ".join(title.split()).casefold()[:300]


class RiskDropReason:
    """Stable reason codes for a dropped risk proposal. Safe to store and audit."""

    #: The proposal named a category outside the six approved ones.
    UNKNOWN_CATEGORY = "unknown_category"
    #: Likelihood missing or not one of L1/L2/L3.
    INVALID_LIKELIHOOD = "invalid_likelihood"
    #: Impact missing or not one of I1/I2/I3.
    INVALID_IMPACT = "invalid_impact"
    #: A rating arrived without the written rationale FR-RSK-003 requires.
    MISSING_RATIONALE = "missing_rationale"
    #: The proposal cited no evidence at all (``FR-RSK-006``).
    UNCITED = "uncited"
    #: A citation did not resolve to evidence supplied to this run.
    UNSUPPORTED_CITATION = "unsupported_citation"
    #: A citation resolved to evidence outside this project.
    OUT_OF_SCOPE_EVIDENCE = "out_of_scope_evidence"
    #: The proposal named a requirement version this call was not about.
    WRONG_REQUIREMENT = "wrong_requirement"
    #: The title or description was empty.
    EMPTY = "empty"
    #: The same risk was proposed twice in one call.
    DUPLICATE = "duplicate"
    #: More proposals than the ruleset permits for one requirement.
    OVER_LIMIT = "over_limit"
    #: ``FR-RSK-011``: the text reads as borrower credit risk, a customer risk
    #: rating, a probability of default or a fraud score.
    OUT_OF_SCOPE = "out_of_scope_borrower_risk"

    ALL: frozenset[str] = frozenset(
        {
            UNKNOWN_CATEGORY,
            INVALID_LIKELIHOOD,
            INVALID_IMPACT,
            MISSING_RATIONALE,
            UNCITED,
            UNSUPPORTED_CITATION,
            OUT_OF_SCOPE_EVIDENCE,
            WRONG_REQUIREMENT,
            EMPTY,
            DUPLICATE,
            OVER_LIMIT,
            OUT_OF_SCOPE,
        }
    )


@dataclass(frozen=True)
class ProposedMitigation:
    """A mitigation consideration, always stored as a suggestion (``FR-RSK-005``).

    ``is_ai_generated`` is set by the code that creates it, not by the model:
    a proposal that arrived from a model is AI-generated whatever it says about
    itself, and only a human acceptance changes its status.
    """

    suggestion: str
    is_ai_generated: bool = True


@dataclass(frozen=True)
class AcceptedRisk:
    """A risk proposal that passed every deterministic check (F.2 stages 1-4).

    Note the absent field: there is no severity here. ``likelihood`` and
    ``impact`` are ordinal ratings; the matrix turns them into the authoritative
    severity when the engine records the row.
    """

    scope: RiskScope
    category: RiskCategory
    title: str
    description: str
    likelihood: RiskLikelihood
    impact: RiskImpact
    likelihood_rationale: str
    impact_rationale: str
    citations: tuple[CitationFacts, ...]
    mitigations: tuple[ProposedMitigation, ...]
    detected_by: FindingDetector
    #: Heuristic review-prioritisation signal in [0, 1] - not a probability, and
    #: never consulted by severity, gating or routing (approved Phase 0's
    #: confidence rule; architecture D.3).
    review_signal: float | None = None
    #: The P5 quality finding, P5 conflict or P6 mapping/finding that indicated
    #: this risk, when one did. Provenance, not authority.
    source_signal_kind: str | None = None
    source_signal_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DroppedRisk:
    """A proposal that did not survive validation, and why. Never persisted as a risk."""

    reason: str
    detail: str
    title: str | None = None
    category: str | None = None
    cited: int = 0
    rule_ids: tuple[str, ...] = ()
