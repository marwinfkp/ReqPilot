"""``collect_factor_evidence`` (architecture C.5 node 1; ``FR-SDL-001``, ``FR-SDL-002``).

Builds the :class:`~reqpilot.domain.sdlc.facts.FactorFacts` a recommendation is
derived from, out of **approved** persisted rows only. Eligibility is enforced
here, in the queries, never in a prompt:

* **The baseline.** The requirement set in force as of one baseline, as P8's
  :meth:`ScopeService.baseline_scope` projects it - only versions that passed G1
  and entered a baseline. The P8 artefact authority check
  (:meth:`ArtifactService.authority_blockers` for the risk register) must return
  nothing: every version still ``BASELINED``/``SUPERSEDED`` with its member hash
  intact and a completed G1 co-approval, no open governance blocker, and no
  unreviewed high-severity project risk. Any blocker refuses the run (fail
  closed) and names what blocks it.
* **The risk register.** The requirement-level risks of the in-scope versions and
  the project-level risks, in a status that needs no further human decision
  (``PROPOSED`` - never HIGH, by a database check - ``ACCEPTED`` or
  ``MITIGATED``). ``UNDER_REVIEW`` (a pending G8), ``REJECTED`` and ``CLOSED``
  risks are excluded. The four risk-derived factors are the P7 I.6 aggregates
  over exactly these rows (:func:`compute_factor_inputs`) - no second risk engine.
* **Compliance.** Mappings of in-scope versions that validation accepted
  (``CANDIDATE``) or a Compliance Officer approved at G2 (``APPROVED``); a
  pending or rejected interpretation never counts. Gaps are the rule engine's
  (and G2 rejections') gaps of the project's latest compliance run.
* **Elicitation.** The project's stakeholders, whether each completed an
  interview, and the clarifications still open on in-scope versions.

Nothing here reads model output: signals are keyword and classification
matches over approved statements (the ruleset's ``signals`` block), and every
count carries the typed references of the rows behind it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    ArtifactType,
    ClarificationStatus,
    ComplianceMappingStatus,
    ConflictStatus,
    InterviewSessionStatus,
    ResourceType,
    RiskScope,
    RiskStatus,
)
from reqpilot.domain.errors import ArtifactError, SdlcError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.compliance import ComplianceMapping
from reqpilot.domain.models.elicitation import Clarification, InterviewSession, Stakeholder
from reqpilot.domain.models.extraction import AcceptanceCriterion, RequirementClassification
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.sdlc.facts import FactorFacts, RiskAggregate, Signal, ref
from reqpilot.rules.risk import RiskRules
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.services.compliance.report import ComplianceReadService
from reqpilot.services.documents.service import ArtifactService
from reqpilot.services.risk.register import compute_factor_inputs
from reqpilot.services.traceability.scope import RequirementScope, ScopeService

#: Risk statuses that count as the governed register for P9 (see module doc).
ELIGIBLE_RISK_STATUSES: frozenset[RiskStatus] = frozenset(
    {RiskStatus.PROPOSED, RiskStatus.ACCEPTED, RiskStatus.MITIGATED}
)

#: Compliance mappings that count: validated, or approved at G2.
ELIGIBLE_MAPPING_STATUSES: frozenset[ComplianceMappingStatus] = frozenset(
    {ComplianceMappingStatus.CANDIDATE, ComplianceMappingStatus.APPROVED}
)

#: The I.6 aggregate keys (P7 ``risk_rules.yaml``) and the facts field each fills.
RISK_AGGREGATES: dict[str, str] = {
    "security_risk": "security_risk",
    "consequences_of_failure": "consequences_of_failure",
    "regulatory_criticality": "regulatory_risk",
    "project_complexity": "technical_risk",
}


@dataclass(frozen=True)
class CollectedEvidence:
    """The facts, their fingerprint, and a text-free summary for the reader."""

    baseline_id: uuid.UUID
    facts: FactorFacts
    input_fingerprint: str
    summary: dict[str, Any]
    #: The in-scope requirement versions - for provenance facts (masking).
    version_ids: tuple[uuid.UUID, ...]


def facts_fingerprint(facts: FactorFacts) -> str:
    """sha256 over every count, every aggregate and every reference, canonically."""
    payload: dict[str, Any] = {"scope_ref": facts.scope_ref}
    for name, value in sorted(facts.__dict__.items()):
        if isinstance(value, Signal):
            payload[name] = {"count": value.count, "refs": sorted(value.refs)}
        elif isinstance(value, RiskAggregate):
            payload[name] = {
                "value": value.value,
                "refs": sorted(value.refs),
                "counts": dict(sorted(value.counts.items())),
            }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def facts_summary(facts: FactorFacts) -> dict[str, Any]:
    """Counts and aggregate values only - no requirement text."""
    out: dict[str, Any] = {"scope": facts.scope_label}
    for name, value in facts.__dict__.items():
        if isinstance(value, Signal):
            out[name] = value.count
        elif isinstance(value, RiskAggregate):
            out[name] = {"value": value.value, "risks": len(value.refs)}
    return out


class SdlcEvidenceService:
    """Collects the approved facts for one baseline. Reads only."""

    def __init__(
        self, session: Session, actor: Actor, sdlc_rules: SdlcRules, risk_rules: RiskRules
    ) -> None:
        self._session = session
        self._actor = actor
        self._sdlc_rules = sdlc_rules
        self._risk_rules = risk_rules

    def _rows(self, model: type, project_id: ProjectId) -> list:  # type: ignore[type-arg]
        stmt: Any = select(model).where(model.project_id == project_id)  # type: ignore[attr-defined]
        return list(self._session.scalars(stmt))

    def eligible_scope(self, project_id: ProjectId, baseline_id: uuid.UUID) -> RequirementScope:
        """The baseline scope, refused unless every input is approved (fail closed)."""
        require(
            self._actor,
            Action.SDLC_RECORD,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        )
        try:
            scope = ScopeService(self._session, self._actor).baseline_scope(project_id, baseline_id)
        except ArtifactError as exc:
            raise SdlcError(str(exc)) from exc
        blockers = ArtifactService(self._session, self._actor).authority_blockers(
            project_id, scope, ArtifactType.RISK_REGISTER
        )
        if blockers:
            shown = "; ".join(blockers[:5])
            more = f" (+{len(blockers) - 5} more)" if len(blockers) > 5 else ""
            raise SdlcError(
                "an SDLC recommendation is computed only from an approved baseline and a "
                f"governed risk register; blocked by: {shown}{more}"
            )
        return scope

    def collect(self, project_id: ProjectId, baseline_id: uuid.UUID) -> CollectedEvidence:
        scope = self.eligible_scope(project_id, baseline_id)
        assert scope.baseline is not None
        baseline = scope.baseline
        in_scope = {item.version.id: item for item in scope.items}
        version_ids = set(in_scope)

        def signal(ids: list[tuple[str, object]]) -> Signal:
            refs = tuple(dict.fromkeys(ref(kind, i) for kind, i in ids))
            return Signal(len(refs), refs)

        # -- requirement-level signals over approved statements ----------------
        categories: dict[uuid.UUID, list[str]] = {v: [] for v in version_ids}
        latest: dict[uuid.UUID, RequirementClassification] = {}
        for label in self._rows(RequirementClassification, project_id):
            if label.requirement_version_id not in version_ids:
                continue
            held = latest.get(label.requirement_version_id)
            if held is None or label.revision_no > held.revision_no:
                latest[label.requirement_version_id] = label
        for version_id, label in latest.items():
            categories[version_id].append(str(label.category))
        for version_id, item in in_scope.items():
            if item.version.category is not None:
                categories[version_id].append(str(item.version.category))

        ordered = sorted(in_scope.values(), key=lambda i: (i.human_id, i.version.version_no))
        matched: dict[str, list[tuple[str, object]]] = {n: [] for n in self._sdlc_rules.signals}
        for item in ordered:
            for name, rule in self._sdlc_rules.signals.items():
                if rule.matches(item.version.statement, categories[item.version.id]):
                    matched[name].append(("requirement_version", item.version.id))

        requirements = signal([("requirement_version", i.version.id) for i in ordered])
        revised = signal(
            [("requirement_version", i.version.id) for i in ordered if i.version.version_no > 1]
        )
        conflicts = signal(
            [
                ("conflict", c.id)
                for c in sorted(self._rows(Conflict, project_id), key=lambda c: str(c.id))
                if c.status is not ConflictStatus.DISMISSED
                and (c.version_a_id in version_ids or c.version_b_id in version_ids)
            ]
        )

        criteria_rows = [
            c
            for c in self._rows(AcceptanceCriterion, project_id)
            if c.requirement_version_id in version_ids
        ]
        with_criteria = {c.requirement_version_id for c in criteria_rows}
        criteria = Signal(
            len(with_criteria),
            tuple(
                ref("acceptance_criterion", c.id)
                for c in sorted(
                    criteria_rows, key=lambda c: (str(c.requirement_version_id), c.ordinal)
                )
            ),
        )

        # -- elicitation -----------------------------------------------------
        people: list[Stakeholder] = sorted(
            self._rows(Stakeholder, project_id), key=lambda s: str(s.id)
        )
        completed = {
            s.stakeholder_id
            for s in self._rows(InterviewSession, project_id)
            if s.status is InterviewSessionStatus.COMPLETED
        }
        stakeholders = signal([("stakeholder", s.id) for s in people])
        interviewed = signal([("stakeholder", s.id) for s in people if s.id in completed])
        open_clarifications = signal(
            [
                ("clarification", c.id)
                for c in sorted(self._rows(Clarification, project_id), key=lambda c: str(c.id))
                if c.status is ClarificationStatus.OPEN and c.requirement_version_id in version_ids
            ]
        )

        # -- compliance ------------------------------------------------------
        mappings = [
            m
            for m in sorted(self._rows(ComplianceMapping, project_id), key=lambda m: str(m.id))
            if m.requirement_version_id in version_ids and m.status in ELIGIBLE_MAPPING_STATUSES
        ]
        sources: dict[str, list[uuid.UUID]] = {}
        for mapping in mappings:
            for citation in mapping.citations or []:
                title = " ".join(str((citation or {}).get("source_title") or "").split()).lower()
                if title:
                    sources.setdefault(title, []).append(mapping.id)
        normative = Signal(
            len(sources),
            tuple(
                dict.fromkeys(
                    ref("compliance_mapping", mid)
                    for title in sorted(sources)
                    for mid in sources[title]
                )
            ),
        )
        _run_id, gaps = ComplianceReadService(self._session, self._actor).current_gaps(project_id)
        open_gaps = signal(
            [("compliance_gap", g.id) for g in sorted(gaps, key=lambda g: str(g.id))]
        )

        # -- the governed risk register (I.6 via P7) ---------------------------
        risks = [
            r
            for r in sorted(self._rows(Risk, project_id), key=lambda r: str(r.id))
            if r.status in ELIGIBLE_RISK_STATUSES
            and (
                r.scope is RiskScope.PROJECT
                or (r.scope is RiskScope.REQUIREMENT and r.requirement_version_id in version_ids)
            )
        ]
        inputs = {i.key: i for i in compute_factor_inputs(self._risk_rules, risks)}
        aggregates: dict[str, RiskAggregate] = {}
        for key, field_name in RISK_AGGREGATES.items():
            factor_input = inputs.get(key)
            if factor_input is None:  # pragma: no cover - the P7 ruleset defines all four
                raise SdlcError(f"the risk ruleset defines no I.6 aggregate {key!r}")
            aggregates[field_name] = RiskAggregate(
                value=factor_input.value,
                refs=tuple(ref("risk", rid) for rid in factor_input.evidence_risk_ids),
                counts=dict(factor_input.counts),
                description=factor_input.description,
            )

        facts = FactorFacts(
            scope_ref=ref("baseline", baseline.id),
            scope_label=f"baseline {baseline.label}",
            requirements=requirements,
            revised_requirements=revised,
            conflicts=conflicts,
            integration_requirements=signal(matched["integration"]),
            legacy_requirements=signal(matched["legacy"]),
            change_signals=signal(matched["change"]),
            delivery_signals=signal(matched["delivery"]),
            verification_signals=signal(matched["verification"]),
            documentation_signals=signal(matched["documentation"]),
            schedule_signals=signal(matched["schedule"]),
            acceptance_criteria=criteria,
            stakeholders=stakeholders,
            stakeholders_interviewed=interviewed,
            open_clarifications=open_clarifications,
            normative_sources=normative,
            open_compliance_gaps=open_gaps,
            **aggregates,
        )
        summary = facts_summary(facts)
        summary["baseline_id"] = str(baseline.id)
        summary["eligible_risks"] = len(risks)
        summary["eligible_mappings"] = len(mappings)
        return CollectedEvidence(
            baseline_id=baseline.id,
            facts=facts,
            input_fingerprint=facts_fingerprint(facts),
            summary=summary,
            version_ids=tuple(i.version.id for i in ordered),
        )
