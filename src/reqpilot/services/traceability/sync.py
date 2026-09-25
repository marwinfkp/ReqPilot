"""Materialising the typed trace graph from persisted facts (``FR-TRC-001``, ``-004``).

Every link written here is **derived by code from a row that already exists**:
a requirement version's ``source_refs`` that resolve to an utterance, chunk or
document of the project; a classification revision; a quality finding and the
clarification raised for it; a recorded conflict; a compliance mapping and its
evidence links; a security/privacy finding; a risk, its evidence and its
mitigations; the risk-analysis agent run that examined the version; an
acceptance criterion; an APPROVE decision; a baseline membership; a supersession.
Nothing is inferred from text and nothing is asserted by a model - an edge
exists only when the fact it records exists (architecture J.1: traceability link
creation is enforced in code).

The sync is **idempotent and append-only**: it inserts the edges that are
missing and never updates or deletes one. Because edges bind to exact
requirement versions, a successor gets its own edges and the predecessor's stay
(architecture N.4), so the graph of any historical version is reconstructable.

Artefact edges (``RENDERED_IN``, ``CONTAINS``, ``CITES``) are written by the
artefact service at generation time, not here.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AgentRole,
    AgentRunStatus,
    ApprovalDecisionType,
    AuditEventType,
    ResourceType,
    SpeakerKind,
)
from reqpilot.domain.errors import TraceabilityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.baseline import BaselineMember
from reqpilot.domain.models.compliance import (
    ComplianceMapping,
    ComplianceMappingEvidence,
    SecurityPrivacyFinding,
    SecurityPrivacyFindingEvidence,
)
from reqpilot.domain.models.elicitation import Clarification, QualityFinding, Utterance
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    RequirementClassification,
    SourceChunk,
    SourceDocument,
)
from reqpilot.domain.models.knowledge import Evidence, KnowledgeItem
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.risk import Risk, RiskEvidence, RiskMitigation
from reqpilot.domain.models.runs import AgentRun, GraphRun
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.refs import EvidenceKind
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType, is_allowed
from reqpilot.repositories.traceability import EdgeKey, TraceLinkRepository
from reqpilot.services.audit import AuditService

N = TraceNodeType
L = TraceLinkType

#: The subject types whose APPROVE decisions become ``APPROVED_BY`` edges.
_DECISION_SUBJECTS: dict[str, TraceNodeType] = {
    "requirement_version": N.REQUIREMENT_VERSION,
    "compliance_mapping": N.COMPLIANCE_MAPPING,
    "security_privacy_finding": N.SECURITY_PRIVACY_FINDING,
    "risk": N.RISK,
    "conflict": N.CONFLICT,
}


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Edge:
    from_type: TraceNodeType
    from_id: str
    link_type: TraceLinkType
    to_type: TraceNodeType
    to_id: str
    anchor: uuid.UUID | None
    origin: str

    @property
    def key(self) -> EdgeKey:
        return (
            str(self.from_type),
            self.from_id,
            str(self.link_type),
            str(self.to_type),
            self.to_id,
        )


@dataclass
class SyncResult:
    project_id: ProjectId
    created: int = 0
    already_present: int = 0
    by_link_type: Counter[str] = field(default_factory=Counter)
    unresolved_source_refs: int = 0


class TraceGraphSync:
    """Derives the typed trace graph from the project's persisted rows."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._links = TraceLinkRepository(session, actor)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------
    def sync(self, project_id: ProjectId, *, audit: bool = True) -> SyncResult:
        require(
            self._actor,
            Action.TRACE_SYNC,
            ResourceRef(resource_type=ResourceType.TRACEABILITY_LINK, project_id=project_id),
        )
        edges, unresolved = self.derive(project_id)
        existing = self._links.existing_keys(project_id)
        result = SyncResult(project_id=project_id, unresolved_source_refs=unresolved)
        new_rows: list[TraceabilityLink] = []
        seen: set[EdgeKey] = set()
        for edge in edges:
            if edge.key in seen:
                continue
            seen.add(edge.key)
            if edge.key in existing:
                result.already_present += 1
                continue
            new_rows.append(self._row(project_id, edge))
            result.by_link_type[str(edge.link_type)] += 1
        result.created = self._links.add_many(project_id, new_rows) if new_rows else 0
        if audit and result.created:
            self._audit.append(
                event_type=AuditEventType.TRACE_LINKS_SYNCED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type="project",
                subject_id=str(project_id),
                payload={
                    "created": result.created,
                    "already_present": result.already_present,
                    "by_link_type": dict(sorted(result.by_link_type.items())),
                    "unresolved_source_refs": unresolved,
                },
            )
        return result

    def record(self, project_id: ProjectId, edges: Iterable[Edge]) -> int:
        """Insert explicitly given edges (the artefact service's own), validated."""
        existing = self._links.existing_keys(project_id)
        rows = []
        for edge in edges:
            if edge.key in existing:
                continue
            existing.add(edge.key)
            rows.append(self._row(project_id, edge))
        return self._links.add_many(project_id, rows) if rows else 0

    def _row(self, project_id: ProjectId, edge: Edge) -> TraceabilityLink:
        if not is_allowed(str(edge.from_type), str(edge.link_type), str(edge.to_type)):
            raise TraceabilityError(
                f"{edge.from_type} -{edge.link_type}-> {edge.to_type} is outside the closed "
                "trace-link allowlist (architecture N.1)"
            )
        return TraceabilityLink(
            project_id=project_id,
            from_type=str(edge.from_type),
            from_id=edge.from_id,
            link_type=str(edge.link_type),
            to_type=str(edge.to_type),
            to_id=edge.to_id,
            anchor_version_id=edge.anchor,
            origin=edge.origin,
            created_by=self._actor.actor_id,
        )

    # ------------------------------------------------------------------
    # derivation - reads only, every query scoped to the project
    # ------------------------------------------------------------------
    def _rows(self, model: type, project_id: ProjectId) -> list:  # type: ignore[type-arg]
        stmt: Any = select(model).where(model.project_id == project_id)  # type: ignore[attr-defined]
        return list(self._session.scalars(stmt))

    def derive(self, project_id: ProjectId) -> tuple[list[Edge], int]:
        """Every edge the persisted facts support, plus the count of unresolvable refs."""
        edges: list[Edge] = []
        versions: list[RequirementVersion] = self._rows(RequirementVersion, project_id)
        version_ids = {v.id for v in versions}
        unresolved = self._source_edges(project_id, versions, edges)

        def add(ft, fid, lt, tt, tid, anchor, origin):  # type: ignore[no-untyped-def]
            edges.append(Edge(ft, str(fid), lt, tt, str(tid), anchor, origin))

        for version in versions:
            if version.superseded_by_id is not None and version.superseded_by_id in version_ids:
                add(
                    N.REQUIREMENT_VERSION,
                    version.superseded_by_id,
                    L.SUPERSEDES,
                    N.REQUIREMENT_VERSION,
                    version.id,
                    version.superseded_by_id,
                    "requirement_version.superseded_by_id",
                )

        for label in self._rows(RequirementClassification, project_id):
            add(
                N.REQUIREMENT_VERSION,
                label.requirement_version_id,
                L.CLASSIFIED_AS,
                N.CLASSIFICATION,
                label.id,
                label.requirement_version_id,
                "requirement_classification",
            )
        for criterion in self._rows(AcceptanceCriterion, project_id):
            add(
                N.REQUIREMENT_VERSION,
                criterion.requirement_version_id,
                L.SATISFIED_BY,
                N.ACCEPTANCE_CRITERION,
                criterion.id,
                criterion.requirement_version_id,
                "acceptance_criterion",
            )

        findings: list[QualityFinding] = self._rows(QualityFinding, project_id)
        finding_version = {f.id: f.requirement_version_id for f in findings}
        for finding in findings:
            add(
                N.REQUIREMENT_VERSION,
                finding.requirement_version_id,
                L.HAS_FINDING,
                N.QUALITY_FINDING,
                finding.id,
                finding.requirement_version_id,
                "quality_finding",
            )
        for clarification in self._rows(Clarification, project_id):
            anchor = finding_version.get(clarification.quality_finding_id)
            if anchor is not None:
                add(
                    N.QUALITY_FINDING,
                    clarification.quality_finding_id,
                    L.RAISED,
                    N.CLARIFICATION,
                    clarification.id,
                    anchor,
                    "clarification.quality_finding_id",
                )
            if clarification.answer_utterance_id is not None:
                add(
                    N.CLARIFICATION,
                    clarification.id,
                    L.ANSWERED_BY,
                    N.UTTERANCE,
                    clarification.answer_utterance_id,
                    clarification.requirement_version_id,
                    "clarification.answer_utterance_id",
                )

        for conflict in self._rows(Conflict, project_id):
            for side in (conflict.version_a_id, conflict.version_b_id):
                add(
                    N.REQUIREMENT_VERSION,
                    side,
                    L.HAS_CONFLICT,
                    N.CONFLICT,
                    conflict.id,
                    side,
                    "conflict",
                )
            add(
                N.REQUIREMENT_VERSION,
                conflict.version_a_id,
                L.CONFLICTS_WITH,
                N.REQUIREMENT_VERSION,
                conflict.version_b_id,
                conflict.version_a_id,
                "conflict",
            )

        mappings: list[ComplianceMapping] = self._rows(ComplianceMapping, project_id)
        mapping_version = {m.id: m.requirement_version_id for m in mappings}
        for mapping in mappings:
            add(
                N.REQUIREMENT_VERSION,
                mapping.requirement_version_id,
                L.HAS_MAPPING,
                N.COMPLIANCE_MAPPING,
                mapping.id,
                mapping.requirement_version_id,
                "compliance_mapping",
            )
            add(
                N.COMPLIANCE_MAPPING,
                mapping.id,
                L.MAPPED_TO,
                N.CHECKLIST_CONTROL,
                mapping.control_key,
                mapping.requirement_version_id,
                "compliance_mapping.control_key",
            )
        cited_evidence: set[uuid.UUID] = set()
        for link in self._rows(ComplianceMappingEvidence, project_id):
            cited_evidence.add(link.evidence_id)
            add(
                N.COMPLIANCE_MAPPING,
                link.mapping_id,
                L.EVIDENCED_BY,
                N.EVIDENCE,
                link.evidence_id,
                mapping_version.get(link.mapping_id),
                "compliance_mapping_evidence",
            )

        security: list[SecurityPrivacyFinding] = self._rows(SecurityPrivacyFinding, project_id)
        finding_anchor = {f.id: f.requirement_version_id for f in security}
        for sp in security:
            add(
                N.REQUIREMENT_VERSION,
                sp.requirement_version_id,
                L.HAS_SECURITY_FINDING,
                N.SECURITY_PRIVACY_FINDING,
                sp.id,
                sp.requirement_version_id,
                "security_privacy_finding",
            )
        for link in self._rows(SecurityPrivacyFindingEvidence, project_id):
            cited_evidence.add(link.evidence_id)
            add(
                N.SECURITY_PRIVACY_FINDING,
                link.finding_id,
                L.EVIDENCED_BY,
                N.EVIDENCE,
                link.evidence_id,
                finding_anchor.get(link.finding_id),
                "security_privacy_finding_evidence",
            )

        risks: list[Risk] = self._rows(Risk, project_id)
        risk_anchor = {r.id: r.requirement_version_id for r in risks}
        for risk in risks:
            if risk.requirement_version_id is not None:
                add(
                    N.REQUIREMENT_VERSION,
                    risk.requirement_version_id,
                    L.HAS_RISK,
                    N.RISK,
                    risk.id,
                    risk.requirement_version_id,
                    "risk.requirement_version_id",
                )
        for link in self._rows(RiskEvidence, project_id):
            cited_evidence.add(link.evidence_id)
            add(
                N.RISK,
                link.risk_id,
                L.EVIDENCED_BY,
                N.EVIDENCE,
                link.evidence_id,
                risk_anchor.get(link.risk_id),
                "risk_evidence",
            )
        for mitigation in self._rows(RiskMitigation, project_id):
            add(
                N.RISK,
                mitigation.risk_id,
                L.MITIGATED_BY,
                N.RISK_MITIGATION,
                mitigation.id,
                risk_anchor.get(mitigation.risk_id),
                "risk_mitigation",
            )

        self._risk_assessment_edges(project_id, version_ids, edges)
        self._decision_edges(
            project_id, version_ids, mapping_version, finding_anchor, risk_anchor, edges
        )

        for member in self._rows(BaselineMember, project_id):
            add(
                N.REQUIREMENT_VERSION,
                member.requirement_version_id,
                L.MEMBER_OF,
                N.BASELINE,
                member.baseline_id,
                member.requirement_version_id,
                "baseline_member",
            )

        self._evidence_edges(project_id, cited_evidence, edges)
        return edges, unresolved

    def _source_edges(
        self, project_id: ProjectId, versions: list[RequirementVersion], edges: list[Edge]
    ) -> int:
        """``SOURCES`` edges from each version's ``source_refs`` that resolve in the project."""
        utterances = {u.id: u for u in self._rows(Utterance, project_id)}
        chunks = {c.id for c in self._rows(SourceChunk, project_id)}
        documents = {d.id for d in self._rows(SourceDocument, project_id)}
        unresolved = 0
        for version in versions:
            for ref in version.source_refs or []:
                if not isinstance(ref, dict):
                    unresolved += 1
                    continue
                ref_id = _uuid(ref.get("ref"))
                doc_id = _uuid(ref.get("document"))
                if ref.get("kind") == "utterance" and ref_id in utterances:
                    utterance = utterances[ref_id]  # type: ignore[index]
                    edges.append(
                        Edge(
                            N.UTTERANCE,
                            str(utterance.id),
                            L.SOURCES,
                            N.REQUIREMENT_VERSION,
                            str(version.id),
                            version.id,
                            "requirement_version.source_refs",
                        )
                    )
                    if (
                        utterance.speaker_kind is SpeakerKind.STAKEHOLDER
                        and utterance.speaker_ref is not None
                    ):
                        edges.append(
                            Edge(
                                N.STAKEHOLDER,
                                str(utterance.speaker_ref),
                                L.STATED,
                                N.UTTERANCE,
                                str(utterance.id),
                                version.id,
                                "utterance.speaker_ref",
                            )
                        )
                elif ref_id is not None and ref_id in chunks:
                    edges.append(
                        Edge(
                            N.SOURCE_CHUNK,
                            str(ref_id),
                            L.SOURCES,
                            N.REQUIREMENT_VERSION,
                            str(version.id),
                            version.id,
                            "requirement_version.source_refs",
                        )
                    )
                elif doc_id is not None and doc_id in documents:
                    edges.append(
                        Edge(
                            N.SOURCE_DOCUMENT,
                            str(doc_id),
                            L.SOURCES,
                            N.REQUIREMENT_VERSION,
                            str(version.id),
                            version.id,
                            "requirement_version.source_refs",
                        )
                    )
                else:
                    # A reference that resolves to nothing in this project is an
                    # unsourced statement (FR-TRC-003) - reported, never linked.
                    unresolved += 1
        return unresolved

    def _risk_assessment_edges(
        self, project_id: ProjectId, version_ids: set[uuid.UUID], edges: list[Edge]
    ) -> None:
        """N.3's recorded risk-analysis outcome: the run that examined this exact version."""
        stmt = (
            select(AgentRun)
            .join(GraphRun, GraphRun.id == AgentRun.graph_run_id)
            .where(
                GraphRun.project_id == project_id,
                AgentRun.role == AgentRole.RISK_ANALYSIS,
                AgentRun.node == "risk_identify",
                AgentRun.status == AgentRunStatus.OK,
            )
        )
        for run in self._session.scalars(stmt):
            version_id = _uuid((run.input_refs or {}).get("requirement_version_id"))
            if version_id is not None and version_id in version_ids:
                edges.append(
                    Edge(
                        N.REQUIREMENT_VERSION,
                        str(version_id),
                        L.RISK_ASSESSED_BY,
                        N.AGENT_RUN,
                        str(run.id),
                        version_id,
                        "agent_run.input_refs",
                    )
                )

    def _decision_edges(
        self,
        project_id: ProjectId,
        version_ids: set[uuid.UUID],
        mapping_version: dict[uuid.UUID, uuid.UUID],
        finding_anchor: dict[uuid.UUID, uuid.UUID],
        risk_anchor: dict[uuid.UUID, uuid.UUID | None],
        edges: list[Edge],
    ) -> None:
        tasks = {t.id: t for t in self._rows(ApprovalTask, project_id)}
        conflicts = {c.id: c.version_a_id for c in self._rows(Conflict, project_id)}
        for decision in self._rows(ApprovalDecision, project_id):
            if decision.decision is not ApprovalDecisionType.APPROVE:
                continue
            task = tasks.get(decision.task_id)
            if task is None:
                continue
            node = _DECISION_SUBJECTS.get(task.subject_type)
            if node is None:
                continue
            if node is N.REQUIREMENT_VERSION:
                if task.subject_id not in version_ids:
                    continue
                anchor: uuid.UUID | None = task.subject_id
            elif node is N.COMPLIANCE_MAPPING:
                anchor = mapping_version.get(task.subject_id)
            elif node is N.SECURITY_PRIVACY_FINDING:
                anchor = finding_anchor.get(task.subject_id)
            elif node is N.RISK:
                anchor = risk_anchor.get(task.subject_id)
            else:
                anchor = conflicts.get(task.subject_id)
            edges.append(
                Edge(
                    node,
                    str(task.subject_id),
                    L.APPROVED_BY,
                    N.APPROVAL_DECISION,
                    str(decision.id),
                    anchor,
                    f"approval_decision:{task.gate.value}",
                )
            )

    def _evidence_edges(
        self, project_id: ProjectId, evidence_ids: set[uuid.UUID], edges: list[Edge]
    ) -> None:
        """Evidence -> knowledge item -> normative source (the control/normative end)."""
        if not evidence_ids:
            return
        stmt = select(Evidence).where(
            Evidence.project_id == project_id, Evidence.id.in_(sorted(evidence_ids))
        )
        for evidence in self._session.scalars(stmt):
            if evidence.kind is not EvidenceKind.KNOWLEDGE_ITEM:
                continue
            item = self._session.get(KnowledgeItem, evidence.target_id)
            if item is None:
                continue
            edges.append(
                Edge(
                    N.EVIDENCE,
                    str(evidence.id),
                    L.DRAWN_FROM,
                    N.KNOWLEDGE_ITEM,
                    str(item.id),
                    None,
                    "evidence.target_id",
                )
            )
            edges.append(
                Edge(
                    N.KNOWLEDGE_ITEM,
                    str(item.id),
                    L.ISSUED_UNDER,
                    N.NORMATIVE_SOURCE,
                    str(item.normative_source_id),
                    None,
                    "knowledge_item.normative_source_id",
                )
            )
