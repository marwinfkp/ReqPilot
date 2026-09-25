"""Artefact generation, versioning and export (architecture C.6, G.8; ``FR-DOC-001``..``-010``).

The deterministic pipeline, in the order architecture C.6 names it:

``load_baseline`` -> **authority check** -> ``assemble_sections`` -> ``validate_artefact``
-> ``render_markdown`` -> ``persist_artifact_version`` (+ trace links, audit);
DOCX and CSV are rendered on export from the **stored** structure.

The authority check is the ``FR-HIL-004`` gate for documents. An artefact is
generated only from an existing baseline, and only when **every** requirement
version it would render

* is a baseline member whose stored hash equals the version's content hash;
* is BASELINED (or SUPERSEDED, for a historical baseline);
* passed G1 as co-approval - every G1 task of some group for it APPROVED, with an
  APPROVE decision bound to its exact hash;
* has nothing still blocking it (the P8 readiness evaluator: open or unsigned
  conflicts, pending G2/G3, unreviewed G8, missing G5/G7, open defects);

and, for the SRS and the risk register, no project-level high-severity risk is
still unreviewed (G8). Otherwise nothing is stored, the refusal is audited, and
the reasons are returned. None of these decisions reads model output; the
document layer decides nothing about approval, baselining, gates or links - it
reads the records those decisions left.

**Versioning.** Every generation that produces new content creates a new,
immutable ``artifact_version``. Identical inputs produce an identical content
hash (the structure hash excludes the timestamp and the user), and regenerating
them returns the existing version - reported as reused, audited, never
duplicated and never overwritten.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.artifacts.docx import render_docx, safe_filename
from reqpilot.artifacts.markdown import MARKDOWN_LAYOUT_VERSION, render_markdown
from reqpilot.artifacts.model import (
    TRACEABLE_CITATION_KINDS,
    Document,
    Table,
    document_from_structure,
    sha256_json,
    sha256_text,
    validate_document,
)
from reqpilot.artifacts.registry import TemplateRegistry, packaged_templates
from reqpilot.domain.compliance.language import assert_artefact_language
from reqpilot.domain.enums import (
    UNREVIEWED_RISK_STATUSES,
    Action,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    ArtifactFormat,
    ArtifactType,
    AuditEventType,
    Gate,
    ResourceType,
    RiskScope,
    RiskSeverity,
)
from reqpilot.domain.errors import ArtifactError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.artifacts import (
    DETERMINISTIC_MODEL_IDENTIFIER,
    Artifact,
    ArtifactSection,
    ArtifactVersion,
)
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.domain.versioning import hashes_match
from reqpilot.repositories.approval import ApprovalDecisionRepository, ApprovalTaskRepository
from reqpilot.repositories.artifacts import ArtifactRepository
from reqpilot.repositories.risk import RiskRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.documents import assemblers
from reqpilot.services.documents.context import load_context
from reqpilot.services.governance.readiness import GovernanceReadinessService
from reqpilot.services.traceability.coverage import CoverageReport, CoverageService
from reqpilot.services.traceability.graph import TraceQueryService
from reqpilot.services.traceability.rtm import RtmBuilder, RtmRow, csv_safe
from reqpilot.services.traceability.scope import RequirementScope, ScopeService
from reqpilot.services.traceability.sync import Edge, TraceGraphSync

GENERATOR = "reqpilot.documents"
GENERATOR_VERSION = "1.0.0"
N = TraceNodeType
L = TraceLinkType

#: The artefacts that include project-level risks, and so need them governed.
_INCLUDES_PROJECT_RISKS = frozenset({ArtifactType.SRS, ArtifactType.RISK_REGISTER})

#: The document set the P8 exit asks for, in a sensible order.
DEFAULT_SET: tuple[ArtifactType, ...] = (
    ArtifactType.SRS,
    ArtifactType.RTM,
    ArtifactType.RISK_REGISTER,
    ArtifactType.USER_STORIES,
    ArtifactType.USE_CASES,
    ArtifactType.COMPLIANCE_MATRIX,
    ArtifactType.ASSUMPTIONS_DEPENDENCIES,
    ArtifactType.OPEN_ISSUES,
)


@dataclass(frozen=True)
class GenerationOutcome:
    artifact_type: ArtifactType
    refused: bool
    blockers: tuple[str, ...]
    version: ArtifactVersion | None
    reused: bool = False


@dataclass(frozen=True)
class Export:
    data: bytes
    media_type: str
    filename: str
    sha256: str


class ArtifactService:
    def __init__(
        self, session: Session, actor: Actor, *, templates: TemplateRegistry | None = None
    ) -> None:
        self._session = session
        self._actor = actor
        self._templates = templates or packaged_templates()
        self._artifacts = ArtifactRepository(session, actor)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------
    # the authority check (FR-HIL-004 for documents)
    # ------------------------------------------------------------------
    def authority_blockers(
        self, project_id: ProjectId, scope: RequirementScope, artifact_type: ArtifactType
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if not scope.items:
            blockers.append("EMPTY_BASELINE: the baseline scope holds no requirement version")
        readiness = GovernanceReadinessService(self._session, self._actor)
        tasks = ApprovalTaskRepository(self._session, self._actor)
        decisions = ApprovalDecisionRepository(self._session, self._actor)
        all_tasks = tasks.list_for_project(project_id)
        for item in scope.items:
            version, label = item.version, f"{item.human_id} v{item.version.version_no}"
            if item.member is None or not hashes_match(
                item.member.version_hash, version.content_hash
            ):
                blockers.append(f"BASELINE_HASH_MISMATCH: {label}")
            if version.state not in (RequirementState.BASELINED, RequirementState.SUPERSEDED):
                blockers.append(f"NOT_BASELINED: {label} is {version.state}")
            if not self._g1_complete(project_id, version, all_tasks, decisions):
                blockers.append(
                    f"G1_INCOMPLETE: {label} has no completed G1 co-approval bound to its hash"
                )
            for blocker in readiness.evaluate(project_id, version, stage="artifact").blockers:
                blockers.append(f"{label}: {blocker.render()}")
        if artifact_type in _INCLUDES_PROJECT_RISKS:
            for risk in RiskRepository(self._session, self._actor).list_for_project(
                project_id, scope=RiskScope.PROJECT
            ):
                if risk.severity is RiskSeverity.HIGH and risk.status in UNREVIEWED_RISK_STATUSES:
                    blockers.append(
                        f"[G8] G8_UNREVIEWED: project-level high-severity risk {risk.id} has not "
                        "been reviewed; the register cannot present it as governed"
                    )
        return tuple(blockers)

    @staticmethod
    def _g1_complete(project_id, version, all_tasks, decisions) -> bool:  # type: ignore[no-untyped-def]
        groups: dict[uuid.UUID | None, list] = {}  # type: ignore[type-arg]
        for task in all_tasks:
            if task.gate is Gate.G1_REQUIREMENT_BASELINE and task.subject_id == version.id:
                groups.setdefault(task.task_group_id, []).append(task)
        for group in groups.values():
            if not all(t.status is ApprovalTaskStatus.APPROVED for t in group):
                continue
            bound = [
                d
                for t in group
                for d in decisions.list_for_task(project_id, t.id)
                if d.decision is ApprovalDecisionType.APPROVE
                and hashes_match(d.subject_version_hash, version.content_hash)
            ]
            if len(bound) >= len(group):
                return True
        return False

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------
    def generate_set(
        self,
        project_id: ProjectId,
        baseline_id: uuid.UUID,
        artifact_types: tuple[ArtifactType, ...] = DEFAULT_SET,
    ) -> list[GenerationOutcome]:
        return [self.generate(project_id, baseline_id, t) for t in artifact_types]

    def generate(
        self, project_id: ProjectId, baseline_id: uuid.UUID, artifact_type: ArtifactType
    ) -> GenerationOutcome:
        require(
            self._actor,
            Action.ARTIFACT_GENERATE,
            ResourceRef(resource_type=ResourceType.ARTIFACT, project_id=project_id),
        )
        project = self._session.get(Project, project_id)
        if project is None:  # pragma: no cover - membership implies existence
            raise ArtifactError("project not found")
        scope = ScopeService(self._session, self._actor).baseline_scope(project_id, baseline_id)
        # The graph is brought up to date from persisted facts before anything
        # reads it; the sync writes only edges that facts support.
        TraceGraphSync(self._session, self._actor).sync(project_id)

        blockers = self.authority_blockers(project_id, scope, artifact_type)
        if blockers:
            self._audit.append(
                event_type=AuditEventType.ARTIFACT_GENERATION_REFUSED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type="baseline",
                subject_id=str(baseline_id),
                payload={
                    "artifact_type": str(artifact_type),
                    "blocker_count": len(blockers),
                    "blocker_codes": sorted({_code(b) for b in blockers}),
                },
            )
            return GenerationOutcome(artifact_type, True, blockers, None)

        template = self._templates.for_type(artifact_type)
        graph = TraceQueryService(self._session, self._actor).graph(project_id)
        context = load_context(self._session, self._actor, project, scope, graph)
        document = self._assemble(artifact_type, context, template)
        validate_document(document, frozenset(str(v) for v in scope.version_ids))
        if artifact_type is ArtifactType.COMPLIANCE_MATRIX:
            # FR-CMP-006 on the whole matrix: a prohibited compliance assertion
            # is a failure, never rewritten.
            try:
                assert_artefact_language(render_markdown(document))
            except ValueError as exc:
                raise ArtifactError(str(exc)) from exc

        artifact = self._artifacts.get_or_create(
            project_id, artifact_type, template.title, self._actor.actor_id
        )
        content_hash = document.content_hash()
        for existing in reversed(self._artifacts.versions(project_id, artifact.id)):
            if existing.baseline_id == baseline_id and existing.content_hash == content_hash:
                self._audit_generated(project_id, artifact, existing, reused=True)
                return GenerationOutcome(artifact_type, False, (), existing, reused=True)

        version_no = self._artifacts.next_version_no(project_id, artifact.id)
        generated_at = dt.datetime.now(dt.UTC)
        kb_version, kb_source = self._kb_version(project, context)
        stamp = (
            ("Artefact version", f"{artifact_type.value} v{version_no}"),
            ("Generated at", generated_at.isoformat()),
            ("Generated by", str(self._actor.actor_id)),
            ("Generator", f"{GENERATOR} {GENERATOR_VERSION}"),
            ("Model identifier", DETERMINISTIC_MODEL_IDENTIFIER),
            ("Prompt version", "not applicable (no prompt: deterministic assembly)"),
            (
                "Knowledge-base version",
                f"{kb_version} ({kb_source})"
                if kb_version is not None
                else "not applicable (no KB evidence cited)",
            ),
            ("Structure hash (sha256)", content_hash),
        )
        stamped = document.with_stamp(stamp)
        markdown = render_markdown(stamped)
        version = ArtifactVersion(
            id=uuid.uuid4(),
            artifact_id=artifact.id,
            project_id=project_id,
            version_no=version_no,
            artifact_type=artifact_type,
            baseline_id=baseline_id,
            template_id=template.template_id,
            template_version=template.version,
            generator=f"{GENERATOR} {GENERATOR_VERSION}",
            model_identifier=DETERMINISTIC_MODEL_IDENTIFIER,
            prompt_version=None,
            kb_version=kb_version,
            kb_version_source=kb_source,
            content_hash=content_hash,
            input_fingerprint=self._fingerprint(scope, template.template_id, template.version),
            structure=stamped.structure(),
            markdown=markdown,
            markdown_sha256=sha256_text(markdown),
            section_count=len(document.sections),
            cited_version_count=len(document.cited_version_ids()),
            generated_by=self._actor.actor_id,
            generated_at=generated_at,
        )
        sections = [
            ArtifactSection(
                artifact_version_id=version.id,
                project_id=project_id,
                section_key=s.key,
                number=s.number,
                title=s.title[:300],
                ordinal=index,
                kind=s.kind,
                content_hash=s.content_hash(),
            )
            for index, s in enumerate(document.sections)
        ]
        self._artifacts.add_version(artifact, version, sections)
        links = self._record_links(project_id, scope, document, version, sections)
        self._audit.append(
            event_type=AuditEventType.ARTIFACT_VERSION_CREATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="artifact_version",
            subject_id=str(version.id),
            subject_version=str(version_no),
            payload={
                "artifact_id": str(artifact.id),
                "artifact_type": str(artifact_type),
                "version_no": version_no,
                "baseline_id": str(baseline_id),
                "content_hash": content_hash,
                "markdown_sha256": version.markdown_sha256,
                "template": f"{template.template_id}@{template.version}",
                "model_identifier": DETERMINISTIC_MODEL_IDENTIFIER,
                "kb_version": kb_version,
                "section_count": version.section_count,
                "cited_version_count": version.cited_version_count,
                "trace_links_recorded": links,
            },
        )
        self._audit_generated(project_id, artifact, version, reused=False)
        return GenerationOutcome(artifact_type, False, (), version)

    def _assemble(self, artifact_type: ArtifactType, context, template) -> Document:  # type: ignore[no-untyped-def]
        if artifact_type is ArtifactType.RTM:
            return assemblers.assemble_rtm(
                context, template, RtmBuilder(self._session, self._actor)
            )
        dispatch = {
            ArtifactType.SRS: assemblers.assemble_srs,
            ArtifactType.USER_STORIES: assemblers.assemble_user_stories,
            ArtifactType.USE_CASES: assemblers.assemble_use_cases,
            ArtifactType.COMPLIANCE_MATRIX: assemblers.assemble_compliance_matrix,
            ArtifactType.RISK_REGISTER: assemblers.assemble_risk_register,
            ArtifactType.ASSUMPTIONS_DEPENDENCIES: assemblers.assemble_assumptions,
            ArtifactType.OPEN_ISSUES: assemblers.assemble_open_issues,
        }
        return dispatch[artifact_type](context, template)

    @staticmethod
    def _kb_version(project: Project, context) -> tuple[int | None, str]:  # type: ignore[no-untyped-def]
        if project.kb_version_pin is not None:
            return int(project.kb_version_pin), "project_pin"
        versions = [e.kb_version for e in context.evidence.values()]
        if versions:
            return max(versions), "evidence"
        return None, "none"

    @staticmethod
    def _fingerprint(scope: RequirementScope, template_id: str, template_version: str) -> str:
        return sha256_json(
            {
                "baseline_id": str(scope.baseline.id) if scope.baseline else None,
                "members": sorted(
                    [
                        str(i.version.id),
                        i.version.content_hash,
                        str(i.baseline.id) if i.baseline else "",
                    ]
                    for i in scope.items
                ),
                "template": f"{template_id}@{template_version}",
                "layout": MARKDOWN_LAYOUT_VERSION,
                "generator": f"{GENERATOR}@{GENERATOR_VERSION}",
            }
        )

    def _record_links(
        self,
        project_id: ProjectId,
        scope: RequirementScope,
        document: Document,
        version: ArtifactVersion,
        sections: list[ArtifactSection],
    ) -> int:
        """``RENDERED_IN``, ``CONTAINS`` and ``CITES`` - FR-DOC-008 made queryable."""
        edges: list[Edge] = []
        vid = str(version.id)
        rendered_baselines = {i.baseline.id for i in scope.items if i.baseline is not None}
        if scope.baseline is not None:
            rendered_baselines.add(scope.baseline.id)
        for baseline_id in sorted(rendered_baselines, key=str):
            edges.append(
                Edge(
                    N.BASELINE,
                    str(baseline_id),
                    L.RENDERED_IN,
                    N.ARTIFACT_VERSION,
                    vid,
                    None,
                    "artifact_version",
                )
            )
        by_key = {s.section_key: s for s in sections}
        for section in document.sections:
            row = by_key[section.key]
            edges.append(
                Edge(
                    N.ARTIFACT_VERSION,
                    vid,
                    L.CONTAINS,
                    N.ARTIFACT_SECTION,
                    str(row.id),
                    None,
                    "artifact_section",
                )
            )
            for citation in section.citations:
                if citation.kind not in TRACEABLE_CITATION_KINDS:
                    continue
                target = N(citation.kind)
                anchor = uuid.UUID(citation.id) if target is N.REQUIREMENT_VERSION else None
                edges.append(
                    Edge(
                        N.ARTIFACT_SECTION,
                        str(row.id),
                        L.CITES,
                        target,
                        citation.id,
                        anchor,
                        "artifact_section.citations",
                    )
                )
        for cited in sorted(document.cited_version_ids()):
            edges.append(
                Edge(
                    N.ARTIFACT_VERSION,
                    vid,
                    L.CITES,
                    N.REQUIREMENT_VERSION,
                    cited,
                    uuid.UUID(cited),
                    "artifact_version.citations",
                )
            )
        return TraceGraphSync(self._session, self._actor).record(project_id, edges)

    def _audit_generated(
        self, project_id: ProjectId, artifact: Artifact, version: ArtifactVersion, *, reused: bool
    ) -> None:
        self._audit.append(
            event_type=AuditEventType.ARTIFACT_GENERATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="artifact",
            subject_id=str(artifact.id),
            subject_version=str(version.version_no),
            payload={
                "artifact_type": str(version.artifact_type),
                "artifact_version_id": str(version.id),
                "baseline_id": str(version.baseline_id),
                "content_hash": version.content_hash,
                "reused_identical_version": reused,
            },
        )

    # ------------------------------------------------------------------
    # reads and exports
    # ------------------------------------------------------------------
    def list_artifacts(self, project_id: ProjectId) -> list[Artifact]:
        return self._artifacts.list_for_project(project_id)

    def versions(self, project_id: ProjectId, artifact_id: uuid.UUID) -> list[ArtifactVersion]:
        return self._artifacts.versions(project_id, artifact_id)

    def all_versions(self, project_id: ProjectId) -> list[ArtifactVersion]:
        return self._artifacts.versions_for_project(project_id)

    def get_artifact(self, project_id: ProjectId, artifact_id: uuid.UUID) -> Artifact | None:
        return self._artifacts.get(project_id, artifact_id)

    def get_version(self, project_id: ProjectId, version_id: uuid.UUID) -> ArtifactVersion | None:
        return self._artifacts.get_version(project_id, version_id)

    def sections(self, project_id: ProjectId, version_id: uuid.UUID) -> list[ArtifactSection]:
        return self._artifacts.sections(project_id, version_id)

    def verify(self, version: ArtifactVersion) -> Document:
        """Rebuild the stored document and check both hashes still match."""
        document = document_from_structure(dict(version.structure))
        if document.content_hash() != version.content_hash:
            raise ArtifactError("the stored structure no longer matches its content hash")
        if sha256_text(version.markdown) != version.markdown_sha256:
            raise ArtifactError("the stored Markdown no longer matches its hash")
        return document

    def export(self, project_id: ProjectId, version_id: uuid.UUID, fmt: ArtifactFormat) -> Export:
        require(
            self._actor,
            Action.ARTIFACT_EXPORT,
            ResourceRef(resource_type=ResourceType.ARTIFACT_VERSION, project_id=project_id),
        )
        version = self._artifacts.get_version(project_id, version_id)
        if version is None:
            raise ArtifactError("artefact version not found in this project")
        document = self.verify(version)
        stem = f"{version.artifact_type.value}-v{version.version_no}"
        if fmt is ArtifactFormat.MARKDOWN:
            data = version.markdown.encode("utf-8")
            export = Export(data, "text/markdown; charset=utf-8", safe_filename(stem, "md"), "")
        elif fmt is ArtifactFormat.DOCX:
            data = render_docx(document, generated_at=version.generated_at)
            export = Export(
                data,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                safe_filename(stem, "docx"),
                "",
            )
        else:
            if version.artifact_type is not ArtifactType.RTM:
                raise ArtifactError("CSV export is offered for the traceability matrix only")
            data = _rtm_csv_from(document).encode("utf-8")
            export = Export(data, "text/csv; charset=utf-8", safe_filename(stem, "csv"), "")
        digest = sha256_bytes(data)
        self._audit.append(
            event_type=AuditEventType.ARTIFACT_EXPORTED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="artifact_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={"format": str(fmt), "sha256": digest, "bytes": len(data)},
        )
        return Export(export.data, export.media_type, export.filename, digest)

    # -- working views (not artefacts) ----------------------------------
    def scope_for(self, project_id: ProjectId, baseline_id: uuid.UUID | None) -> RequirementScope:
        scopes = ScopeService(self._session, self._actor)
        if baseline_id is None:
            return scopes.project_scope(project_id)
        return scopes.baseline_scope(project_id, baseline_id)

    def rtm_rows(self, project_id: ProjectId, baseline_id: uuid.UUID | None) -> list[RtmRow]:
        graph = TraceQueryService(self._session, self._actor).graph(project_id)
        return RtmBuilder(self._session, self._actor).rows(
            self.scope_for(project_id, baseline_id), graph
        )

    def coverage(self, project_id: ProjectId, baseline_id: uuid.UUID | None) -> CoverageReport:
        return CoverageService(self._session, self._actor).report(
            self.scope_for(project_id, baseline_id)
        )


_CODE = re.compile(r"\b([A-Z][A-Z0-9_]{3,}):")


def _code(blocker: str) -> str:
    """The stable blocker code (e.g. ``G1_INCOMPLETE``) - references only, for audit."""
    match = _CODE.search(blocker)
    return match.group(1) if match else "UNSPECIFIED"


def _rtm_csv_from(document: Document) -> str:
    import csv
    import io

    matrix = next((s for s in document.sections if s.key == "matrix"), None)
    table = next((b for b in matrix.blocks if isinstance(b, Table)), None) if matrix else None
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if table is None:
        writer.writerow(["No requirement version is in this baseline."])
        return buffer.getvalue()
    writer.writerow(table.columns)
    for row in table.rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return buffer.getvalue()


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
