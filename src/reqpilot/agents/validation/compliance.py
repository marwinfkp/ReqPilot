"""Deterministic validation of the P6 proposals (architecture F.2 stages 2-4, K.1, J.5, I.7).

The strictest validation in the system. For a **compliance mapping**, in order:

1. The output names the requirement it was asked about - else every mapping is
   dropped (substitution).
2. The control is one of the checklist's - else dropped (unknown control).
3. The mapping cites at least one evidence id - else dropped (uncited).
4. **Every** cited id is evidence supplied to *this* call and resolves in the
   database - else the whole claim is dropped (unsupported citation). A
   fabricated id, another run's evidence or another project's evidence cannot
   resolve, so a claim carrying one never survives (J.5). Never retried: a
   retry invites the model to invent a better-looking citation (F.2).
5. Every cited source is in the project's jurisdiction scope - else dropped.
6. At least one cited item is *about* the control (its applicability tags meet
   the control's evidence tags) - else dropped: a real citation to the wrong
   clause is not support.
7. The model's claimed jurisdiction and source type match a cited source - else
   dropped (provenance mismatch). What is stored comes from the evidence.
8. No prohibited assertion and no authority claim in any generated text
   (``FR-CMP-006``) - else dropped. Never rewritten.
9. High impact (``FR-CMP-004``): a high-impact checklist control, a binding
   source type, or the model's own flag. The model's flag can only add a G2
   gate; it cannot remove one.

For a **derived security/privacy requirement**: the requirement id and the
category must be the ones asked about; the family must be a catalogue family of
that category; cited evidence, if any, must all be supplied and resolvable; the
text must state an obligation and contain no prohibited assertion or authority
claim. The proposed risk level is *not* judged here: whatever it is, it goes to
the deterministic evaluator, which can only raise it (I.7).

Pure functions: no database, no model. The caller resolves citations first.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from reqpilot.agents.contracts.compliance import (
    ComplianceMappingOutput,
    SecurityPrivacyOutput,
)
from reqpilot.domain.compliance.claims import (
    AcceptedMapping,
    AcceptedSecurityProposal,
    CitationFacts,
    ControlRef,
    DroppedClaim,
    DropReason,
)
from reqpilot.domain.compliance.language import LanguageViolation, check_fields
from reqpilot.domain.enums import (
    ComplianceRelationship,
    FindingDetector,
    NormativeSourceType,
    SecurityControlFamily,
    SecurityPrivacyCategory,
)
from reqpilot.rules.compliance import SecurityRules

#: A derived requirement states an obligation (the P1/P3 statement convention).
_OBLIGATION = re.compile(r"\b(?:shall|must)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MappingDecision:
    accepted: tuple[AcceptedMapping, ...]
    dropped: tuple[DroppedClaim, ...]


@dataclass(frozen=True)
class SecurityDecision:
    accepted: tuple[AcceptedSecurityProposal, ...]
    dropped: tuple[DroppedClaim, ...]


def _language_drop(violations: list[LanguageViolation], **fields: object) -> DroppedClaim | None:
    if not violations:
        return None
    authority = any(v.is_authority_claim for v in violations)
    return DroppedClaim(
        reason=DropReason.AUTHORITY_CLAIM if authority else DropReason.PROHIBITED_LANGUAGE,
        detail="generated text asserts "
        + ("a decision only a human records" if authority else "compliance or a legal conclusion")
        + f" (in {sorted({v.field for v in violations})})",
        rule_ids=tuple(sorted({v.rule_id for v in violations})),
        **fields,  # type: ignore[arg-type]
    )


def _source_type(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


def _resolve_citations(
    cited: list[str],
    *,
    supplied: frozenset[str],
    citations: Mapping[str, CitationFacts],
) -> tuple[tuple[CitationFacts, ...], list[str]]:
    """Citations in cited order, and the ids that are not supplied-and-resolved."""
    resolved: list[CitationFacts] = []
    bad: list[str] = []
    for raw in dict.fromkeys(str(c).strip().lower() for c in cited):
        if raw not in supplied or raw not in citations:
            bad.append(raw)
            continue
        resolved.append(citations[raw])
    return tuple(resolved), bad


def validate_compliance_mappings(
    output: ComplianceMappingOutput,
    *,
    version_id: str,
    controls: Mapping[str, ControlRef],
    supplied: frozenset[str],
    citations: Mapping[str, CitationFacts],
    project_jurisdictions: frozenset[str],
    high_impact_source_types: frozenset[NormativeSourceType],
    max_mappings: int,
) -> MappingDecision:
    """Apply stages 1-9 to every proposed mapping. See the module docstring."""
    supplied = frozenset(s.lower() for s in supplied)
    citations = {k.lower(): v for k, v in citations.items()}
    if output.requirement_version_id.strip().lower() != version_id.lower():
        return MappingDecision(
            accepted=(),
            dropped=tuple(
                DroppedClaim(
                    DropReason.WRONG_REQUIREMENT,
                    "the output names a different requirement version",
                    control_key=m.control_key,
                    cited=len(m.evidence_ids),
                )
                for m in output.mappings
            ),
        )
    accepted: list[AcceptedMapping] = []
    dropped: list[DroppedClaim] = []
    seen: set[str] = set()
    for index, proposal in enumerate(output.mappings):
        key = proposal.control_key.strip().upper()
        cited = len(proposal.evidence_ids)
        if index >= max_mappings:
            dropped.append(
                DroppedClaim(
                    DropReason.OVER_LIMIT, "over the per-requirement limit", key, cited=cited
                )
            )
            continue
        control = controls.get(key)
        if control is None:
            dropped.append(
                DroppedClaim(
                    DropReason.UNKNOWN_CONTROL, "not a checklist control", key, cited=cited
                )
            )
            continue
        if key in seen:
            dropped.append(
                DroppedClaim(DropReason.DUPLICATE, "control already mapped", key, cited=cited)
            )
            continue
        if not proposal.evidence_ids:
            dropped.append(
                DroppedClaim(DropReason.UNCITED, "a compliance claim must cite evidence", key)
            )
            continue
        resolved, bad = _resolve_citations(
            proposal.evidence_ids, supplied=supplied, citations=citations
        )
        if bad:
            dropped.append(
                DroppedClaim(
                    DropReason.UNSUPPORTED_CITATION,
                    f"{len(bad)} cited id(s) are not evidence supplied to this run",
                    key,
                    cited=cited,
                )
            )
            continue
        if any(c.jurisdiction not in project_jurisdictions for c in resolved):
            dropped.append(
                DroppedClaim(
                    DropReason.OUT_OF_SCOPE_EVIDENCE,
                    "a cited source is outside the project's jurisdiction scope",
                    key,
                    cited=cited,
                )
            )
            continue
        if not any(c.applicability & control.evidence_tags for c in resolved):
            dropped.append(
                DroppedClaim(
                    DropReason.EVIDENCE_NOT_RELEVANT,
                    "no cited evidence concerns this control",
                    key,
                    cited=cited,
                )
            )
            continue
        jurisdictions = {c.jurisdiction.upper() for c in resolved}
        source_types = {c.source_type.value for c in resolved}
        if (
            proposal.jurisdiction.strip().upper() not in jurisdictions
            or _source_type(proposal.source_type) not in source_types
        ):
            dropped.append(
                DroppedClaim(
                    DropReason.PROVENANCE_MISMATCH,
                    "the claimed jurisdiction or source type is not that of the cited evidence",
                    key,
                    cited=cited,
                )
            )
            continue
        language = _language_drop(
            check_fields(
                {
                    "rationale": proposal.rationale,
                    "candidate_text": proposal.candidate_text,
                    "implied_obligation": proposal.implied_obligation,
                }
            ),
            control_key=key,
            cited=cited,
        )
        if language is not None:
            dropped.append(language)
            continue
        reasons: list[str] = []
        if control.high_impact:
            reasons.append("checklist: high-impact control")
        for source_type in sorted(source_types):
            if NormativeSourceType(source_type) in high_impact_source_types:
                reasons.append(f"cited source type: {source_type}")
        if proposal.is_high_impact_interpretation:
            reasons.append("model flagged a high-impact interpretation")
        seen.add(key)
        accepted.append(
            AcceptedMapping(
                control=control,
                relationship=ComplianceRelationship(proposal.relationship),
                rationale=" ".join(proposal.rationale.split()),
                candidate_text=" ".join(proposal.candidate_text.split())
                if proposal.candidate_text
                else None,
                implied_obligation=" ".join(proposal.implied_obligation.split())
                if proposal.implied_obligation
                else None,
                citations=resolved,
                is_high_impact=bool(reasons),
                high_impact_reasons=tuple(reasons),
                review_signal=proposal.review_signal,
            )
        )
    return MappingDecision(accepted=tuple(accepted), dropped=tuple(dropped))


def validate_security_proposals(
    output: SecurityPrivacyOutput,
    *,
    version_id: str,
    category: SecurityPrivacyCategory,
    rules: SecurityRules,
    supplied: frozenset[str],
    citations: Mapping[str, CitationFacts],
    indicated: Mapping[SecurityControlFamily, object],
) -> SecurityDecision:
    """Validate derived security/privacy requirements. The risk level is not judged here."""
    supplied = frozenset(s.lower() for s in supplied)
    citations = {k.lower(): v for k, v in citations.items()}
    wrong = None
    if output.requirement_version_id.strip().lower() != version_id.lower():
        wrong = DroppedClaim(DropReason.WRONG_REQUIREMENT, "a different requirement version")
    elif output.category != category.value:
        wrong = DroppedClaim(DropReason.WRONG_CATEGORY, f"asked for {category.value} only")
    if wrong is not None:
        return SecurityDecision(
            accepted=(),
            dropped=tuple(
                DroppedClaim(wrong.reason, wrong.detail, family=f.family, cited=len(f.evidence_ids))
                for f in output.findings
            ),
        )
    accepted: list[AcceptedSecurityProposal] = []
    dropped: list[DroppedClaim] = []
    seen: set[SecurityControlFamily] = set()
    for index, proposal in enumerate(output.findings):
        cited = len(proposal.evidence_ids)
        if index >= rules.max_findings_per_call:
            dropped.append(
                DroppedClaim(
                    DropReason.OVER_LIMIT,
                    "over the per-call limit",
                    family=proposal.family,
                    cited=cited,
                )
            )
            continue
        family = rules.family_from(proposal.family)
        if family is None:
            dropped.append(
                DroppedClaim(
                    DropReason.UNKNOWN_FAMILY,
                    "not a catalogue family",
                    family=proposal.family,
                    cited=cited,
                )
            )
            continue
        if rules.spec(family).category is not category:
            dropped.append(
                DroppedClaim(
                    DropReason.WRONG_CATEGORY,
                    f"{family.value} is not {category.value}",
                    family=family.value,
                    cited=cited,
                )
            )
            continue
        if family in seen:
            dropped.append(
                DroppedClaim(
                    DropReason.DUPLICATE, "family already derived", family=family.value, cited=cited
                )
            )
            continue
        resolved, bad = _resolve_citations(
            proposal.evidence_ids, supplied=supplied, citations=citations
        )
        if bad:
            dropped.append(
                DroppedClaim(
                    DropReason.UNSUPPORTED_CITATION,
                    f"{len(bad)} cited id(s) are not evidence supplied to this run",
                    family=family.value,
                    cited=cited,
                )
            )
            continue
        statement = " ".join(proposal.proposed_requirement.split())
        if not _OBLIGATION.search(statement):
            dropped.append(
                DroppedClaim(
                    DropReason.NOT_A_REQUIREMENT,
                    "a derived requirement states an obligation (shall / must)",
                    family=family.value,
                    cited=cited,
                )
            )
            continue
        language = _language_drop(
            check_fields(
                {
                    "proposed_requirement": statement,
                    "rationale": proposal.rationale,
                    "risk_rationale": proposal.risk_rationale,
                }
            ),
            family=family.value,
            cited=cited,
        )
        if language is not None:
            dropped.append(language)
            continue
        seen.add(family)
        signal = indicated.get(family)
        accepted.append(
            AcceptedSecurityProposal(
                family=family,
                category=category,
                derived_requirement=statement,
                rationale=" ".join(proposal.rationale.split()),
                risk_rationale=" ".join(proposal.risk_rationale.split())
                if proposal.risk_rationale
                else None,
                proposed_risk_level=proposal.proposed_risk_level,
                citations=resolved,
                review_signal=proposal.review_signal,
                detected_by=FindingDetector.AGENT,
                source_signal_finding_id=signal if signal is not None else None,  # type: ignore[arg-type]
            )
        )
    return SecurityDecision(accepted=tuple(accepted), dropped=tuple(dropped))


def baseline_proposal(
    family: SecurityControlFamily,
    rules: SecurityRules,
    *,
    signal_finding_id: object = None,
) -> AcceptedSecurityProposal:
    """The catalogue's baseline derived requirement for an indicated family.

    Used when no validated model proposal covers a family the version is
    deterministically indicated for - so omitting a family, or having no model
    at all, never suppresses a finding or its G3 gate. No proposed level: it
    normalises to medium, and the floor applies (I.7).
    """
    spec = rules.spec(family)
    return AcceptedSecurityProposal(
        family=family,
        category=spec.category,
        derived_requirement=spec.baseline_requirement,
        rationale=(
            f"The requirement is deterministically indicated for {spec.title.lower()} "
            f"({rules.ruleset_ref}); this is the catalogue's baseline derived requirement."
        ),
        risk_rationale=None,
        proposed_risk_level=None,
        citations=(),
        review_signal=None,
        detected_by=FindingDetector.RULE,
        source_signal_finding_id=signal_finding_id,  # type: ignore[arg-type]
    )
