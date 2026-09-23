"""The P7 engine: what the risk nodes record, deterministically.

The *disposing* half of "the LLM proposes; deterministic code disposes" for risk
(architecture I.3-I.5, C.3 nodes 18-19 and the G8 part of node 20). It:

* decides which versions a run analyses and which risk categories each is
  deterministically indicated for;
* assembles the prior-phase context a proposal is made against - P3
  classifications, P5 quality findings and conflicts, P6 compliance mappings,
  gaps and security/privacy findings - from **persisted rows**, so risk analysis
  consumes earlier analysis rather than repeating it (``FR-RSK-001``);
* reuses the evidence the P6 retrieval path already recorded for this project,
  and resolves citations from the database, so validation can check a model's
  citations against exactly the evidence the call was given (``FR-RSK-006``);
* **computes the severity itself**, from the versioned matrix, and records the
  risk with it (``FR-RSK-004``);
* raises G8 for every persisted HIGH risk (``FR-RSK-007``), reading the database
  column and never model output.

The one method that writes a risk, :meth:`RiskEngine.record_risk`, takes an
:class:`~reqpilot.domain.risk.claims.AcceptedRisk` - which has no severity - and
computes the severity inside. **There is no parameter through which a caller, or
a model behind one, could pass a severity.** That is the same shape as P6's
``record_finding``, and for the same reason.

It has no access to a model: the graph nodes call the role and hand the engine
validated value objects.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.compliance.claims import CitationFacts
from reqpilot.domain.enums import (
    BLOCKING_CONFLICT_STATUSES,
    Action,
    AuditEventType,
    Gate,
    MitigationStatus,
    QualityFindingStatus,
    ResourceType,
    ReviewReason,
    RiskCategory,
    RiskScope,
    RiskSeverity,
    RiskStatus,
)
from reqpilot.domain.errors import ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.risk.claims import (
    AcceptedRisk,
    DroppedRisk,
    RiskDropReason,
    title_key,
)
from reqpilot.domain.risk.hashing import risk_hash
from reqpilot.domain.risk.matrix import SeverityComputation, compute_severity
from reqpilot.domain.risk.scope import SCOPE_REFUSAL_NOTICE, SCOPE_RULES_VERSION
from reqpilot.repositories.compliance import (
    ComplianceGapRepository,
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.risk import RiskMitigationRepository, RiskRepository
from reqpilot.rules.risk import RiskRules
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance.engine import AnalysisView, ComplianceEngine
from reqpilot.services.review import ReviewQueue
from reqpilot.services.risk.gates import RISK_SUBJECT


@dataclass(frozen=True)
class PriorSignal:
    """One persisted prior-phase finding that informs risk analysis.

    Context, never authority. A HIGH P6 security finding indicates that security
    risk is worth examining; it does not make any P7 risk HIGH, because only the
    matrix rates a risk and it rates it from that risk's own two ratings.
    """

    kind: str
    key: str
    detail: str
    category: RiskCategory
    signal_id: uuid.UUID | None = None


@dataclass
class RiskContext:
    """Everything one requirement's risk analysis is given (transient)."""

    view: AnalysisView
    indicated: tuple[RiskCategory, ...] = ()
    signals: tuple[PriorSignal, ...] = ()
    evidence_ids: tuple[uuid.UUID, ...] = ()
    citations: dict[str, CitationFacts] = field(default_factory=dict)


