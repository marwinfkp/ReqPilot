"""The Requirements Traceability Matrix (``FR-TRC-002``), read from the persisted graph.

Every cell is produced by following **persisted** ``traceability_link`` edges
from the row's exact requirement version and then reading the linked row for
its display label. There is no manually maintained table and no inference: if
an edge is absent the cell says so (:data:`NOT_LINKED`), and the RTM never
claims a relationship the graph does not hold.

Rows are always about exact versions. A baseline-scope RTM lists the versions in
force as of that baseline; a project-scope RTM lists current versions and marks
which of them are not approved - it is a working view, and only the
baseline-scope RTM is ever rendered as an authoritative artefact.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.artifacts import ArtifactSection, ArtifactVersion
from reqpilot.domain.models.baseline import Baseline
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.models.elicitation import Stakeholder, Utterance
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    RequirementClassification,
    SourceDocument,
)
from reqpilot.domain.models.knowledge import KnowledgeItem, NormativeSource
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.policy import Actor
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.services.traceability.graph import TraceGraph
from reqpilot.services.traceability.scope import RequirementScope

N = TraceNodeType
L = TraceLinkType

#: What an empty cell says. Never blank, never guessed.
NOT_LINKED = "- not linked"

#: RTM columns, in order. The names are the ones the CSV header carries.
RTM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("requirement_id", "Requirement ID"),
    ("version", "Version"),
    ("state", "Lifecycle state"),
    ("statement", "Statement"),
    ("sources", "Source (stakeholder input / document)"),
    ("classification", "Classification"),
    ("acceptance_criteria", "Acceptance criteria"),
    ("conflicts", "Conflict status"),
    ("compliance", "Compliance mapping (candidate)"),
    ("security_privacy", "Security / privacy analysis"),
    ("risks", "Risk"),
    ("mitigations", "Mitigation"),
    ("evidence", "Evidence"),
    ("artifact_sections", "Artefact section(s)"),
    ("approval", "Approval"),
    ("baseline", "Baseline"),
    ("version_id", "Version UUID"),
    ("content_hash", "Content hash"),
)


@dataclass(frozen=True)
class RtmRow:
    version_id: uuid.UUID
    cells: dict[str, str]

    def get(self, key: str) -> str:
        return self.cells.get(key, NOT_LINKED)


def _short(value: object) -> str:
    return str(value)[:8]


def _join(values: list[str]) -> str:
    cleaned = [v for v in values if v]
    return "; ".join(cleaned) if cleaned else NOT_LINKED


class RtmBuilder:
    """Builds RTM rows from the graph. Reads only; called after authorisation."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor

    def _get(self, model: type, project_id: ProjectId, key: str) -> object | None:
        try:
            row = self._session.get(model, uuid.UUID(key))
        except ValueError:
            return None
        if row is None:
            return None
        owner = getattr(row, "project_id", project_id)
        return row if owner == project_id else None

    def rows(self, scope: RequirementScope, graph: TraceGraph) -> list[RtmRow]:
        return [self.row(scope.project_id, graph, item) for item in scope.items]

    def row(self, project_id: ProjectId, graph: TraceGraph, item: object) -> RtmRow:
        version = item.version  # type: ignore[attr-defined]
        vid = version.id
        get = self._get

        sources: list[str] = []
        for edge in graph.incoming(N.REQUIREMENT_VERSION, vid, L.SOURCES):
            if edge.from_type == str(N.UTTERANCE):
                utterance = get(Utterance, project_id, edge.from_id)
                speakers = [
                    getattr(get(Stakeholder, project_id, s), "name", None)
                    for s in graph.sources(N.UTTERANCE, edge.from_id, L.STATED)
                ]
                who = ", ".join(s for s in speakers if s) or "stakeholder"
                seq = getattr(utterance, "seq", "?")
                sources.append(f"utterance {_short(edge.from_id)} (#{seq}, {who})")
            elif edge.from_type == str(N.SOURCE_DOCUMENT):
                document = get(SourceDocument, project_id, edge.from_id)
                title = getattr(document, "title", "document")
                sources.append(f"document '{title}' ({_short(edge.from_id)})")
            else:
                sources.append(f"source chunk {_short(edge.from_id)}")

        labels = [
            get(RequirementClassification, project_id, t)
            for t in graph.targets(N.REQUIREMENT_VERSION, vid, L.CLASSIFIED_AS)
        ]
        labels = [label for label in labels if label is not None]
        latest = max((getattr(label, "revision_no", 0) for label in labels), default=0)
        classification = sorted(
            f"{label.category.value}"  # type: ignore[attr-defined]
            + (" (human)" if str(label.source) == "human" else "")  # type: ignore[attr-defined]
            for label in labels
            if label.revision_no == latest  # type: ignore[attr-defined]
        )

        criteria = [
            get(AcceptanceCriterion, project_id, t)
            for t in graph.targets(N.REQUIREMENT_VERSION, vid, L.SATISFIED_BY)
        ]
        criteria_cells = [
            f"AC{c.ordinal}: Given {c.given_text} When {c.when_text} Then {c.then_text}"  # type: ignore[attr-defined]
            for c in sorted(
                (c for c in criteria if c is not None),
                key=lambda c: c.ordinal,  # type: ignore[attr-defined]
            )
        ]

        conflicts = []
        for cid in graph.targets(N.REQUIREMENT_VERSION, vid, L.HAS_CONFLICT):
            conflict = get(Conflict, project_id, cid)
            if conflict is None:
                continue
            g4 = graph.targets(N.CONFLICT, cid, L.APPROVED_BY)
            note = ""
            if conflict.involves_stakeholder_disagreement:  # type: ignore[attr-defined]
                note = f", G4 signatures: {len(g4)}"
            conflicts.append(
                f"conflict {_short(cid)} {conflict.status.value}"  # type: ignore[attr-defined]
                + (f" ({conflict.resolution.value})" if conflict.resolution else "")  # type: ignore[attr-defined]
                + note
            )

        compliance, evidence_ids = [], set()
        for mid in graph.targets(N.REQUIREMENT_VERSION, vid, L.HAS_MAPPING):
            mapping = get(ComplianceMapping, project_id, mid)
            if mapping is None:
                continue
            compliance.append(
                f"{mapping.control_key} {mapping.control_title} - candidate, "  # type: ignore[attr-defined]
                f"{mapping.relationship.value.replace('_', ' ')}, status {mapping.status.value}"  # type: ignore[attr-defined]
            )
            evidence_ids.update(graph.targets(N.COMPLIANCE_MAPPING, mid, L.EVIDENCED_BY))

        security = []
        for fid in graph.targets(N.REQUIREMENT_VERSION, vid, L.HAS_SECURITY_FINDING):
            finding = get(SecurityPrivacyFinding, project_id, fid)
            if finding is None:
                continue
            security.append(
                f"{finding.category.value}/{finding.family.value}: authoritative level "  # type: ignore[attr-defined]
                f"{finding.risk_level.value}, status {finding.status.value}"  # type: ignore[attr-defined]
            )
            evidence_ids.update(graph.targets(N.SECURITY_PRIVACY_FINDING, fid, L.EVIDENCED_BY))

        risks, mitigations = [], []
        for rid in graph.targets(N.REQUIREMENT_VERSION, vid, L.HAS_RISK):
            risk = get(Risk, project_id, rid)
            if risk is None:
                continue
            risks.append(
                f"{risk.title} [{risk.severity.value}, {risk.status.value}]"  # type: ignore[attr-defined]
            )
            evidence_ids.update(graph.targets(N.RISK, rid, L.EVIDENCED_BY))
            for mid in graph.targets(N.RISK, rid, L.MITIGATED_BY):
                mitigation = get(RiskMitigation, project_id, mid)
                if mitigation is not None:
                    label = (
                        "AI-suggested, requires human validation"
                        if mitigation.is_ai_generated and mitigation.status.value == "suggested"  # type: ignore[attr-defined]
                        else mitigation.status.value  # type: ignore[attr-defined]
                    )
                    mitigations.append(f"{mitigation.suggestion} ({label})")  # type: ignore[attr-defined]
        if not risks and graph.outgoing(N.REQUIREMENT_VERSION, vid, L.RISK_ASSESSED_BY):
            risks.append("assessed - no risk recorded")

        evidence = []
        for eid in sorted(evidence_ids):
            source_titles = []
            for item_id in graph.targets(N.EVIDENCE, eid, L.DRAWN_FROM):
                for sid in graph.targets(N.KNOWLEDGE_ITEM, item_id, L.ISSUED_UNDER):
                    source = self._session.get(NormativeSource, uuid.UUID(sid))
                    if source is not None:
                        source_titles.append(source.title)
                if not source_titles:
                    item_row = self._session.get(KnowledgeItem, uuid.UUID(item_id))
                    if item_row is not None:
                        source_titles.append(item_row.item_key)
            evidence.append(
                f"evidence {_short(eid)}"
                + (f" ({', '.join(source_titles)})" if source_titles else "")
            )

        sections = []
        for sid in graph.sources(N.REQUIREMENT_VERSION, vid, L.CITES):
            section = get(ArtifactSection, project_id, sid)
            if section is None:
                continue
            artefact = get(ArtifactVersion, project_id, str(section.artifact_version_id))  # type: ignore[attr-defined]
            if artefact is None:
                continue
            sections.append(
                f"{artefact.artifact_type.value} v{artefact.version_no} section {section.number}"  # type: ignore[attr-defined]
            )

        approvals = []
        for did in graph.targets(N.REQUIREMENT_VERSION, vid, L.APPROVED_BY):
            decision = get(ApprovalDecision, project_id, did)
            if decision is None:
                continue
            task = self._session.get(ApprovalTask, decision.task_id)  # type: ignore[attr-defined]
            gate = task.gate.value if task is not None else "?"
            approvals.append(
                f"{gate} approved by {decision.role_exercised.value} "  # type: ignore[attr-defined]
                f"on {decision.decided_at.date().isoformat()}"  # type: ignore[attr-defined]
            )

        baselines = []
        for bid in graph.targets(N.REQUIREMENT_VERSION, vid, L.MEMBER_OF):
            baseline = get(Baseline, project_id, bid)
            if baseline is not None:
                baselines.append(str(baseline.label))  # type: ignore[attr-defined]

        cells = {
            "requirement_id": item.human_id,  # type: ignore[attr-defined]
            "version": f"v{version.version_no}",
            "state": version.state.value,
            "statement": version.statement,
            "sources": _join(sources),
            "classification": _join(classification),
            "acceptance_criteria": _join(criteria_cells),
            "conflicts": _join(conflicts) if conflicts else "none recorded",
            "compliance": _join(compliance),
            "security_privacy": _join(security),
            "risks": _join(risks),
            "mitigations": _join(mitigations),
            "evidence": _join(evidence),
            "artifact_sections": _join(sorted(sections)),
            "approval": _join(sorted(approvals)),
            "baseline": _join(sorted(baselines)),
            "version_id": str(vid),
            "content_hash": version.content_hash,
        }
        return RtmRow(version_id=vid, cells=cells)


def rtm_csv(rows: list[RtmRow]) -> str:
    """The RTM as CSV (``FR-TRC-002``). Every cell is data, quoted by the csv module.

    A cell that begins with a spreadsheet formula character is prefixed with an
    apostrophe, so a requirement statement cannot become a formula when the
    file is opened in a spreadsheet (CSV injection).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([title for _key, title in RTM_COLUMNS])
    for row in rows:
        writer.writerow([csv_safe(row.get(key)) for key, _title in RTM_COLUMNS])
    return buffer.getvalue()


def csv_safe(value: str) -> str:
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r") and value != NOT_LINKED:
        return "'" + value
    return value
