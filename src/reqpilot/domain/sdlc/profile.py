"""The approved factor profile and the hashes that pin a recommendation (``FR-SDL-003``-``-005``).

Three inputs meet here, in a fixed order of precedence that no model can change:

1. the **derived** score (:func:`~reqpilot.domain.sdlc.derivation.derive_profile`),
   or for the four risk-derived factors the P7 I.6 aggregate;
2. a **validated model proposal** (role #10) - only for the nine factors that are
   not risk-derived, only within ``max_proposal_deviation`` of the derived
   score, and only when it cites evidence it was given. Validation happens
   before this module; a rejected proposal is recorded beside the factor and
   changes nothing;
3. a **human override** (``FR-SDL-003``) - any factor, any valid score, with a
   recorded reason, actor, role and time. It always wins, and the value it
   replaced is kept as ``previous_score``.

The result is the *effective* profile the MCDA scores. Then three hashes:

* ``profile_hash`` - the 13 effective scores;
* ``ranking_hash`` - the ruleset, the profile and every candidate's scores, rank
  and rule effects, and every rule application: what "the ranking" means;
* ``recommendation_hash`` - the ranking plus the explanation as stored: what
  each G6 task binds to. Anything that changes it after G6 is raised makes the
  open tasks stale.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from reqpilot.domain.sdlc.derivation import DerivedFactor
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    FactorId,
    FactorSource,
    ProposalStatus,
    is_valid_score,
)
from reqpilot.domain.sdlc.scoring import ReversalCondition, ScoringResult

RANKING_HASH = "sdlc-ranking-sha256-v1"
RECOMMENDATION_HASH = "sdlc-recommendation-sha256-v1"


@dataclass(frozen=True)
class ProposalDecision:
    """What deterministic validation made of role #10's proposal for one factor."""

    status: ProposalStatus
    proposed_score: int | None = None
    rationale: str | None = None
    evidence_refs: tuple[str, ...] = ()
    rejection_reason: str | None = None


NOT_REQUESTED = ProposalDecision(ProposalStatus.NOT_REQUESTED)


@dataclass(frozen=True)
class OverrideRecord:
    """A human override of one factor (``FR-SDL-003``). Never a model's."""

    factor: FactorId
    new_score: int
    previous_score: int
    reason: str
    actor_id: uuid.UUID
    role: str
    at: dt.datetime

    def __post_init__(self) -> None:
        if not is_valid_score(self.new_score) or not is_valid_score(self.previous_score):
            raise ValueError("an override moves a factor between scores on the 1-5 scale")
        if not " ".join(self.reason.split()):
            raise ValueError("an override needs a reason")


@dataclass(frozen=True)
class EffectiveFactor:
    factor: FactorId
    score: int
    source: FactorSource
    derived: DerivedFactor
    proposal: ProposalDecision
    override: OverrideRecord | None = None


def compose_profile(
    derived: Mapping[FactorId, DerivedFactor],
    proposals: Mapping[FactorId, ProposalDecision],
    overrides: Mapping[FactorId, OverrideRecord],
) -> dict[FactorId, EffectiveFactor]:
    """Derived, then an accepted proposal, then a human override - in that order."""
    out: dict[FactorId, EffectiveFactor] = {}
    for f in FACTOR_ORDER:
        base = derived[f]
        proposal = proposals.get(f, NOT_REQUESTED)
        score, source = base.score, base.source
        if proposal.status is ProposalStatus.ACCEPTED and proposal.proposed_score is not None:
            score, source = proposal.proposed_score, FactorSource.MODEL_PROPOSAL
        override = overrides.get(f)
        if override is not None:
            score, source = override.new_score, FactorSource.HUMAN_OVERRIDE
        out[f] = EffectiveFactor(f, score, source, base, proposal, override)
    return out


def scores_of(profile: Mapping[FactorId, EffectiveFactor]) -> dict[FactorId, int]:
    return {f: profile[f].score for f in FACTOR_ORDER}


def _sha(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def profile_hash(scores: Mapping[FactorId, int]) -> str:
    return _sha({str(f): int(scores[f]) for f in FACTOR_ORDER})


def ranking_payload(
    result: ScoringResult, ruleset_ref: str, weights_version: str
) -> dict[str, Any]:
    return {
        "algorithm": RANKING_HASH,
        "ruleset_ref": ruleset_ref,
        "weights_version": weights_version,
        "profile": {str(f): int(result.profile[f]) for f in FACTOR_ORDER},
        "candidates": [
            {
                "key": c.key,
                "rank": c.rank,
                "raw": c.raw,
                "max_raw": c.max_raw,
                "mcda_score": c.mcda_score,
                "score": c.score,
                "vetoed_by": list(c.vetoed_by),
                "boosted_by": list(c.boosted_by),
                "required_by": list(c.required_by),
            }
            for c in result.candidates
        ],
        "rules": [
            {
                "rule_id": r.rule_id,
                "effect": str(r.effect),
                "affected_candidate": r.affected_candidate,
                "changed_ranking": r.changed_ranking,
            }
            for r in result.rules
        ],
    }


def ranking_hash(result: ScoringResult, ruleset_ref: str, weights_version: str) -> str:
    return _sha(ranking_payload(result, ruleset_ref, weights_version))


def recommendation_hash(
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    ranking_hash_value: str,
    top_candidate: str,
    explanation_status: str,
    narrative: str | None,
    counter_arguments: Sequence[Mapping[str, Any]],
    discrepancies: Sequence[Mapping[str, Any]],
) -> str:
    """What a G6 signature covers: this exact ranking and this exact explanation."""
    return _sha(
        {
            "algorithm": RECOMMENDATION_HASH,
            "run_id": str(run_id),
            "project_id": str(project_id),
            "ranking_hash": ranking_hash_value,
            "top_candidate": top_candidate,
            "explanation_status": explanation_status,
            "narrative": narrative or "",
            "counter_arguments": list(counter_arguments),
            "discrepancies": list(discrepancies),
        }
    )


def reversal_payload(reversals: Sequence[ReversalCondition]) -> list[dict[str, Any]]:
    return [
        {
            "factor": str(r.factor),
            "from_score": r.from_score,
            "to_score": r.to_score,
            "new_top": r.new_top,
            "text": r.render(),
        }
        for r in reversals
    ]
