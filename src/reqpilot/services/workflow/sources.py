"""What a workflow may be generated from: a passed G6, and the project's approved records.

Two steps, both reads, both failing closed with a :class:`~reqpilot.domain.errors.WorkflowError`:

**1. G6 is verified from the persisted approval records** (architecture M.2, M.3).
The run must be ``SELECTED``; its selection must be the computed first candidate;
and its G6 task group must hold exactly one task per G6 role (Project Manager,
Architect, Security Reviewer, Compliance Officer), every one ``APPROVED``, every one
bound to the run's recommendation hash, and every one carrying an ``APPROVE``
decision recorded in that role. A run status alone is not trusted, and nothing a
client sends can stand in for these rows - there is no resume payload and no
"approved" flag anywhere in the request.

**2. The inputs are the ones P9 used** (``services/sdlc/evidence.py``): the approved
baseline scope, refused if any authority blocker has appeared since (an open
conflict, a pending G2/G3, an unreviewed high-severity risk); the mappings of
in-scope versions that validation accepted or G2 approved; the risks of in-scope
versions and the project-level risks in a status needing no further human
decision - and, in addition, generation is refused while any in-scope HIGH risk
still awaits its G8 review, because a workflow would otherwise omit a HIGH risk the
register holds; the derived security/privacy requirements of in-scope versions
that are not rejected, refused while one still awaits G3; and the latest compliance
run's gaps. The eligibility constants are imported from P9, not restated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    GATE_REQUIRED_ROLES,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    Gate,
    RiskScope,
    RiskSeverity,
    RiskStatus,
    SdlcRunStatus,
    SecurityFindingStatus,
)
from reqpilot.domain.errors import SdlcError, WorkflowError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision
from reqpilot.domain.models.baseline import Baseline
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.models.sdlc import SdlcCandidate, SdlcRun
from reqpilot.domain.policy import Actor
from reqpilot.domain.workflow.inputs import (
    GapInput,
    MappingInput,
    MitigationInput,
    RiskInput,
    SecurityFindingInput,
    WorkflowInputs,
)
from reqpilot.domain.workflow.plan import Finding
from reqpilot.repositories.sdlc import SdlcRunRepository
from reqpilot.rules.risk import RiskRules
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.rules.workflow import candidate_models
from reqpilot.services.compliance.report import ComplianceReadService
from reqpilot.services.sdlc.evidence import (
    ELIGIBLE_MAPPING_STATUSES,
    ELIGIBLE_RISK_STATUSES,
    SdlcEvidenceService,
)
from reqpilot.services.sdlc.service import SdlcService, run_recommendation_hash

#: Derived security requirements that count: validated (PROPOSED) or G3-approved.
ELIGIBLE_FINDING_STATUSES: frozenset[SecurityFindingStatus] = frozenset(
    {SecurityFindingStatus.PROPOSED, SecurityFindingStatus.APPROVED}
)


def refused(code: str, message: str) -> WorkflowError:
    return WorkflowError(message, (Finding(code, "error", message).as_dict(),))


@dataclass(frozen=True)
class G6Verified:
    run: SdlcRun
    candidate: SdlcCandidate
    decision_ids: tuple[uuid.UUID, ...]


class WorkflowSourceLoader:
    """Reads only. Called by the ``generate_workflow`` node as the pipeline actor."""

    def __init__(
        self, session: Session, actor: Actor, sdlc_rules: SdlcRules, risk_rules: RiskRules
    ) -> None:
        self._session = session
        self._actor = actor
        self._sdlc_rules = sdlc_rules
        self._risk_rules = risk_rules

    # ------------------------------------------------------------------
    def verify_g6(self, project_id: ProjectId, run_id: uuid.UUID) -> G6Verified:
        run = SdlcRunRepository(self._session, self._actor).get(project_id, run_id)
        if run is None:
            raise refused("RUN_NOT_FOUND", "SDLC run not found in this project")
        if run.status is not SdlcRunStatus.SELECTED:
            raise refused(
                "G6_NOT_PASSED",
                f"a workflow is generated only from an SDLC selection that passed G6; this run "
                f"is {run.status}",
            )
        if not run.selected_candidate or run.selected_candidate != run.top_candidate:
            raise refused(
                "SELECTION_NOT_COMPUTED_FIRST",
                "the run's selection is not its computed first candidate",
            )
        tasks = SdlcService(self._session, self._actor, self._sdlc_rules).g6_tasks(project_id, run)
        required = GATE_REQUIRED_ROLES[Gate.G6_SDLC_SELECTION]
        by_role = {t.required_role: t for t in tasks if t.gate is Gate.G6_SDLC_SELECTION}
        if set(by_role) != set(required) or len(tasks) != len(required):
            raise refused(
                "G6_TASKS_INCOMPLETE",
                f"G6 needs one task for each of {sorted(str(r) for r in required)}; the run's "
                f"group holds {sorted(str(t.required_role) for t in tasks)}",
            )
        if not run.recommendation_hash or run.recommendation_hash != run_recommendation_hash(run):
            raise refused(
                "G6_BINDING_STALE",
                "the run's recommendation no longer matches the hash G6 was raised on",
            )
        expected_hash = run.recommendation_hash
        decision_ids: list[uuid.UUID] = []
        for role in sorted(required, key=str):
            task = by_role[role]
            if task.status is not ApprovalTaskStatus.APPROVED:
                raise refused("G6_NOT_PASSED", f"the {role} G6 task is {task.status}, not approved")
            if task.subject_version_hash != expected_hash:
                raise refused(
                    "G6_BINDING_STALE",
                    f"the {role} G6 approval is bound to a different recommendation",
                )
            decision = self._session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.project_id == project_id,
                    ApprovalDecision.task_id == task.id,
                    ApprovalDecision.decision == ApprovalDecisionType.APPROVE,
                    ApprovalDecision.role_exercised == role,
                )
            ).first()
            if decision is None:
                raise refused(
                    "G6_DECISION_MISSING",
                    f"the {role} G6 task has no recorded APPROVE decision in that role",
                )
            decision_ids.append(decision.id)
        candidate = self._session.scalars(
            select(SdlcCandidate).where(
                SdlcCandidate.project_id == project_id,
                SdlcCandidate.sdlc_run_id == run.id,
                SdlcCandidate.candidate_key == run.selected_candidate,
            )
        ).first()
        if candidate is None or candidate.rank != 1:
            raise refused(
                "SELECTED_CANDIDATE_MISSING",
                "the selected candidate is not the run's first-ranked candidate row",
            )
        return G6Verified(run=run, candidate=candidate, decision_ids=tuple(decision_ids))

    # ------------------------------------------------------------------
    def load(self, project_id: ProjectId, verified: G6Verified) -> WorkflowInputs:
        run, candidate = verified.run, verified.candidate
        try:
            scope = SdlcEvidenceService(
                self._session, self._actor, self._sdlc_rules, self._risk_rules
            ).eligible_scope(project_id, run.baseline_id)
        except SdlcError as exc:
            raise refused("SOURCES_NOT_GOVERNED", str(exc)) from exc
        in_scope = {item.version.id: item for item in scope.items}

        def label(version_id: uuid.UUID | None) -> str:
            item = in_scope.get(version_id) if version_id else None
            return f"{item.human_id} v{item.version.version_no}" if item else "project-level"

        mappings = [
            m
            for m in self._rows(ComplianceMapping, project_id)
            if m.requirement_version_id in in_scope and m.status in ELIGIBLE_MAPPING_STATUSES
        ]
        risks_all = [
            r
            for r in self._rows(Risk, project_id)
            if r.scope is RiskScope.PROJECT or r.requirement_version_id in in_scope
        ]
        pending = [
            r
            for r in risks_all
            if r.severity is RiskSeverity.HIGH and r.status is RiskStatus.UNDER_REVIEW
        ]
        if pending:
            raise refused(
                "G8_PENDING",
                f"{len(pending)} in-scope HIGH risk(s) still await G8; a workflow is not "
                "generated while the register holds an unreviewed HIGH risk",
            )
        risks = [r for r in risks_all if r.status in ELIGIBLE_RISK_STATUSES]
        mitigations: dict[uuid.UUID, list[RiskMitigation]] = {}
        for mitigation in self._rows(RiskMitigation, project_id):
            mitigations.setdefault(mitigation.risk_id, []).append(mitigation)
        findings_all = [
            f
            for f in self._rows(SecurityPrivacyFinding, project_id)
            if f.requirement_version_id in in_scope
        ]
        if any(f.status is SecurityFindingStatus.PENDING_REVIEW for f in findings_all):
            raise refused(
                "G3_PENDING",
                "an in-scope derived security requirement still awaits G3",
            )
        findings = [f for f in findings_all if f.status in ELIGIBLE_FINDING_STATUSES]
        _gap_run, gaps = ComplianceReadService(self._session, self._actor).current_gaps(project_id)

        definition = next(
            (c for c in self._sdlc_rules.config.candidates if c.key == candidate.candidate_key),
            None,
        )
        if definition is None:
            raise refused(
                "CANDIDATE_UNKNOWN",
                f"the selected candidate {candidate.candidate_key!r} is not in the SDLC ruleset",
            )
        baseline = self._session.get(Baseline, run.baseline_id)
        return WorkflowInputs(
            project_id=str(project_id),
            sdlc_run_id=str(run.id),
            candidate_id=str(candidate.id),
            candidate_key=candidate.candidate_key,
            candidate_label=candidate.label,
            candidate_models=candidate_models(definition.attributes),
            baseline_id=str(run.baseline_id),
            baseline_label=baseline.label if baseline is not None else str(run.baseline_id),
            mappings=tuple(
                MappingInput(
                    id=str(m.id),
                    control_key=m.control_key,
                    control_title=m.control_title,
                    obligation_kind=str(m.obligation_kind),
                    relationship=str(m.relationship),
                    status=str(m.status),
                    requirement_label=label(m.requirement_version_id),
                    evidence_count=int(m.evidence_count),
                    content_hash=m.content_hash,
                )
                for m in sorted(mappings, key=lambda m: str(m.id))
            ),
            risks=tuple(
                RiskInput(
                    id=str(r.id),
                    title=r.title,
                    category=str(r.category),
                    severity=str(r.severity),
                    status=str(r.status),
                    subject_label=label(r.requirement_version_id),
                    content_hash=r.content_hash,
                    mitigations=tuple(
                        MitigationInput(
                            id=str(m.id),
                            suggestion=m.suggestion,
                            is_ai_generated=bool(m.is_ai_generated),
                            status=str(m.status),
                        )
                        for m in sorted(mitigations.get(r.id, []), key=lambda m: str(m.id))
                    ),
                )
                for r in sorted(risks, key=lambda r: str(r.id))
            ),
            findings=tuple(
                SecurityFindingInput(
                    id=str(f.id),
                    family=str(f.family),
                    category=str(f.category),
                    risk_level=str(f.risk_level),
                    status=str(f.status),
                    requirement_label=label(f.requirement_version_id),
                    content_hash=f.content_hash,
                )
                for f in sorted(findings, key=lambda f: str(f.id))
            ),
            gaps=tuple(
                GapInput(
                    id=str(g.id),
                    control_key=g.control_key,
                    control_title=g.control_title,
                    obligation_kind=str(g.obligation_kind),
                )
                for g in sorted(gaps, key=lambda g: str(g.id))
            ),
        )

    def _rows(self, model: type, project_id: ProjectId) -> list:  # type: ignore[type-arg]
        stmt: Any = select(model).where(model.project_id == project_id)  # type: ignore[attr-defined]
        return list(self._session.scalars(stmt))
