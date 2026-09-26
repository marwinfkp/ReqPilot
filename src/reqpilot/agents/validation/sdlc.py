"""Deterministic validation of role #10's output (P9; architecture F.2, L.2, L.5).

**Factor proposals.** For each proposal, in order:

1. The factor is one of the 13 - else it is dropped (counted, attached to no
   factor: there is no factor to record it on).
2. A factor proposed twice keeps neither: an ambiguous proposal is not guessed.
3. A **risk-derived** factor (security risk, consequences of failure, regulatory
   criticality, project complexity) is rejected: those come from the P7 register
   aggregates (architecture I.6) and are changed only by a human override or by
   the register itself.
4. The score is an integer 1-5 - else rejected (never coerced or defaulted).
5. It is within ``max_proposal_deviation`` of the derived score - else rejected:
   the model may refine a derivation, not replace it.
6. It carries a rationale - else rejected.
7. It cites at least one reference, and **every** reference it cites was shown
   in this request - else rejected. A fabricated reference never survives.

A factor the model was asked about and did not propose is ``missing``; nothing
is invented for it. Accepted and rejected alike are recorded beside the factor.

**The explanation.** :func:`explanation_claims` turns the draft into the plain
values the domain's consistency check compares with the persisted ranking. The
draft's ``asserted_*`` fields go there and nowhere else.

Pure functions: no database, no model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from reqpilot.agents.contracts.sdlc import ExplanationDraft, FactorProposalOutput
from reqpilot.domain.sdlc.consistency import CounterArgumentClaim, ExplanationClaims
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    RISK_DERIVED_FACTORS,
    FactorId,
    ProposalStatus,
    factor_id,
    is_valid_score,
)
from reqpilot.domain.sdlc.profile import ProposalDecision

#: The factors role #10 may propose for (the nine that are not risk-derived).
PROPOSABLE_FACTORS: tuple[FactorId, ...] = tuple(
    f for f in FACTOR_ORDER if f not in RISK_DERIVED_FACTORS
)


@dataclass(frozen=True)
class ProposalValidation:
    decisions: dict[FactorId, ProposalDecision]
    #: Proposals for no known factor (dropped; nothing to record them on).
    unknown_factors: int

    @property
    def accepted(self) -> int:
        return sum(1 for d in self.decisions.values() if d.status is ProposalStatus.ACCEPTED)

    @property
    def rejected(self) -> int:
        return sum(1 for d in self.decisions.values() if d.status is ProposalStatus.REJECTED)


def _reject(score: object, rationale: str, refs: tuple[str, ...], reason: str) -> ProposalDecision:
    return ProposalDecision(
        ProposalStatus.REJECTED,
        proposed_score=score if is_valid_score(score) else None,  # type: ignore[arg-type]
        rationale=rationale or None,
        evidence_refs=refs,
        rejection_reason=reason,
    )


def validate_factor_proposals(
    output: FactorProposalOutput,
    derived_scores: Mapping[FactorId, int],
    supplied_refs: Collection[str],
    *,
    max_deviation: int,
) -> ProposalValidation:
    """Every proposable factor's decision: accepted, rejected with a reason, or missing."""
    supplied = set(supplied_refs)
    counts = Counter(factor_id(p.factor) for p in output.proposals)
    decisions: dict[FactorId, ProposalDecision] = {}
    unknown = 0
    for proposal in output.proposals:
        fid = factor_id(proposal.factor)
        if fid is None:
            unknown += 1
            continue
        if fid in decisions:
            continue
        rationale = " ".join(proposal.rationale.split())
        refs = tuple(
            dict.fromkeys(" ".join(r.split()) for r in proposal.evidence_refs if r.strip())
        )
        score = proposal.proposed_score
        if counts[fid] > 1:
            decisions[fid] = _reject(
                score, rationale, refs, "DUPLICATE: the factor was proposed more than once"
            )
        elif fid in RISK_DERIVED_FACTORS:
            decisions[fid] = _reject(
                score,
                rationale,
                refs,
                "RISK_DERIVED: this factor is the P7 register aggregate (I.6); only a human "
                "override changes it",
            )
        elif not is_valid_score(score):
            decisions[fid] = _reject(
                score, rationale, refs, f"INVALID_SCORE: {str(score)[:20]!r} is not an integer 1-5"
            )
        elif abs(int(score) - derived_scores[fid]) > max_deviation:
            decisions[fid] = _reject(
                score,
                rationale,
                refs,
                f"OUT_OF_BOUNDS: {score} is more than {max_deviation} from the derived "
                f"{derived_scores[fid]}",
            )
        elif not rationale:
            decisions[fid] = _reject(
                score, rationale, refs, "NO_RATIONALE: a proposal needs a rationale"
            )
        elif not refs:
            decisions[fid] = _reject(
                score, rationale, refs, "NO_EVIDENCE: a proposal cites no reference"
            )
        elif any(r not in supplied for r in refs):
            decisions[fid] = _reject(
                score,
                rationale,
                refs,
                "UNSUPPLIED_EVIDENCE: a cited reference was not shown in this request",
            )
        else:
            decisions[fid] = ProposalDecision(
                ProposalStatus.ACCEPTED,
                proposed_score=int(score),
                rationale=rationale,
                evidence_refs=refs,
            )
    for fid in PROPOSABLE_FACTORS:
        decisions.setdefault(fid, ProposalDecision(ProposalStatus.MISSING))
    return ProposalValidation(decisions=decisions, unknown_factors=unknown)


def explanation_claims(draft: ExplanationDraft) -> ExplanationClaims:
    """The draft as the domain's plain values, for the consistency check."""
    return ExplanationClaims(
        narrative=draft.narrative,
        counter_arguments=tuple(
            CounterArgumentClaim(
                candidate=" ".join(c.candidate.split()).lower(),
                why_not_selected=c.why_not_selected,
                reversal_condition=c.reversal_condition,
                cited_factors=tuple(c.cited_factors),
            )
            for c in draft.counter_arguments
        ),
        asserted_top_candidate=" ".join(draft.asserted_top_candidate.split()).lower(),
        asserted_scores={
            " ".join(k.split()).lower(): float(v) for k, v in draft.asserted_scores.items()
        },
        cited_factors=tuple(draft.cited_factors),
        cited_evidence_refs=tuple(draft.cited_evidence_refs),
    )
