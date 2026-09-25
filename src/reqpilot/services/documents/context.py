"""Everything an artefact is assembled from, loaded once, read only (architecture C.6).

``load_baseline`` + ``assemble_sections`` of the documentation graph: the context
holds the baseline scope (only approved baseline members - ``FR-HIL-004``) and the
persisted rows the sections are built from - classifications, acceptance
criteria, compliance mappings, security/privacy findings, risks and their
mitigations and evidence, conflicts, approval decisions, open issues. Every
query is scoped to the project. Nothing is inferred, and nothing here is model
output other than the P3-P7 proposals that were already validated, persisted
and (where a gate applies) decided by a human.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import ApprovalDecisionType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.base import as_utc
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    ComplianceMappingEvidence,
    SecurityPrivacyFinding,
    SecurityPrivacyFindingEvidence,
)
from reqpilot.domain.models.elicitation import (
    Clarification,
    InterviewSession,
    QualityFinding,
    Stakeholder,
    Utterance,
)
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    RequirementClassification,
    SourceDocument,
)
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.knowledge import Evidence, KnowledgeItem, NormativeSource
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.risk import Risk, RiskEvidence, RiskMitigation
from reqpilot.domain.policy import Actor
from reqpilot.repositories.compliance import ComplianceGapRepository
from reqpilot.services.traceability.graph import TraceGraph
from reqpilot.services.traceability.scope import RequirementScope, ScopedVersion


@dataclass
class EvidenceView:
    id: uuid.UUID
    kb_version: int
    source_title: str | None
    source_type: str | None
    clause: str | None

    @property
    def label(self) -> str:
        where = self.source_title or "knowledge item"
        clause = f" {self.clause}" if self.clause else ""
        return f"{where}{clause} (evidence {str(self.id)[:8]}, KB v{self.kb_version})"


@dataclass
class DocumentContext:
    project: Project
    scope: RequirementScope
    graph: TraceGraph
    labels: dict[uuid.UUID, list[RequirementClassification]] = field(default_factory=dict)
    criteria: dict[uuid.UUID, list[AcceptanceCriterion]] = field(default_factory=dict)
    mappings: dict[uuid.UUID, list[ComplianceMapping]] = field(default_factory=dict)
    mapping_evidence: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    findings: dict[uuid.UUID, list[SecurityPrivacyFinding]] = field(default_factory=dict)
    finding_evidence: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    risks: list[Risk] = field(default_factory=list)
    risk_evidence: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    mitigations: dict[uuid.UUID, list[RiskMitigation]] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    tasks: list[ApprovalTask] = field(default_factory=list)
    decisions: dict[uuid.UUID, list[ApprovalDecision]] = field(default_factory=dict)
    evidence: dict[uuid.UUID, EvidenceView] = field(default_factory=dict)
    stakeholders: list[Stakeholder] = field(default_factory=list)
    utterances: dict[uuid.UUID, Utterance] = field(default_factory=dict)
    sessions: dict[uuid.UUID, InterviewSession] = field(default_factory=dict)
    documents: dict[uuid.UUID, SourceDocument] = field(default_factory=dict)
    open_findings: list[QualityFinding] = field(default_factory=list)
    open_clarifications: list[Clarification] = field(default_factory=list)
    gaps: list[ComplianceGap] = field(default_factory=list)
    all_versions: dict[uuid.UUID, RequirementVersion] = field(default_factory=dict)
    human_ids: dict[uuid.UUID, str] = field(default_factory=dict)

    # -- convenience ------------------------------------------------------
    @property
    def items(self) -> tuple[ScopedVersion, ...]:
        return self.scope.items

    def version_label(self, version_id: uuid.UUID) -> str:
        version = self.all_versions.get(version_id)
        if version is None:
            return str(version_id)[:8]
        return f"{self.human_ids.get(version.requirement_id, '?')} v{version.version_no}"

    def current_categories(self, version: RequirementVersion) -> list[str]:
        rows = self.labels.get(version.id, [])
        latest = max((r.revision_no for r in rows), default=0)
        cats = sorted({str(r.category) for r in rows if r.revision_no == latest})
        if not cats and version.category is not None:
            cats = [str(version.category)]
        return cats

    def risks_for(self, version_id: uuid.UUID) -> list[Risk]:
        return [r for r in self.risks if r.requirement_version_id == version_id]

    def project_risks(self) -> list[Risk]:
        return [r for r in self.risks if r.requirement_version_id is None]

    def conflicts_for(self, version_id: uuid.UUID) -> list[Conflict]:
        return [c for c in self.conflicts if version_id in (c.version_a_id, c.version_b_id)]

    def tasks_for(self, subject_id: uuid.UUID) -> list[ApprovalTask]:
        return [t for t in self.tasks if t.subject_id == subject_id]

    def approvals_for(self, subject_id: uuid.UUID) -> list[tuple[ApprovalTask, ApprovalDecision]]:
        out = []
        for task in self.tasks_for(subject_id):
            for decision in self.decisions.get(task.id, []):
                out.append((task, decision))
        return sorted(out, key=lambda td: (as_utc(td[1].decided_at), str(td[1].id)))

    def speakers(self, version: RequirementVersion) -> list[str]:
        """Who the version's sources name - stakeholder records first, then speaker labels."""
        names: list[str] = []
        by_id = {s.id: s for s in self.stakeholders}
        for ref in version.source_refs or []:
            if not isinstance(ref, dict):
                continue
            if ref.get("kind") == "utterance":
                try:
                    utterance = self.utterances.get(uuid.UUID(str(ref.get("ref"))))
                except ValueError:
                    utterance = None
                if utterance is not None and utterance.speaker_ref in by_id:
                    person = by_id[utterance.speaker_ref]  # type: ignore[index]
                    names.append(f"{person.name} ({person.stakeholder_role})")
                    continue
            for key in ("stakeholder", "speaker"):
                if ref.get(key):
                    names.append(str(ref[key]))
                    break
        return sorted(set(names))

    def source_descriptions(self, version: RequirementVersion) -> list[str]:
        out: list[str] = []
        for ref in version.source_refs or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "reference")
            doc = ref.get("document")
            session = ref.get("session")
            try:
                if doc and uuid.UUID(str(doc)) in self.documents:
                    out.append(f"{kind}: '{self.documents[uuid.UUID(str(doc))].title}'")
                    continue
                if session and uuid.UUID(str(session)) in self.sessions:
                    out.append(f"interview answer (session {str(session)[:8]})")
                    continue
            except ValueError:
                pass
            ref_text = str(ref.get("ref") or "")
            out.append(f"{kind}: {ref_text[:40]} (not resolvable in this project)")
        return out


