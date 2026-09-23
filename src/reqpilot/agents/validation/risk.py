"""Deterministic validation of risk proposals (P7; architecture F.2 stages 2-4, I.1).

Role #9's validation, in the order the architecture states it (E #9: "category in
the 6 ``[PS §4]`` values; both ratings present with rationale; >=1 evidence ref;
scope-guard check"). For each proposed risk, in order:

1. The output names the requirement it was asked about - else every risk in it
   is dropped (substitution). A project-level pass names no version.
2. **Scope guard** (``FR-RSK-011``): the title, description, rationales and
   mitigations must not read as borrower credit risk, a customer risk rating, a
   probability of default or a fraud score. Checked *first* among the content
   checks, so an out-of-scope proposal is refused with its own reason code
   rather than being dropped for some incidental reason - the audit must be able
   to say that the scope boundary held.
3. The category is one of the six approved values - else dropped.
4. Title and description are non-empty.
5. Likelihood is ``L1|L2|L3`` and impact is ``I1|I2|I3`` - else dropped. Never
   guessed, never defaulted (a fabricated rating is a fabricated judgement).
6. Each rating carries a written rationale (``FR-RSK-003``) - else dropped.
7. The risk cites at least one evidence id (``FR-RSK-006``) - else dropped.
8. **Every** cited id was supplied to *this* call and resolves in the database -
   else the whole risk is dropped. A fabricated id, another run's evidence or
   another project's evidence cannot resolve, so a risk carrying one never
   survives. Never retried: a retry invites a better-looking citation (F.2).
9. Duplicates within one call, and proposals over the ruleset's per-call limit,
   are dropped rather than silently merged or truncated.

What is **not** judged here: severity. There is no severity in the proposal, no
severity in an :class:`~reqpilot.domain.risk.claims.AcceptedRisk`, and no
threshold in this module. The two ordinal ratings go to the matrix, which is the
only thing that produces one (``FR-RSK-004``).

Pure functions: no database, no model. The caller resolves citations first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.risk import ProposedRisk, RiskAnalysisOutput
from reqpilot.domain.compliance.claims import CitationFacts
from reqpilot.domain.enums import (
    FindingDetector,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
)
from reqpilot.domain.risk.claims import (
    AcceptedRisk,
    DroppedRisk,
    ProposedMitigation,
    RiskDropReason,
    title_key,
)
from reqpilot.domain.risk.matrix import parse_impact, parse_likelihood
from reqpilot.domain.risk.scope import check_fields


@dataclass(frozen=True)
class RiskDecision:
    """What survived validation, and what did not."""

    accepted: tuple[AcceptedRisk, ...]
    dropped: tuple[DroppedRisk, ...]

    @property
    def out_of_scope(self) -> tuple[DroppedRisk, ...]:
        """The proposals the ``FR-RSK-011`` scope guard refused."""
        return tuple(d for d in self.dropped if d.reason == RiskDropReason.OUT_OF_SCOPE)


def _category(value: str) -> RiskCategory | None:
    token = " ".join(str(value).split()).lower().replace("-", "_").replace(" ", "_")
    try:
        return RiskCategory(token)
    except ValueError:
        return None


def validate_risk_proposals(
    output: RiskAnalysisOutput,
    *,
    version_id: str | None,
    scope: RiskScope,
    supplied: frozenset[str],
    citations: Mapping[str, CitationFacts],
    max_risks: int,
    max_mitigations: int,
    detected_by: FindingDetector = FindingDetector.AGENT,
    known_titles: Sequence[str] = (),
) -> RiskDecision:
    """Validate one risk-identification call. Severity is not computed here."""
    supplied = frozenset(s.lower() for s in supplied)
    resolved_by_id = {k.lower(): v for k, v in citations.items()}

    echoed = (output.requirement_version_id or "").strip().lower()
    expected = (version_id or "").strip().lower()
    if echoed != expected:
        # A substitution: the model answered about something else. Nothing in
        # the output can be trusted to belong to this subject.
        return RiskDecision(
            accepted=(),
            dropped=tuple(
                DroppedRisk(
                    RiskDropReason.WRONG_REQUIREMENT,
                    "the output names a different requirement version",
                    title=risk.title[:120],
                    category=risk.category,
                    cited=len(risk.evidence_ids),
                )
                for risk in output.risks
            ),
        )

    accepted: list[AcceptedRisk] = []
    dropped: list[DroppedRisk] = []
    seen: set[str] = {title_key(t) for t in known_titles}

    for index, proposal in enumerate(output.risks):
        drop = _judge(
            proposal,
            index=index,
            scope=scope,
            supplied=supplied,
            resolved_by_id=resolved_by_id,
            max_risks=max_risks,
            seen=seen,
        )
        if isinstance(drop, DroppedRisk):
            dropped.append(drop)
            continue
        key, resolved, likelihood, impact = drop
        # The category was already parsed (and the proposal dropped if unknown)
        # inside the ordered checks; re-parsing here cannot fail.
        category = _category(proposal.category)
        assert category is not None
        seen.add(key)
        accepted.append(
            AcceptedRisk(
                scope=scope,
                category=category,
                title=" ".join(proposal.title.split()),
                description=" ".join(proposal.description.split()),
                likelihood=likelihood,
                impact=impact,
                likelihood_rationale=" ".join(proposal.likelihood_rationale.split()),
                impact_rationale=" ".join(proposal.impact_rationale.split()),
                citations=resolved,
                mitigations=tuple(
                    # Always a suggestion requiring human validation
                    # (``FR-RSK-005``): the flag is set here, not read from output.
                    ProposedMitigation(
                        suggestion=" ".join(m.suggestion.split()), is_ai_generated=True
                    )
                    for m in proposal.mitigations[:max_mitigations]
                ),
                detected_by=detected_by,
                review_signal=proposal.review_signal,
            )
        )
    return RiskDecision(accepted=tuple(accepted), dropped=tuple(dropped))


def _judge(
    proposal: ProposedRisk,
    *,
    index: int,
    scope: RiskScope,
    supplied: frozenset[str],
    resolved_by_id: Mapping[str, CitationFacts],
    max_risks: int,
    seen: set[str],
) -> DroppedRisk | tuple[str, tuple[CitationFacts, ...], RiskLikelihood, RiskImpact]:
    """One proposal's ordered checks. Returns a drop, or what persistence needs."""
    cited = len(proposal.evidence_ids)

    def drop(reason: str, detail: str, rule_ids: tuple[str, ...] = ()) -> DroppedRisk:
        return DroppedRisk(
            reason=reason,
            detail=detail,
            title=proposal.title[:120],
            category=proposal.category[:40],
            cited=cited,
            rule_ids=rule_ids,
        )

    if index >= max_risks:
        return drop(RiskDropReason.OVER_LIMIT, f"over the per-call limit of {max_risks}")

    # FR-RSK-011 first: the scope boundary is reported as itself, never masked
    # by an incidental failure further down the list.
    hits = check_fields(
        {
            "title": proposal.title,
            "description": proposal.description,
            "likelihood_rationale": proposal.likelihood_rationale,
            "impact_rationale": proposal.impact_rationale,
            "category": proposal.category,
            **{f"mitigation[{n}]": m.suggestion for n, m in enumerate(proposal.mitigations[:6])},
        }
    )
    if hits:
        return drop(
            RiskDropReason.OUT_OF_SCOPE,
            "reads as borrower credit risk, a customer risk rating, a probability of "
            f"default or a fraud score (in {sorted({h.field for h in hits})}); ReqPilot "
            "analyses project and engineering risk only",
            tuple(sorted({h.rule_id for h in hits})),
        )

    category = _category(proposal.category)
    if category is None:
        return drop(
            RiskDropReason.UNKNOWN_CATEGORY,
            "not one of the six approved risk categories",
        )
    if not proposal.title.strip() or not proposal.description.strip():
        return drop(RiskDropReason.EMPTY, "title or description is empty")

    likelihood = parse_likelihood(proposal.likelihood)
    if likelihood is None:
        return drop(
            RiskDropReason.INVALID_LIKELIHOOD,
            "likelihood is missing or is not L1, L2 or L3; a rating is never guessed",
        )
    impact = parse_impact(proposal.impact)
    if impact is None:
        return drop(
            RiskDropReason.INVALID_IMPACT,
            "impact is missing or is not I1, I2 or I3; a rating is never guessed",
        )
    if not proposal.likelihood_rationale.strip() or not proposal.impact_rationale.strip():
        return drop(
            RiskDropReason.MISSING_RATIONALE,
            "FR-RSK-003 requires a written rationale for each rating",
        )

    if not proposal.evidence_ids:
        return drop(RiskDropReason.UNCITED, "FR-RSK-006 requires at least one evidence link")
    ids = [str(e).strip().lower() for e in proposal.evidence_ids]
    unsupplied = [e for e in ids if e not in supplied]
    if unsupplied:
        return drop(
            RiskDropReason.UNSUPPORTED_CITATION,
            f"{len(unsupplied)} citation(s) were not supplied to this call",
        )
    unresolved = [e for e in ids if e not in resolved_by_id]
    if unresolved:
        return drop(
            RiskDropReason.OUT_OF_SCOPE_EVIDENCE,
            f"{len(unresolved)} citation(s) do not resolve to evidence of this project and run",
        )

    key = title_key(proposal.title)
    if key in seen:
        return drop(RiskDropReason.DUPLICATE, "the same risk was already proposed")

    # Deduplicated by evidence id, in the order cited: a citation's resolved
    # facts carry a snapshot dict, so the value objects are not hashable.
    resolved = tuple({e: resolved_by_id[e] for e in ids}.values())
    _ = scope  # the scope is carried through; it is not a validation input
    return key, resolved, likelihood, impact