class RiskEngine:
    """Risk identification (role #9) and the deterministic severity, recorded."""

    def __init__(
        self,
        session: Session,
        actor: Actor,
        rules: RiskRules,
        compliance: ComplianceEngine,
    ) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._compliance = compliance
        self._risks = RiskRepository(session, actor)
        self._mitigations = RiskMitigationRepository(session, actor)
        self._quality = QualityFindingRepository(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._mappings = ComplianceMappingRepository(session, actor)
        self._gaps = ComplianceGapRepository(session, actor)
        self._findings = SecurityFindingRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def rules(self) -> RiskRules:
        return self._rules

    @property
    def compliance(self) -> ComplianceEngine:
        return self._compliance

    # ------------------------------------------------------------------
    # scope and context (deterministic)
    # ------------------------------------------------------------------
    def read(self, project_id: ProjectId) -> None:
        """Authorise reading the register in this project (ADR-009, layer one)."""
        require(
            self._actor,
            Action.RISK_READ,
            ResourceRef(resource_type=ResourceType.RISK, project_id=project_id),
        )

    def views(self, project_id: ProjectId, version_ids: Sequence[uuid.UUID]) -> list[AnalysisView]:
        """The versions this run analyses: the P6 scope rules, reused unchanged."""
        if version_ids:
            return self._compliance.views(project_id, version_ids)
        return self._compliance.current_views(project_id)[: self._rules.max_versions_per_run]

    def indicated_categories(self, view: AnalysisView) -> tuple[RiskCategory, ...]:
        """The risk categories this version is deterministically examined for."""
        return self._rules.indicated_categories(view.version.statement, view.categories)

    def signals_for(self, project_id: ProjectId, view: AnalysisView) -> tuple[PriorSignal, ...]:
        """The persisted P5/P6 findings for one version (``FR-RSK-001``, brief §12).

        Read from the database, never re-derived: P7 consumes what P5 and P6
        recorded rather than running a second detector over the same text.
        """
        self.read(project_id)
        version_id = view.version.id
        signals: list[PriorSignal] = []
        mapping = self._rules.prior_signals
        for finding in self._quality.for_version(project_id, version_id):
            if finding.status is not QualityFindingStatus.OPEN:
                continue
            signals.append(
                PriorSignal(
                    kind="quality_finding",
                    key=str(finding.rule_id or finding.finding_type),
                    detail=f"open {finding.finding_type} defect on this requirement",
                    category=mapping.get("open_quality_finding", RiskCategory.TECHNICAL),
                    signal_id=finding.id,
                )
            )
        for conflict in self._conflicts.touching(project_id, version_id):
            if conflict.status not in BLOCKING_CONFLICT_STATUSES:
                continue
            signals.append(
                PriorSignal(
                    kind="conflict",
                    key=f"{conflict.conflict_class}/{conflict.kind}",
                    detail="an unresolved conflict with another requirement",
                    category=mapping.get("open_conflict", RiskCategory.BUSINESS),
                    signal_id=conflict.id,
                )
            )
        for item in self._mappings.list_for_project(project_id, version_id=version_id):
            if not item.is_high_impact:
                continue
            signals.append(
                PriorSignal(
                    kind="compliance_mapping",
                    key=item.control_key,
                    detail=(
                        "a high-impact candidate mapping to this control, pending or decided "
                        f"at G2 (status {item.status})"
                    ),
                    category=mapping.get("high_impact_mapping", RiskCategory.COMPLIANCE),
                    signal_id=item.id,
                )
            )
        for derived in self._findings.list_for_project(project_id, version_id=version_id):
            key = f"security_finding_{derived.category}"
            signals.append(
                PriorSignal(
                    kind="security_privacy_finding",
                    key=str(derived.family),
                    detail=(
                        f"a derived {derived.category} requirement whose authoritative P6 risk "
                        f"level is {derived.risk_level} (status {derived.status})"
                    ),
                    category=mapping.get(key, RiskCategory.SECURITY),
                    signal_id=derived.id,
                )
            )
        return tuple(signals)

    def project_signals(self, project_id: ProjectId) -> tuple[PriorSignal, ...]:
        """The project-wide signals the project-level pass is given: gaps, mostly."""
        self.read(project_id)
        category = self._rules.prior_signals.get("compliance_gap", RiskCategory.COMPLIANCE)
        return tuple(
            PriorSignal(
                kind="compliance_gap",
                key=gap.control_key,
                detail=f"an expected control with no covering mapping ({gap.origin})",
                category=category,
                signal_id=gap.id,
            )
            for gap in self._project_gaps(project_id)
        )

    def _project_gaps(self, project_id: ProjectId) -> list[Any]:
        """The latest run's rule-engine gaps. Nothing is re-derived here."""
        run_id = self._gaps.latest_run_id(project_id)
        return [] if run_id is None else list(self._gaps.list_for_run(project_id, run_id))

    # ------------------------------------------------------------------
    # recording (C.3 node 19: risk_compute_severity - the only severity writer)
    # ------------------------------------------------------------------
    def rate(self, accepted: AcceptedRisk) -> SeverityComputation:
        """C.3 node 19: the only source of an authoritative severity (``FR-RSK-004``)."""
        return compute_severity(self._rules.matrix, accepted.likelihood, accepted.impact)

    def record_risk(
        self,
        project_id: ProjectId,
        accepted: AcceptedRisk,
        *,
        view: AnalysisView | None,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> Risk | None:
        """Rate and record one risk, unless the same live risk already exists.

        The caller supplies a *proposal*; the severity is computed here and
        nowhere else, and the status follows from it: a HIGH risk is written
        straight into ``UNDER_REVIEW`` so that it counts against the baseline
        guard from the moment it exists, even before its G8 task is raised
        (``FR-RSK-007``; fail closed).
        """
        if accepted.scope is RiskScope.REQUIREMENT and view is None:
            raise ReqPilotError("a requirement-level risk needs the version it was analysed from")
        if view is not None and view.version.project_id != project_id:
            raise ReqPilotError("a risk's version must be in its project")

        key = title_key(accepted.title)
        existing = (
            self._risks.active_project_risk(project_id, key)
            if accepted.scope is RiskScope.PROJECT
            else self._risks.active_for(project_id, view.version.id, key)  # type: ignore[union-attr]
        )
        if existing is not None:
            return None

        computation = self.rate(accepted)
        evidence_ids = [c.evidence_id for c in accepted.citations]
        content_hash = risk_hash(
            project_id=str(project_id),
            scope=str(accepted.scope),
            requirement_version_id=str(view.version.id) if view else None,
            version_content_hash=view.version.content_hash if view else None,
            category=str(accepted.category),
            title=accepted.title,
            description=accepted.description,
            likelihood=str(computation.likelihood),
            impact=str(computation.impact),
            severity=str(computation.severity),
            matrix_version=computation.matrix_version,
            likelihood_rationale=accepted.likelihood_rationale,
            impact_rationale=accepted.impact_rationale,
            evidence_ids=[str(e) for e in evidence_ids],
        )
        signal = (
            None
            if accepted.review_signal is None
            else max(0.0, min(1.0, float(accepted.review_signal)))
        )
        risk = self._risks.add(
            Risk(
                project_id=project_id,
                scope=accepted.scope,
                requirement_version_id=view.version.id if view else None,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                category=accepted.category,
                title=accepted.title,
                title_key=key,
                description=accepted.description,
                likelihood=computation.likelihood,
                impact=computation.impact,
                severity=computation.severity,
                matrix_version=computation.matrix_version,
                likelihood_rationale=accepted.likelihood_rationale,
                impact_rationale=accepted.impact_rationale,
                citations=[c.snapshot for c in accepted.citations],
                evidence_count=len(set(evidence_ids)),
                detected_by=accepted.detected_by,
                source_signal_kind=accepted.source_signal_kind,
                source_signal_id=accepted.source_signal_id,
                scope_rules_version=SCOPE_RULES_VERSION,
                rules_version=self._rules.ruleset_ref,
                content_hash=content_hash,
                recorded_by=self._actor.actor_id,
                owner_role=self._rules.owner_for(accepted.category),
                review_signal=signal,
                # FR-RSK-007: a HIGH risk is never merely PROPOSED. The database
                # refuses that combination, so this is structural, not a habit.
                status=(
                    RiskStatus.UNDER_REVIEW if computation.requires_gate else RiskStatus.PROPOSED
                ),
            ),
            evidence_ids,
            [
                RiskMitigation(
                    suggestion=m.suggestion,
                    is_ai_generated=m.is_ai_generated,
                    status=MitigationStatus.SUGGESTED,
                )
                for m in accepted.mitigations[: self._rules.max_mitigations_per_risk]
            ],
        )
        common = {
            "actor_kind": self._actor.kind,
            "actor_ref": str(self._actor.actor_id),
            "project_id": project_id,
            "graph_run_id": graph_run_id,
            "agent_run_id": agent_run_id,
        }
        self._audit.append(
            event_type=AuditEventType.RISK_RECORDED,
            subject_type=RISK_SUBJECT,
            subject_id=str(risk.id),
            subject_version=str(view.version.version_no) if view else None,
            payload={
                "scope": str(risk.scope),
                "category": str(risk.category),
                "requirement_version_id": str(risk.requirement_version_id)
                if risk.requirement_version_id
                else None,
                "detected_by": str(risk.detected_by),
                "evidence_count": risk.evidence_count,
                "mitigations": len(accepted.mitigations),
                "owner_role": str(risk.owner_role),
                "scope_rules_version": risk.scope_rules_version,
            },
            **common,  # type: ignore[arg-type]
        )
        self._audit.append(
            event_type=AuditEventType.RISK_SEVERITY_COMPUTED,
            subject_type=RISK_SUBJECT,
            subject_id=str(risk.id),
            payload={
                "likelihood": str(computation.likelihood),
                "impact": str(computation.impact),
                "severity": str(computation.severity),
                "matrix_version": computation.matrix_version,
                "requires_gate": computation.requires_gate,
                "explanation": computation.explanation,
                "rules_version": risk.rules_version,
            },
            **common,  # type: ignore[arg-type]
        )
        return risk

    def record_drop(
        self,
        project_id: ProjectId,
        dropped: DroppedRisk,
        *,
        view: AnalysisView | None,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> None:
        """A dropped proposal is audited - reason codes and counts, never the text.

        A scope-guard refusal (``FR-RSK-011``) is audited under its own event so
        that "the boundary held, and here is when" is answerable from the audit
        log alone.
        """
        out_of_scope = dropped.reason == RiskDropReason.OUT_OF_SCOPE
        self._audit.append(
            event_type=AuditEventType.RISK_DROPPED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version" if view else "project",
            subject_id=view.key if view else str(project_id),
            subject_version=str(view.version.version_no) if view else None,
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "reason": dropped.reason,
                "detail": dropped.detail[:300],
                "category": dropped.category,
                "cited": dropped.cited,
                "rule_ids": list(dropped.rule_ids),
                "scope_rules_version": SCOPE_RULES_VERSION if out_of_scope else None,
                "scope_guard_refusal": out_of_scope,
                "notice": SCOPE_REFUSAL_NOTICE if out_of_scope else None,
            },
        )

    def raise_drop_review(
        self,
        project_id: ProjectId,
        drops: Sequence[DroppedRisk],
        *,
        view: AnalysisView | None,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> None:
        """One review item per call that had proposals dropped, listing the reasons."""
        if not drops:
            return
        out_of_scope = [d for d in drops if d.reason == RiskDropReason.OUT_OF_SCOPE]
        ReviewQueue(self._session, self._actor).raise_item(
            project_id=project_id,
            reason=(ReviewReason.RISK_OUT_OF_SCOPE if out_of_scope else ReviewReason.RISK_DROPPED),
            subject_type="requirement_version" if view else "project",
            subject_id=view.version.id if view else uuid.UUID(str(project_id)),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            detail={
                "dropped": len(drops),
                "reasons": sorted({d.reason for d in drops}),
                "out_of_scope": len(out_of_scope),
                "rule_ids": sorted({r for d in drops for r in d.rule_ids}),
            },
        )

    # ------------------------------------------------------------------
    # gate fan-out (C.3 node 20: persisted values only)
    # ------------------------------------------------------------------
    def raise_pending_gates(
        self, project_id: ProjectId, *, graph_run_id: uuid.UUID
    ) -> list[ApprovalTask]:
        """Raise G8 for every persisted HIGH risk of this run that has no task yet.

        The predicate reads a database column - the authoritative ``severity`` -
        never model output, and never the proposal it came from (I.8's shape,
        applied to P7). A risk that a model rated ``low`` but the matrix rated
        ``high`` is escalated; a risk a model insisted was critical but the
        matrix rated ``low`` is not.
        """
        from reqpilot.services.approval.service import ApprovalService

        approvals = ApprovalService(self._session, self._actor)
        raised: list[ApprovalTask] = []
        risks = self._session.scalars(
            select(Risk).where(
                Risk.project_id == project_id,
                Risk.graph_run_id == graph_run_id,
                Risk.severity == RiskSeverity.HIGH,
                Risk.approval_task_id.is_(None),
            )
        ).all()
        for risk in risks:
            if risk.status is not RiskStatus.UNDER_REVIEW:
                continue
            version = (
                self._compliance._versions.get(project_id, risk.requirement_version_id)
                if risk.requirement_version_id
                else None
            )
            task = approvals.raise_analysis_gate(
                project_id=project_id,
                gate=Gate.G8_HIGH_SEVERITY_RISK,
                subject_type=RISK_SUBJECT,
                subject_id=risk.id,
                subject_version=str(version.version_no) if version else None,
                subject_version_hash=risk.content_hash,
            )
            self._risks.link_task(risk, task.id)
            self._audit.append(
                event_type=AuditEventType.RISK_ESCALATED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type=RISK_SUBJECT,
                subject_id=str(risk.id),
                graph_run_id=graph_run_id,
                payload={
                    "gate": str(Gate.G8_HIGH_SEVERITY_RISK),
                    "task_id": str(task.id),
                    "severity": str(risk.severity),
                    "matrix_version": risk.matrix_version,
                    "blocking": task.blocking,
                    "required_role": str(task.required_role),
                },
            )
            raised.append(task)
        return raised

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def list_risks(self, project_id: ProjectId, **filters: object) -> list[Risk]:
        return self._risks.list_for_project(project_id, **filters)  # type: ignore[arg-type]

    def mitigations_for(self, project_id: ProjectId, risk_id: uuid.UUID) -> list[RiskMitigation]:
        return self._mitigations.list_for_risk(project_id, risk_id)

    def baseline_risk(
        self, view: AnalysisView, category: RiskCategory, signal: PriorSignal
    ) -> AcceptedRisk | None:
        """Deliberately absent: there is no rule-engine fallback risk in P7.

        P6 has one - an indicated security family always yields a finding, so
        that removing the model cannot suppress a G3 gate. Risk is different: a
        risk needs a *rated judgement* with a written rationale, and inventing
        one deterministically would be fabricating the judgement the ratings are
        supposed to record. A run with no model therefore records no risks, and
        that is visible in the run's counters rather than papered over.

        The security and compliance escalations that P6 owns are unaffected: G2
        and G3 still fire from their own persisted values with no model at all.
        """
        _ = (view, category, signal)
        return None
