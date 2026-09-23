"""The P6 engine: what the compliance and security nodes record, deterministically.

The *disposing* half of "the LLM proposes; deterministic code disposes"
(architecture K.1, I.7, C.3 nodes 12-17 and 20). It:

* decides which versions a run analyses, which checklist applies to the project
  (domain x jurisdiction), which controls and security/privacy families each
  version is indicated for, and what retrieval is asked (the *applicable source
  determination* of K.1);
* retrieves through the P2 allowlisted retrieval service and records every
  supplied chunk as evidence, or records ``RETRIEVAL_EMPTY`` and raises a review
  item instead of letting a model answer from memory (``FR-RAG-005``);
* resolves the run's citations from the database, so validation can check a
  model's citations against exactly the evidence that run was given;
* records validated mappings, drops, rule-engine gaps and derived
  security/privacy findings - the last with the authoritative risk level it
  computes itself (I.7), never one it is handed;
* raises G2 and G3 tasks from **persisted** values only (``gate_fanout``).

It has no access to a model: the graph nodes call the roles and hand the engine
validated value objects.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.compliance.claims import (
    AcceptedMapping,
    AcceptedSecurityProposal,
    CitationFacts,
    ControlRef,
    DroppedClaim,
)
from reqpilot.domain.compliance.gaps import CoveringMapping, compute_gaps, covered_controls
from reqpilot.domain.compliance.hashing import finding_hash, mapping_hash
from reqpilot.domain.compliance.language import LANGUAGE_RULES_VERSION
from reqpilot.domain.compliance.risk import RiskEvaluation, evaluate_risk
from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    Action,
    AuditEventType,
    ComplianceGapOrigin,
    ComplianceMappingStatus,
    EvidenceStatus,
    Gate,
    QualityFindingStatus,
    QualityFindingType,
    ResourceType,
    ReviewReason,
    SecurityControlFamily,
    SecurityFindingStatus,
    SecurityPrivacyCategory,
)
from reqpilot.domain.errors import ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.lifecycle.states import TERMINAL_STATES
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.knowledge import Evidence
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.compliance import (
    ComplianceGapRepository,
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.extraction import ClassificationRepository
from reqpilot.repositories.knowledge import EvidenceRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.retrieval.contracts import (
    QueryClassification,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalResult,
)
from reqpilot.rules.compliance import Checklist, ComplianceRules, SecurityRules
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance.gates import FINDING_SUBJECT, MAPPING_SUBJECT
from reqpilot.services.elicitation import source_facts
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.review import ReviewQueue

#: The retrieval boundary P6 uses: the P2 ``RetrievalService.retrieve`` (or,
#: offline, a test double that returns stored chunks - evidence recording
#: re-checks every chunk against the project's allowlist either way).
Retriever = Callable[[RetrievalQuery], RetrievalResult]

#: Versions in these states are history or refused, and are not analysed.
NOT_ANALYSED: frozenset[RequirementState] = TERMINAL_STATES | {RequirementState.REJECTED}

#: P5 signal rule ids, by the P5 finding type that carries them (FR-QAL-008).
_SIGNAL_TYPES = {
    QualityFindingType.MISSING_SECURITY_CONSIDERATION,
    QualityFindingType.MISSING_PRIVACY_CONSIDERATION,
}


@dataclass(frozen=True)
class AnalysisView:
    """One version as a P6 run reads it (transient, never checkpointed)."""

    version: RequirementVersion
    human_id: str
    categories: tuple[str, ...]
    masked: bool
    synthetic: bool

    @property
    def key(self) -> str:
        return str(self.version.id)


@dataclass
class VersionEvidence:
    """The evidence one version's analysis was given (transient)."""

    retrieval_id: uuid.UUID | None
    outcome: RetrievalOutcome
    evidence_ids: list[uuid.UUID] = field(default_factory=list)
    empty_reason: str | None = None


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


