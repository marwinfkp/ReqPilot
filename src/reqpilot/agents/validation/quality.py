"""Deterministic validation of the P5 semantic proposals (architecture F.2).

Nothing a model proposes about quality or conflicts is persisted until it passes
these checks:

**Quality review.** The key names a statement of the batch; the finding type is
one the rules allow a model to propose; the evidence is words of *that*
statement (a model cannot point at text that is not there); the explanation is
non-empty and claims no authority; an incompleteness finding says what is
missing; per-statement and duplicate limits hold. The recorded severity is the
ruleset's, never the proposal's.

**Conflict adjudication.** Both version ids echo the pair exactly (a substituted
or invented id is refused); for a conflict verdict, evidence A is words of
statement A and evidence B words of statement B; the explanation claims no
authority. A verdict other than a definite or potential conflict leaves no
conflict row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from reqpilot.agents.contracts.quality import (
    ConflictAdjudication,
    ConflictPairView,
    ProposedQualityFinding,
)
from reqpilot.domain.enums import ConflictClass, ConflictKind, ConflictVerdict, QualityFindingType
from reqpilot.rules.quality import QualityRules


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .,;:\"'")


def locate(statement: str, quote: str) -> tuple[int, int] | None:
    """Where ``quote`` occurs in ``statement``, ignoring case, spacing and edge punctuation."""
    parts = _norm(quote).split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(part) for part in parts)
    match = re.search(pattern, statement, flags=re.IGNORECASE)
    return (match.start(), match.end()) if match else None


def _authority(text: str, rules: QualityRules) -> str | None:
    lowered = " ".join(text.lower().split())
    return next((p for p in rules.authority_phrases if p in lowered), None)


@dataclass(frozen=True)
class AcceptedQualityFinding:
    key: str
    finding_type: QualityFindingType
    explanation: str
    evidence: str
    start: int | None
    end: int | None
    missing: str | None
    proposed_severity: str
    review_signal: float


@dataclass(frozen=True)
class QualityReviewDecision:
    accepted: tuple[AcceptedQualityFinding, ...]
    #: ``(index in the output, reason)`` for every refused proposal.
    rejected: tuple[tuple[int, str], ...]


def validate_quality_findings(
    findings: tuple[ProposedQualityFinding, ...] | list[ProposedQualityFinding],
    *,
    statements: Mapping[str, str],
    rules: QualityRules,
) -> QualityReviewDecision:
    accepted: list[AcceptedQualityFinding] = []
    rejected: list[tuple[int, str]] = []
    per_key: dict[str, int] = {}
    seen: set[tuple[str, QualityFindingType, str]] = set()
    for index, proposal in enumerate(findings):
        reason = None
        statement = statements.get(proposal.requirement_key)
        try:
            finding_type = QualityFindingType(proposal.finding_type.strip().lower())
        except ValueError:
            finding_type = None
        explanation = " ".join(proposal.explanation.split())
        evidence = " ".join(proposal.evidence.split())
        located = locate(statement, evidence) if statement and evidence else None
        if statement is None:
            reason = f"unknown requirement key {proposal.requirement_key!r}"
        elif finding_type is None:
            reason = f"unknown finding type {proposal.finding_type!r}"
        elif finding_type not in rules.proposable_types:
            reason = f"{finding_type} findings are decided by the rules, not proposed by a model"
        elif not explanation:
            reason = "the explanation is empty"
        elif (phrase := _authority(explanation, rules)) is not None:
            reason = f"the explanation claims authority ({phrase!r})"
        elif evidence and len(evidence) > rules.max_quote_chars:
            reason = "the evidence quote is too long"
        elif evidence and located is None:
            reason = "the evidence is not words of the requirement it cites"
        elif not evidence and finding_type is not QualityFindingType.INCOMPLETENESS:
            reason = "a finding must quote the words of the requirement it is about"
        elif (
            finding_type is QualityFindingType.INCOMPLETENESS
            and not (proposal.missing or "").strip()
        ):
            reason = "an incompleteness finding must say what is missing"
        elif per_key.get(proposal.requirement_key, 0) >= rules.max_findings_per_requirement:
            reason = "too many findings for one requirement"
        if reason is None:
            assert finding_type is not None and statement is not None
            identity = (proposal.requirement_key, finding_type, _norm(evidence))
            if identity in seen:
                reason = "duplicate of another proposal in this output"
            else:
                seen.add(identity)
        if reason is not None:
            rejected.append((index, reason))
            continue
        assert finding_type is not None and statement is not None
        start, end = located if located else (None, None)
        per_key[proposal.requirement_key] = per_key.get(proposal.requirement_key, 0) + 1
        accepted.append(
            AcceptedQualityFinding(
                key=proposal.requirement_key,
                finding_type=finding_type,
                explanation=explanation,
                evidence=statement[start:end] if start is not None else "",
                start=start,
                end=end,
                missing=" ".join((proposal.missing or "").split()) or None,
                proposed_severity=proposal.proposed_severity,
                review_signal=proposal.review_signal,
            )
        )
    return QualityReviewDecision(tuple(accepted), tuple(rejected))


@dataclass(frozen=True)
class ConflictDecision:
    """The validated adjudication. ``conflict_class`` is ``None`` when no conflict."""

    accepted: bool
    verdict: ConflictVerdict | None
    conflict_class: ConflictClass | None
    kind: ConflictKind
    explanation: str
    evidence_a: str
    evidence_b: str
    conditions: str | None
    proposed_severity: str | None
    review_signal: float | None
    findings: tuple[str, ...]


_CONFLICT_CLASS = {
    ConflictVerdict.DEFINITE_CONFLICT: ConflictClass.DEFINITE,
    ConflictVerdict.POTENTIAL_CONFLICT: ConflictClass.POTENTIAL,
}


def validate_conflict(
    proposal: ConflictAdjudication, *, pair: ConflictPairView, rules: QualityRules
) -> ConflictDecision:
    findings: list[str] = []
    if proposal.requirement_version_id_a.lower() != pair.version_a_id.lower():
        findings.append("requirement_version_id_a is not version A of the pair (substitution)")
    if proposal.requirement_version_id_b.lower() != pair.version_b_id.lower():
        findings.append("requirement_version_id_b is not version B of the pair (substitution)")
    verdict = ConflictVerdict(proposal.verdict)
    explanation = " ".join(proposal.explanation.split())
    if not explanation:
        findings.append("the explanation is empty")
    phrase = _authority(explanation, rules)
    if phrase is not None:
        findings.append(f"the explanation claims authority ({phrase!r})")
    conflict_class = _CONFLICT_CLASS.get(verdict)
    evidence_a, evidence_b = "", ""
    if conflict_class is not None:
        located_a = locate(pair.statement_a, proposal.evidence_a)
        located_b = locate(pair.statement_b, proposal.evidence_b)
        if located_a is None:
            findings.append("evidence_a is not words of requirement A")
        else:
            evidence_a = pair.statement_a[located_a[0] : located_a[1]]
        if located_b is None:
            findings.append("evidence_b is not words of requirement B")
        else:
            evidence_b = pair.statement_b[located_b[0] : located_b[1]]
    return ConflictDecision(
        accepted=not findings,
        verdict=verdict,
        conflict_class=conflict_class,
        kind=ConflictKind(proposal.conflict_kind),
        explanation=explanation,
        evidence_a=evidence_a,
        evidence_b=evidence_b,
        conditions=" ".join((proposal.conditions or "").split()) or None,
        proposed_severity=proposal.proposed_severity,
        review_signal=proposal.review_signal,
        findings=tuple(findings),
    )