def load_context(
    session: Session, actor: Actor, project: Project, scope: RequirementScope, graph: TraceGraph
) -> DocumentContext:
    """Load the rows an artefact may use. Called only after authorisation."""
    project_id = ProjectId(project.id)
    ctx = DocumentContext(project=project, scope=scope, graph=graph)

    def rows(model: type) -> list:  # type: ignore[type-arg]
        return list(session.scalars(select(model).where(model.project_id == project_id)))  # type: ignore[attr-defined]

    from reqpilot.domain.models.requirements import Requirement

    for requirement in rows(Requirement):
        ctx.human_ids[requirement.id] = requirement.human_id
    for version in rows(RequirementVersion):
        ctx.all_versions[version.id] = version
    for label in rows(RequirementClassification):
        ctx.labels.setdefault(label.requirement_version_id, []).append(label)
    for criterion in rows(AcceptanceCriterion):
        ctx.criteria.setdefault(criterion.requirement_version_id, []).append(criterion)
    for rows_list in ctx.criteria.values():
        rows_list.sort(key=lambda c: c.ordinal)
    for mapping in rows(ComplianceMapping):
        ctx.mappings.setdefault(mapping.requirement_version_id, []).append(mapping)
    for link in rows(ComplianceMappingEvidence):
        ctx.mapping_evidence.setdefault(link.mapping_id, []).append(link.evidence_id)
    for finding in rows(SecurityPrivacyFinding):
        ctx.findings.setdefault(finding.requirement_version_id, []).append(finding)
    for link in rows(SecurityPrivacyFindingEvidence):
        ctx.finding_evidence.setdefault(link.finding_id, []).append(link.evidence_id)
    ctx.risks = sorted(rows(Risk), key=lambda r: (as_utc(r.created_at), r.title_key))
    for link in rows(RiskEvidence):
        ctx.risk_evidence.setdefault(link.risk_id, []).append(link.evidence_id)
    for mitigation in rows(RiskMitigation):
        ctx.mitigations.setdefault(mitigation.risk_id, []).append(mitigation)
    ctx.conflicts = sorted(rows(Conflict), key=lambda c: (as_utc(c.created_at), str(c.id)))
    ctx.tasks = sorted(rows(ApprovalTask), key=lambda t: (as_utc(t.created_at), str(t.id)))
    by_task: dict[uuid.UUID, list[ApprovalDecision]] = defaultdict(list)
    for decision in rows(ApprovalDecision):
        by_task[decision.task_id].append(decision)
    ctx.decisions = dict(by_task)
    ctx.stakeholders = sorted(rows(Stakeholder), key=lambda s: (s.name, str(s.id)))
    ctx.utterances = {u.id: u for u in rows(Utterance)}
    ctx.sessions = {s.id: s for s in rows(InterviewSession)}
    ctx.documents = {d.id: d for d in rows(SourceDocument)}
    ctx.open_findings = [f for f in rows(QualityFinding) if str(f.status) == "open"]
    ctx.open_clarifications = [c for c in rows(Clarification) if str(c.status) == "open"]
    gap_repo = ComplianceGapRepository(session, actor)
    run_id = gap_repo.latest_run_id(project_id)
    ctx.gaps = gap_repo.list_for_run(project_id, run_id) if run_id is not None else []

    evidence_ids = {
        e
        for group in (ctx.mapping_evidence, ctx.finding_evidence, ctx.risk_evidence)
        for ids in group.values()
        for e in ids
    }
    if evidence_ids:
        stmt = select(Evidence).where(
            Evidence.project_id == project_id, Evidence.id.in_(sorted(evidence_ids))
        )
        for evidence in session.scalars(stmt):
            item = session.get(KnowledgeItem, evidence.target_id)
            source = session.get(NormativeSource, item.normative_source_id) if item else None
            ctx.evidence[evidence.id] = EvidenceView(
                id=evidence.id,
                kb_version=evidence.kb_version,
                source_title=source.title if source else None,
                source_type=str(source.source_type) if source else None,
                clause=getattr(item, "clause_ref", None) if item else None,
            )
    return ctx


def approved_decision_ids(ctx: DocumentContext, subject_id: uuid.UUID) -> list[uuid.UUID]:
    return [
        d.id
        for _t, d in ctx.approvals_for(subject_id)
        if d.decision is ApprovalDecisionType.APPROVE
    ]