class ComplianceEngine:
    """Compliance mapping (role #7) and security/privacy derivation (role #8), recorded."""

    def __init__(
        self,
        session: Session,
        actor: Actor,
        rules: ComplianceRules,
        security_rules: SecurityRules,
    ) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._security_rules = security_rules
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._labels = ClassificationRepository(session, actor)
        self._mappings = ComplianceMappingRepository(session, actor)
        self._gaps = ComplianceGapRepository(session, actor)
        self._findings = SecurityFindingRepository(session, actor)
        self._quality = QualityFindingRepository(session, actor)
        self._evidence = EvidenceService(session, actor)
        self._evidence_repo = EvidenceRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def rules(self) -> ComplianceRules:
        return self._rules

    @property
    def security_rules(self) -> SecurityRules:
        return self._security_rules

    # ------------------------------------------------------------------
    # scope and applicability (K.1: deterministic)
    # ------------------------------------------------------------------
    def project(self, project_id: ProjectId) -> Project:
        require(
            self._actor,
            Action.COMPLIANCE_READ,
            ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id),
        )
        project = self._session.get(Project, project_id)
        if project is None:
            raise ReqPilotError("project not found")
        return project

    def checklists(self, project_id: ProjectId) -> list[Checklist]:
        """The project's expected-control checklists: its domain x each jurisdiction."""
        project = self.project(project_id)
        return self._rules.checklists_for(project.domain, project.jurisdiction_scope or ())

    def controls(self, project_id: ProjectId) -> list[ControlRef]:
        out: list[ControlRef] = []
        for checklist in self.checklists(project_id):
            for control in checklist.controls:
                out.append(
                    ControlRef(
                        key=control.key,
                        title=control.title,
                        obligation_kind=control.obligation_kind,
                        high_impact=control.high_impact,
                        checklist_ref=self._rules.ruleset_ref,
                        domain=checklist.domain,
                        jurisdiction=checklist.jurisdiction,
                        evidence_tags=control.evidence_tags,
                    )
                )
        return out

    def indicated_controls(self, project_id: ProjectId, view: AnalysisView) -> list[ControlRef]:
        """Controls the version is deterministically indicated for (a hint, not coverage)."""
        keys = {
            control.key
            for checklist in self.checklists(project_id)
            for control in checklist.controls
            if control.indicated_for(view.version.statement, view.categories)
        }
        return [c for c in self.controls(project_id) if c.key in keys]

    def current_views(self, project_id: ProjectId) -> list[AnalysisView]:
        views: list[AnalysisView] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is None or version.state in NOT_ANALYSED:
                continue
            views.append(self._view(project_id, version, requirement.human_id))
        views.sort(key=lambda v: v.human_id)
        return views[: self._rules.max_versions_per_run]

    def views(self, project_id: ProjectId, version_ids: Sequence[uuid.UUID]) -> list[AnalysisView]:
        views = []
        for version_id in dict.fromkeys(version_ids):
            version = self._versions.get(project_id, version_id)
            if version is None:
                raise ReqPilotError("a version in the scope is not in this project")
            if version.state in NOT_ANALYSED:
                raise ReqPilotError(f"a version in the scope is {version.state}; not analysed")
            requirement = self._requirements.get(project_id, version.requirement_id)
            assert requirement is not None  # a foreign key
            views.append(self._view(project_id, version, requirement.human_id))
        return views

    def _view(
        self, project_id: ProjectId, version: RequirementVersion, human_id: str
    ) -> AnalysisView:
        refs = version.source_refs or []
        masked, synthetic = source_facts(self._session, self._actor, project_id, refs)
        if not refs:
            masked, synthetic = False, False
        categories = list(self._labels.current_categories(project_id, version.id))
        if version.category is not None:
            categories.append(str(version.category))
        return AnalysisView(
            version, human_id, tuple(dict.fromkeys(str(c) for c in categories)), masked, synthetic
        )

    def retrieval_query(
        self, project_id: ProjectId, view: AnalysisView, *, as_of: dt.date | None = None
    ) -> RetrievalQuery:
        """K.1 applicable-source determination: classification narrows, never widens.

        The query is the requirement statement. The applicability tags are the
        evidence tags of the controls the version is indicated for (no tags: no
        narrowing beyond the project's allowlisted scope). Jurisdiction, KB pin and
        allowlist come from the project inside the P2 query, never from here.
        """
        tags = frozenset(
            tag for c in self.indicated_controls(project_id, view) for tag in c.evidence_tags
        )
        category = next(iter(view.categories), None)
        from reqpilot.domain.enums import RequirementCategory

        try:
            requirement_category = RequirementCategory(category) if category else None
        except ValueError:
            requirement_category = None
        return RetrievalQuery(
            project_id=project_id,
            text=_norm(view.version.statement)[:2000] or "requirement",
            classification=QueryClassification(
                applicability=tags, requirement_category=requirement_category
            ),
            as_of=as_of,
            top_k=self._rules.retrieval_top_k,
        )

    # ------------------------------------------------------------------
    # evidence (C.3 node 12: compliance_retrieve)
    # ------------------------------------------------------------------
    def retrieve_evidence(
        self,
        project_id: ProjectId,
        view: AnalysisView,
        retriever: Retriever,
        *,
        graph_run_id: uuid.UUID,
        as_of: dt.date | None = None,
    ) -> VersionEvidence:
        """Retrieve for one version and record what was supplied, or the empty outcome."""
        result = retriever(self.retrieval_query(project_id, view, as_of=as_of))
        if result.project_id != project_id:
            raise ReqPilotError("a retrieval result for another project was refused")
        if result.outcome is RetrievalOutcome.EMPTY:
            self._audit.append(
                event_type=AuditEventType.COMPLIANCE_RETRIEVED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type="requirement_version",
                subject_id=view.key,
                subject_version=str(view.version.version_no),
                graph_run_id=graph_run_id,
                payload={
                    "outcome": str(result.outcome),
                    "empty_reason": str(result.empty_reason),
                    "retrieval_id": str(result.retrieval_id),
                    "kb_version": result.kb_version,
                    "evidence": 0,
                },
            )
            ReviewQueue(self._session, self._actor).raise_item(
                project_id=project_id,
                reason=ReviewReason.EVIDENCE_UNAVAILABLE,
                subject_type="requirement_version",
                subject_id=view.version.id,
                graph_run_id=graph_run_id,
                detail={
                    "empty_reason": str(result.empty_reason),
                    "retrieval_id": str(result.retrieval_id),
                    "note": "no allowlisted evidence; no compliance mapping was attempted",
                },
            )
            return VersionEvidence(
                result.retrieval_id, result.outcome, empty_reason=str(result.empty_reason)
            )
        rows = self._evidence.record(result, graph_run_id=graph_run_id)
        self._audit.append(
            event_type=AuditEventType.COMPLIANCE_RETRIEVED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=view.key,
            subject_version=str(view.version.version_no),
            graph_run_id=graph_run_id,
            payload={
                "outcome": str(result.outcome),
                "retrieval_id": str(result.retrieval_id),
                "kb_version": result.kb_version,
                "kb_version_pinned": result.kb_version_pinned,
                "ruleset_version": result.ruleset_version,
                "evidence": len(rows),
                "evidence_ids": [str(r.id) for r in rows],
            },
        )
        return VersionEvidence(result.retrieval_id, result.outcome, [r.id for r in rows])

    def run_evidence_ids(
        self, project_id: ProjectId, graph_run_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        """J.5: the only ids this run's outputs may cite."""
        return self._evidence.evidence_ids_for_run(project_id, graph_run_id)

    def citation_facts(
        self,
        project_id: ProjectId,
        evidence_ids: Iterable[uuid.UUID],
        *,
        allowed: frozenset[uuid.UUID],
    ) -> dict[str, CitationFacts]:
        """Resolved, integrity-checked facts for evidence this run may cite, keyed by id string."""
        wanted = [e for e in dict.fromkeys(evidence_ids) if e in allowed]
        check = self._evidence.check_citations(
            project_id, [str(e) for e in wanted], allowed_evidence_ids=allowed
        )
        rows = self._evidence_repo.resolve(project_id, [c.evidence_id for c in check.resolved])
        out: dict[str, CitationFacts] = {}
        for citation in check.resolved:
            evidence_row, _chunk, item, _source = rows[citation.evidence_id]
            out[str(citation.evidence_id)] = CitationFacts(
                evidence_id=citation.evidence_id,
                jurisdiction=citation.jurisdiction,
                source_type=citation.source_type,
                applicability=frozenset(str(t).lower() for t in (item.applicability or ())),
                snapshot={
                    "evidence_id": str(citation.evidence_id),
                    "retrieval_id": str(citation.retrieval_id),
                    "knowledge_item_id": str(citation.knowledge_item_id),
                    "item_key": citation.item_key,
                    "item_version_no": citation.item_version_no,
                    "clause_ref": citation.clause_ref,
                    "source_title": citation.source_title,
                    "source_type": str(citation.source_type),
                    "binding": SOURCE_TYPE_BINDING[citation.source_type],
                    "issuing_body": citation.issuing_body,
                    "jurisdiction": citation.jurisdiction,
                    "source_version": citation.source_version,
                    "effective_date": (
                        citation.effective_date.isoformat() if citation.effective_date else None
                    ),
                    "curated_on": citation.retrieved_at.isoformat(),
                    "char_start": citation.char_start,
                    "char_end": citation.char_end,
                    "kb_version": citation.kb_version,
                    "quote_sha256": evidence_row.quote_hash,
                },
            )
        return out

    def evidence_text(
        self, project_id: ProjectId, evidence_ids: Sequence[uuid.UUID]
    ) -> list[Evidence]:
        """Evidence rows, in the given order, for the model's fenced evidence block."""
        resolved = self._evidence_repo.resolve(project_id, list(evidence_ids))
        return [resolved[e][0] for e in evidence_ids if e in resolved]

    # ------------------------------------------------------------------
    # mappings (C.3 node 14: compliance_validate)
    # ------------------------------------------------------------------
    def record_mapping(
        self,
        project_id: ProjectId,
        view: AnalysisView,
        accepted: AcceptedMapping,
        *,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> ComplianceMapping | None:
        """Record one validated mapping, unless this version already has an active one."""
        if view.version.project_id != project_id:
            raise ReqPilotError("a mapping's version must be in its project")
        if self._mappings.active_for(project_id, view.version.id, accepted.control.key):
            return None
        evidence_ids = [c.evidence_id for c in accepted.citations]
        if not evidence_ids:
            raise ReqPilotError("a mapping must cite evidence (FR-CMP-001)")
        content_hash = mapping_hash(
            project_id=str(project_id),
            requirement_version_id=view.key,
            version_content_hash=view.version.content_hash,
            control_key=accepted.control.key,
            checklist_ref=accepted.control.checklist_ref,
            relationship=str(accepted.relationship),
            rationale=accepted.rationale,
            candidate_text=accepted.candidate_text,
            implied_obligation=accepted.implied_obligation,
            jurisdiction=accepted.jurisdiction,
            source_type=str(accepted.source_type),
            evidence_ids=[str(e) for e in evidence_ids],
            is_high_impact=accepted.is_high_impact,
        )
        signal = (
            None
            if accepted.review_signal is None
            else max(0.0, min(1.0, float(accepted.review_signal)))
        )
        mapping = self._mappings.add(
            ComplianceMapping(
                project_id=project_id,
                requirement_version_id=view.version.id,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                control_key=accepted.control.key,
                control_title=accepted.control.title,
                obligation_kind=accepted.control.obligation_kind,
                checklist_ref=accepted.control.checklist_ref,
                checklist_domain=accepted.control.domain,
                checklist_jurisdiction=accepted.control.jurisdiction,
                relationship=accepted.relationship,
                rationale=accepted.rationale,
                candidate_text=accepted.candidate_text,
                implied_obligation=accepted.implied_obligation,
                jurisdiction=accepted.jurisdiction,
                source_type=accepted.source_type,
                jurisdictions=sorted({c.jurisdiction for c in accepted.citations}),
                source_types=sorted({str(c.source_type) for c in accepted.citations}),
                citations=[c.snapshot for c in accepted.citations],
                evidence_count=len(set(evidence_ids)),
                is_high_impact=accepted.is_high_impact,
                high_impact_reasons=list(accepted.high_impact_reasons),
                review_signal=signal,
                language_rules_version=LANGUAGE_RULES_VERSION,
                content_hash=content_hash,
                recorded_by=self._actor.actor_id,
                # FR-CMP-004: a high-impact interpretation waits for G2; the
                # database refuses a high-impact CANDIDATE.
                status=(
                    ComplianceMappingStatus.PENDING_REVIEW
                    if accepted.is_high_impact
                    else ComplianceMappingStatus.CANDIDATE
                ),
            ),
            evidence_ids,
        )
        self._audit.append(
            event_type=AuditEventType.COMPLIANCE_MAPPING_ACCEPTED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=view.key,
            subject_version=str(view.version.version_no),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "mapping_id": str(mapping.id),
                "control_key": mapping.control_key,
                "relationship": str(mapping.relationship),
                "jurisdiction": mapping.jurisdiction,
                "source_type": str(mapping.source_type),
                "evidence_ids": [str(e) for e in evidence_ids],
                "is_high_impact": mapping.is_high_impact,
                "high_impact_reasons": list(accepted.high_impact_reasons),
                "status": str(mapping.status),
                "content_hash": content_hash,
            },
        )
        return mapping

    def record_drop(
        self,
        project_id: ProjectId,
        view: AnalysisView,
        dropped: DroppedClaim,
        *,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
        event: AuditEventType = AuditEventType.COMPLIANCE_CLAIM_DROPPED,
    ) -> None:
        """K.3 / J.5: a dropped claim is audited - reason codes and counts, never the text."""
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=view.key,
            subject_version=str(view.version.version_no),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "reason": dropped.reason,
                "detail": dropped.detail[:300],
                "control_key": dropped.control_key,
                "family": dropped.family,
                "cited": dropped.cited,
                "rule_ids": list(dropped.rule_ids),
            },
        )

    def raise_drop_review(
        self,
        project_id: ProjectId,
        view: AnalysisView,
        drops: Sequence[DroppedClaim],
        *,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> None:
        """One review item per model call that had claims dropped, listing the reasons."""
        if not drops:
            return
        ReviewQueue(self._session, self._actor).raise_item(
            project_id=project_id,
            reason=ReviewReason.CLAIM_DROPPED,
            subject_type="requirement_version",
            subject_id=view.version.id,
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            detail={
                "dropped": len(drops),
                "reasons": sorted({d.reason for d in drops}),
                "controls": sorted({d.control_key for d in drops if d.control_key}),
            },
        )

    # ------------------------------------------------------------------
    # gaps (C.3 node 15: compliance_gaps - rules, never the model)
    # ------------------------------------------------------------------
    def covering(self, project_id: ProjectId) -> list[ComplianceMapping]:
        """Mappings on the project's current, analysed versions."""
        current = {v.version.id for v in self.current_views(project_id)}
        return [
            m
            for m in self._mappings.list_for_project(project_id)
            if m.requirement_version_id in current
        ]

    def record_gaps(self, project_id: ProjectId, *, graph_run_id: uuid.UUID) -> list[ComplianceGap]:
        """``expected - covered`` per applicable checklist, recorded for this run (K.2)."""
        mappings = self.covering(project_id)
        recorded: list[ComplianceGap] = []
        for checklist in self.checklists(project_id):
            covered = covered_controls(
                CoveringMapping(m.control_key, m.relationship, m.status)
                for m in mappings
                if m.checklist_jurisdiction == checklist.jurisdiction
            )
            for key in compute_gaps((c.key for c in checklist.controls), covered):
                control = checklist.get(key)
                assert control is not None
                gap = self._gaps.add(
                    ComplianceGap(
                        project_id=project_id,
                        graph_run_id=graph_run_id,
                        control_key=control.key,
                        control_title=control.title,
                        obligation_kind=control.obligation_kind,
                        is_high_impact=control.high_impact,
                        checklist_ref=self._rules.ruleset_ref,
                        checklist_domain=checklist.domain,
                        checklist_jurisdiction=checklist.jurisdiction,
                        origin=ComplianceGapOrigin.RULE_ENGINE,
                        reason=(
                            "expected for "
                            f"{checklist.domain} x {checklist.jurisdiction} by the checklist; no "
                            "validated candidate mapping of a current requirement covers it"
                        ),
                        recorded_by=self._actor.actor_id,
                    )
                )
                self._audit.append(
                    event_type=AuditEventType.COMPLIANCE_GAP_FOUND,
                    actor_kind=self._actor.kind,
                    actor_ref=str(self._actor.actor_id),
                    project_id=project_id,
                    subject_type="compliance_gap",
                    subject_id=str(gap.id),
                    graph_run_id=graph_run_id,
                    payload={
                        "control_key": gap.control_key,
                        "origin": str(gap.origin),
                        "obligation_kind": str(gap.obligation_kind),
                        "checklist_ref": gap.checklist_ref,
                        "jurisdiction": gap.checklist_jurisdiction,
                    },
                )
                recorded.append(gap)
        return recorded

    # ------------------------------------------------------------------
    # security / privacy (C.3 nodes 16-17)
    # ------------------------------------------------------------------
    def indicated_families(
        self, project_id: ProjectId, view: AnalysisView
    ) -> dict[SecurityControlFamily, uuid.UUID | None]:
        """Families the version is deterministically indicated for, with the P5 signal if any.

        Keyword triggers from the catalogue, plus every family an open P5
        security/privacy signal on this version indicates (FR-QAL-008 delegated).
        """
        out: dict[SecurityControlFamily, uuid.UUID | None] = {}
        for spec in self._security_rules.families:
            if spec.indicated_by(view.version.statement):
                out[spec.family] = None
        for finding in self._quality.for_version(project_id, view.version.id):
            if finding.finding_type not in _SIGNAL_TYPES:
                continue
            if finding.status is not QualityFindingStatus.OPEN:
                continue
            rule = (finding.rule_id or "").rsplit(":", 1)[-1]
            for family in self._security_rules.signals.get(rule, ()):
                out[family] = finding.id
        return out

    def evaluate(self, proposal: AcceptedSecurityProposal) -> RiskEvaluation:
        """C.3 node 17 (``security_privacy_evaluate``): the only source of ``risk_level``."""
        rules = self._security_rules
        return evaluate_risk(
            proposed_level=proposal.proposed_risk_level,
            family=proposal.family,
            category=proposal.category,
            high_impact_families=rules.high_impact_families,
            rules_version=rules.ruleset_ref,
            privacy_floor=rules.privacy_floor,
            default_floor=rules.default_floor,
        )

    def record_finding(
        self,
        project_id: ProjectId,
        view: AnalysisView,
        proposal: AcceptedSecurityProposal,
        *,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID | None,
    ) -> SecurityPrivacyFinding | None:
        """Evaluate and record one derived requirement, unless the family is already recorded.

        The caller supplies a *proposal*; the authoritative level is computed
        here and nowhere else. There is no parameter through which a caller - or
        a model behind it - could pass a risk level.
        """
        if view.version.project_id != project_id:
            raise ReqPilotError("a finding's version must be in its project")
        if self._findings.active_for(project_id, view.version.id, str(proposal.family)):
            return None
        evaluation = self.evaluate(proposal)
        evidence_ids = [c.evidence_id for c in proposal.citations]
        content_hash = finding_hash(
            project_id=str(project_id),
            requirement_version_id=view.key,
            version_content_hash=view.version.content_hash,
            category=str(proposal.category),
            family=str(proposal.family),
            derived_requirement=proposal.derived_requirement,
            proposed_risk_level=evaluation.proposed_raw,
            risk_level=str(evaluation.authoritative),
            risk_rules_version=evaluation.rules_version,
            evidence_ids=[str(e) for e in evidence_ids],
        )
        signal = (
            None
            if proposal.review_signal is None
            else max(0.0, min(1.0, float(proposal.review_signal)))
        )
        finding = self._findings.add(
            SecurityPrivacyFinding(
                project_id=project_id,
                requirement_version_id=view.version.id,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                category=proposal.category,
                family=proposal.family,
                derived_requirement=proposal.derived_requirement,
                rationale=proposal.rationale,
                risk_rationale=proposal.risk_rationale,
                evidence_status=(
                    EvidenceStatus.SUPPORTED if evidence_ids else EvidenceStatus.UNAVAILABLE
                ),
                evidence_count=len(set(evidence_ids)),
                citations=[c.snapshot for c in proposal.citations],
                proposed_risk_level=evaluation.proposed_raw,
                normalised_proposed_level=evaluation.normalised_proposal,
                catalogue_floor=evaluation.floor,
                risk_level=evaluation.authoritative,
                risk_rules_version=evaluation.rules_version,
                escalation_reason=evaluation.escalation_reason,
                detected_by=proposal.detected_by,
                source_signal_finding_id=proposal.source_signal_finding_id,
                review_signal=signal,
                content_hash=content_hash,
                recorded_by=self._actor.actor_id,
                # INV-G3: a HIGH authoritative level is always pending G3 (and
                # the database refuses a HIGH finding in PROPOSED).
                status=(
                    SecurityFindingStatus.PENDING_REVIEW
                    if evaluation.requires_g3
                    else SecurityFindingStatus.PROPOSED
                ),
            ),
            evidence_ids,
        )
        derived_event = (
            AuditEventType.SECURITY_REQUIREMENT_DERIVED
            if proposal.category is SecurityPrivacyCategory.SECURITY
            else AuditEventType.PRIVACY_REQUIREMENT_DERIVED
        )
        common = {
            "actor_kind": self._actor.kind,
            "actor_ref": str(self._actor.actor_id),
            "project_id": project_id,
            "graph_run_id": graph_run_id,
            "agent_run_id": agent_run_id,
        }
        self._audit.append(
            event_type=derived_event,
            subject_type="requirement_version",
            subject_id=view.key,
            subject_version=str(view.version.version_no),
            payload={
                "finding_id": str(finding.id),
                "family": str(finding.family),
                "category": str(finding.category),
                "detected_by": str(finding.detected_by),
                "evidence_status": str(finding.evidence_status),
                "source_signal_finding_id": (
                    str(finding.source_signal_finding_id)
                    if finding.source_signal_finding_id
                    else None
                ),
            },
            **common,  # type: ignore[arg-type]
        )
        self._audit.append(
            event_type=AuditEventType.SECURITY_RISK_EVALUATED,
            subject_type=FINDING_SUBJECT,
            subject_id=str(finding.id),
            payload={
                "proposed_risk_level": evaluation.proposed_raw,
                "proposal_recognised": evaluation.proposal_recognised,
                "normalised_proposed_level": str(evaluation.normalised_proposal),
                "catalogue_floor": str(evaluation.floor),
                "risk_level": str(evaluation.authoritative),
                "risk_rules_version": evaluation.rules_version,
                "escalation_reason": evaluation.escalation_reason,
                "requires_g3": evaluation.requires_g3,
            },
            **common,  # type: ignore[arg-type]
        )
        return finding

    # ------------------------------------------------------------------
    # gate fan-out (C.3 node 20: persisted values only)
    # ------------------------------------------------------------------
    def raise_pending_gates(
        self, project_id: ProjectId, *, graph_run_id: uuid.UUID
    ) -> list[ApprovalTask]:
        """Raise G2 for every persisted high-impact mapping and G3 for every persisted
        HIGH finding of this run that has no task yet.

        The predicates read database columns - ``is_high_impact`` and the
        authoritative ``risk_level`` - never model output (I.8).
        """
        from reqpilot.services.approval.service import ApprovalService

        approvals = ApprovalService(self._session, self._actor)
        raised: list[ApprovalTask] = []
        mappings = self._session.scalars(
            select(ComplianceMapping).where(
                ComplianceMapping.project_id == project_id,
                ComplianceMapping.graph_run_id == graph_run_id,
                ComplianceMapping.is_high_impact.is_(True),
                ComplianceMapping.approval_task_id.is_(None),
            )
        ).all()
        for mapping in mappings:
            if mapping.status is not ComplianceMappingStatus.PENDING_REVIEW:
                continue
            version = self._versions.get(project_id, mapping.requirement_version_id)
            task = approvals.raise_analysis_gate(
                project_id=project_id,
                gate=Gate.G2_REGULATORY_INTERPRETATION,
                subject_type=MAPPING_SUBJECT,
                subject_id=mapping.id,
                subject_version=str(version.version_no) if version else None,
                subject_version_hash=mapping.content_hash,
            )
            self._mappings.link_task(mapping, task.id)
            raised.append(task)
        findings = self._session.scalars(
            select(SecurityPrivacyFinding).where(
                SecurityPrivacyFinding.project_id == project_id,
                SecurityPrivacyFinding.graph_run_id == graph_run_id,
                SecurityPrivacyFinding.approval_task_id.is_(None),
            )
        ).all()
        from reqpilot.domain.enums import SecurityRiskLevel

        for finding in findings:
            if finding.risk_level is not SecurityRiskLevel.HIGH:
                continue
            version = self._versions.get(project_id, finding.requirement_version_id)
            task = approvals.raise_analysis_gate(
                project_id=project_id,
                gate=Gate.G3_HIGH_RISK_SECURITY,
                subject_type=FINDING_SUBJECT,
                subject_id=finding.id,
                subject_version=str(version.version_no) if version else None,
                subject_version_hash=finding.content_hash,
            )
            self._findings.link_task(finding, task.id)
            raised.append(task)
        return raised
