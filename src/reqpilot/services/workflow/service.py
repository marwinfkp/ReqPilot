"""The workflow service - generation, reading, editing and export (roadmap phase P10).

**Generation** (``generate_workflow``; called by the graph node as the pipeline's
system actor): verify G6 from the persisted approval records, load the approved
records, derive the workflow, validate it (fail closed), render-check it, and only
then store it - the workflow, its ordered children and their provenance - in one
flush. Generation is **idempotent**: the same run and the same approved inputs
return the stored workflow; changed inputs produce a new workflow that supersedes
the project's live one, which is kept, change log and all. A refusal stores
nothing but its audit event.

**Editing** (``FR-WFL-007``; the Project Manager): an edit is applied to the current
content, the result is validated against the provenance the workflow was generated
with, and only a valid result is stored - with a change-log row carrying the
previous and new value of every changed field, the actor, the role exercised and
the reason, and a reference-only audit event. The generated structure on the
``workflow`` row never changes.

**Export** (``FR-WFL-008``): the current revision, through P8's renderers.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.artifacts.docx import render_docx, safe_filename
from reqpilot.artifacts.markdown import render_markdown
from reqpilot.artifacts.model import Document, validate_document
from reqpilot.domain.compliance.language import assert_artefact_language
from reqpilot.domain.enums import (
    Action,
    ApprovalTaskStatus,
    ArtifactFormat,
    AuditEventType,
    ResourceType,
    Role,
    WorkflowStatus,
)
from reqpilot.domain.errors import ArtifactError, WorkflowError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowChange,
    WorkflowGate,
    WorkflowPhase,
)
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.workflow.derivation import derive_workflow
from reqpilot.domain.workflow.edits import (
    AddActivity,
    EditResult,
    RemoveActivity,
    UpdateActivity,
    UpdateGate,
    UpdatePhase,
    WorkflowEdit,
    apply_edit,
)
from reqpilot.domain.workflow.inputs import WorkflowInputs
from reqpilot.domain.workflow.plan import (
    SOURCE_COMPLIANCE_MAPPING,
    SOURCE_RISK,
    SOURCE_RISK_MITIGATION,
    SOURCE_SECURITY_FINDING,
    WorkflowPlan,
    plan_from_structure,
    sha256_json,
)
from reqpilot.domain.workflow.templates import WorkflowTemplates
from reqpilot.domain.workflow.validation import (
    known_sources,
    mandatory_sources,
    sources_of,
    validate_workflow,
)
from reqpilot.repositories.workflow import WorkflowRepository
from reqpilot.rules.risk import RiskRules, packaged_risk_rules
from reqpilot.rules.sdlc import SdlcRules, packaged_sdlc_rules
from reqpilot.rules.workflow import packaged_workflow_templates
from reqpilot.services.audit import AuditService
from reqpilot.services.documents.service import Export, sha256_bytes
from reqpilot.services.workflow.document import (
    ChangeView,
    WorkflowDocumentMeta,
    workflow_document,
)
from reqpilot.services.workflow.rows import plan_from_rows, rows_from_plan
from reqpilot.services.workflow.sources import G6Verified, WorkflowSourceLoader, refused

MAX_REASON = 2000


@dataclass(frozen=True)
class GenerationOutcome:
    workflow: Workflow | None
    reused: bool = False
    refused: bool = False
    findings: tuple[dict[str, str], ...] = ()
    superseded_id: uuid.UUID | None = None


@dataclass(frozen=True)
class WorkflowView:
    """One workflow, read: the row, its current content, its rows and its change log."""

    workflow: Workflow
    plan: WorkflowPlan
    phases: list[WorkflowPhase]
    activities: list[WorkflowActivity]
    gates: list[WorkflowGate]
    changes: list[WorkflowChange]
    source_labels: dict[tuple[str, str], str]


def input_fingerprint(inputs: WorkflowInputs, templates: WorkflowTemplates) -> str:
    """sha256 over everything a generated workflow is a function of."""
    return sha256_json(
        {
            "templates": templates.sha256,
            "run": inputs.sdlc_run_id,
            "candidate": inputs.candidate_id,
            "baseline": inputs.baseline_id,
            "mappings": sorted((m.id, m.content_hash, m.status) for m in inputs.mappings),
            "risks": sorted(
                (
                    r.id,
                    r.content_hash,
                    r.status,
                    sorted((m.id, m.status) for m in r.mitigations),
                )
                for r in inputs.risks
            ),
            "findings": sorted((f.id, f.content_hash, f.status) for f in inputs.findings),
            "gaps": sorted(g.id for g in inputs.gaps),
        }
    )


class WorkflowService:
    def __init__(
        self,
        session: Session,
        actor: Actor,
        *,
        templates: WorkflowTemplates | None = None,
        sdlc_rules: SdlcRules | None = None,
        risk_rules: RiskRules | None = None,
    ) -> None:
        self._session = session
        self._actor = actor
        self._templates = templates or packaged_workflow_templates()
        self._sdlc_rules = sdlc_rules or packaged_sdlc_rules()
        self._risk_rules = risk_rules or packaged_risk_rules()
        self._repo = WorkflowRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def templates(self) -> WorkflowTemplates:
        return self._templates

    # ------------------------------------------------------------------
    # generation (the pipeline's system actor)
    # ------------------------------------------------------------------
    def generate(
        self,
        project_id: ProjectId,
        sdlc_run_id: uuid.UUID,
        *,
        initiator: uuid.UUID,
        graph_run_id: uuid.UUID | None = None,
    ) -> GenerationOutcome:
        loader = WorkflowSourceLoader(
            self._session, self._actor, self._sdlc_rules, self._risk_rules
        )
        try:
            verified = loader.verify_g6(project_id, sdlc_run_id)
            inputs = loader.load(project_id, verified)
            plan = derive_workflow(inputs, self._templates)
        except WorkflowError as exc:
            return self._refuse(project_id, sdlc_run_id, graph_run_id, exc.findings)
        errors = validate_workflow(
            plan,
            candidate_key=inputs.candidate_key,
            candidate_id=inputs.candidate_id,
            roles=self._templates.roles,
            required=mandatory_sources(inputs),
            known=known_sources(inputs),
        )
        if errors:
            return self._refuse(
                project_id, sdlc_run_id, graph_run_id, tuple(e.as_dict() for e in errors)
            )
        fingerprint = input_fingerprint(inputs, self._templates)
        existing = self._repo.by_inputs(project_id, verified.run.id, fingerprint)
        if existing is not None:
            if existing.status is WorkflowStatus.SUPERSEDED:
                return self._refuse(
                    project_id,
                    sdlc_run_id,
                    graph_run_id,
                    (
                        {
                            "code": "INPUTS_MATCH_SUPERSEDED",
                            "severity": "error",
                            "message": "these exact inputs produced a workflow that was later "
                            "superseded; it is kept unchanged and is not revived",
                        },
                    ),
                )
            return GenerationOutcome(workflow=existing, reused=True)
        try:
            self._render_check(plan, project_id, inputs, verified)
        except (ArtifactError, ValueError) as exc:
            return self._refuse(
                project_id,
                sdlc_run_id,
                graph_run_id,
                ({"code": "RENDER_CHECK_FAILED", "severity": "error", "message": str(exc)},),
            )
        live = self._repo.live(project_id)
        if live is not None:
            self._repo.supersede(live)
        workflow = self._persist(plan, inputs, verified, fingerprint, graph_run_id, initiator, live)
        if live is not None:
            self._event(
                AuditEventType.WORKFLOW_SUPERSEDED,
                project_id,
                live,
                {"superseded_by": str(workflow.id), "sdlc_run_id": str(live.sdlc_run_id)},
            )
        self._event(
            AuditEventType.WORKFLOW_GENERATED,
            project_id,
            workflow,
            {
                "sdlc_run_id": str(workflow.sdlc_run_id),
                "candidate": workflow.candidate_key,
                "template_ref": workflow.template_ref,
                "generated_hash": workflow.generated_hash,
                "input_fingerprint": fingerprint,
                "phases": len(plan.phases),
                "activities": len(plan.activities()),
                "gates": len(plan.gates()),
                "checkpoints": sum(
                    1 for _p, g in plan.gates() if g.kind == "compliance_checkpoint"
                ),
                "open_items": len(plan.open_items),
                "g6_decisions": [str(d) for d in verified.decision_ids],
                "status": str(workflow.status),
            },
            graph_run_id=graph_run_id,
        )
        return GenerationOutcome(
            workflow=workflow, superseded_id=live.id if live is not None else None
        )

    def _persist(
        self,
        plan: WorkflowPlan,
        inputs: WorkflowInputs,
        verified: G6Verified,
        fingerprint: str,
        graph_run_id: uuid.UUID | None,
        initiator: uuid.UUID,
        live: Workflow | None,
    ) -> Workflow:
        project_id = uuid.UUID(inputs.project_id)
        workflow_id = uuid.uuid4()
        digest = plan.content_hash()
        workflow = Workflow(
            id=workflow_id,
            project_id=project_id,
            sdlc_run_id=verified.run.id,
            selected_candidate_id=verified.candidate.id,
            candidate_key=verified.candidate.candidate_key,
            baseline_id=verified.run.baseline_id,
            graph_run_id=graph_run_id,
            supersedes_workflow_id=live.id if live is not None else None,
            status=WorkflowStatus.OPEN_ITEMS if plan.open_items else WorkflowStatus.COMPLETE,
            template_ref=self._templates.ref,
            templates_sha256=self._templates.sha256,
            input_fingerprint=fingerprint,
            generated_structure=plan.canonical(),
            generated_hash=digest,
            revision=1,
            content_hash=digest,
            open_items=[f.as_dict() for f in plan.open_items],
            generated_by=initiator,
        )
        rows = rows_from_plan(project_id, workflow_id, plan)
        return self._repo.add(workflow, rows.phases, rows.activities, rows.gates, rows.sources)

    def _render_check(
        self,
        plan: WorkflowPlan,
        project_id: ProjectId,
        inputs: WorkflowInputs,
        verified: G6Verified,
    ) -> None:
        """The workflow must be exportable before it is stored (FR-WFL-008)."""
        now = utc_now().isoformat()
        meta = WorkflowDocumentMeta(
            project_name=self._project_name(project_id),
            workflow_id="(not yet stored)",
            revision=1,
            status="open_items" if plan.open_items else "complete",
            sdlc_run_id=inputs.sdlc_run_id,
            baseline_label=inputs.baseline_label,
            g6_decisions=len(verified.decision_ids),
            content_hash=plan.content_hash(),
            generated_at=now,
            last_changed_at=now,
        )
        document = workflow_document(
            plan, meta, role_label=self._templates.role_label, source_label={}
        )
        validate_document(document, frozenset())
        assert_artefact_language(render_markdown(document))

    def _refuse(
        self,
        project_id: ProjectId,
        sdlc_run_id: uuid.UUID,
        graph_run_id: uuid.UUID | None,
        findings: tuple[dict[str, str], ...],
    ) -> GenerationOutcome:
        self._audit.append(
            event_type=AuditEventType.WORKFLOW_GENERATION_REFUSED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="sdlc_run",
            subject_id=str(sdlc_run_id),
            graph_run_id=graph_run_id,
            payload={"codes": sorted({f["code"] for f in findings}), "count": len(findings)},
        )
        return GenerationOutcome(workflow=None, refused=True, findings=findings)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def get(self, project_id: ProjectId, workflow_id: uuid.UUID) -> Workflow | None:
        return self._repo.get(project_id, workflow_id)

    def list_workflows(self, project_id: ProjectId) -> list[Workflow]:
        return self._repo.list_for_project(project_id)

    def for_run(self, project_id: ProjectId, sdlc_run_id: uuid.UUID) -> Workflow | None:
        """The run's current workflow: its live one, else its most recent."""
        found = self._repo.for_run(project_id, sdlc_run_id)
        live = [w for w in found if w.status is not WorkflowStatus.SUPERSEDED]
        chosen = live or found
        return chosen[-1] if chosen else None

    def view(self, project_id: ProjectId, workflow: Workflow) -> WorkflowView:
        phases = self._repo.phases(project_id, workflow.id)
        activities = self._repo.activities(project_id, workflow.id)
        gates = self._repo.gates(project_id, workflow.id)
        sources = self._repo.sources(project_id, workflow.id)
        plan = plan_from_rows(workflow, phases, activities, gates, sources)
        return WorkflowView(
            workflow=workflow,
            plan=plan,
            phases=phases,
            activities=activities,
            gates=gates,
            changes=self._repo.changes(project_id, workflow.id),
            source_labels=self._source_labels(project_id, sources),
        )

    def _source_labels(
        self, project_id: ProjectId, sources: list[Any]
    ) -> dict[tuple[str, str], str]:
        """Display labels for provenance, read from the source rows themselves."""
        wanted: dict[str, set[uuid.UUID]] = {}
        for row in sources:
            wanted.setdefault(row.source_type, set()).add(row.source_id)
        labels: dict[tuple[str, str], str] = {}
        models: dict[str, Any] = {
            SOURCE_COMPLIANCE_MAPPING: ComplianceMapping,
            SOURCE_RISK: Risk,
            SOURCE_RISK_MITIGATION: RiskMitigation,
            SOURCE_SECURITY_FINDING: SecurityPrivacyFinding,
        }
        for source_type, ids in wanted.items():
            model = models.get(source_type)
            if model is None:
                continue
            rows = self._session.scalars(
                select(model).where(model.project_id == project_id, model.id.in_(ids))
            )
            for row in rows:
                labels[(source_type, str(row.id))] = _label(source_type, row)
        return labels

    # ------------------------------------------------------------------
    # editing (FR-WFL-007; the Project Manager)
    # ------------------------------------------------------------------
    def edit(
        self,
        project_id: ProjectId,
        workflow_id: uuid.UUID,
        edit: WorkflowEdit,
        *,
        reason: str,
    ) -> WorkflowChange:
        require(
            self._actor,
            Action.WORKFLOW_EDIT,
            ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=project_id),
        )
        workflow = self._repo.get(project_id, workflow_id)
        if workflow is None:
            raise refused("WORKFLOW_NOT_FOUND", "workflow not found in this project")
        if workflow.status is WorkflowStatus.SUPERSEDED:
            raise refused("WORKFLOW_SUPERSEDED", "a superseded workflow is kept unchanged")
        text = " ".join(str(reason or "").split())
        if not text:
            raise refused("EDIT_REASON_REQUIRED", "an edit records its reason (FR-WFL-007)")
        if len(text) > MAX_REASON:
            raise refused("EDIT_REASON_TOO_LONG", f"the reason exceeds {MAX_REASON} characters")

        view = self.view(project_id, workflow)
        revision = workflow.revision + 1
        new_plan, result = apply_edit(view.plan, edit, revision=revision)
        generated = plan_from_structure(workflow.generated_structure)
        recorded = sources_of(generated)
        errors = validate_workflow(
            new_plan,
            candidate_key=workflow.candidate_key,
            candidate_id=str(workflow.selected_candidate_id),
            roles=self._templates.roles,
            required={
                k: v
                for k, v in recorded.items()
                if k
                in (
                    SOURCE_COMPLIANCE_MAPPING,
                    SOURCE_RISK,
                    SOURCE_RISK_MITIGATION,
                    SOURCE_SECURITY_FINDING,
                )
            },
            known=recorded,
        )
        if errors:
            raise WorkflowError(
                "the edited workflow would fail validation: "
                + "; ".join(f"{e.code}: {e.message}" for e in errors[:5]),
                tuple(e.as_dict() for e in errors),
            )

        element_id = self._apply_rows(project_id, workflow, view, new_plan, result)
        before = workflow.content_hash
        after = new_plan.content_hash()
        change = WorkflowChange(
            id=uuid.uuid4(),
            project_id=project_id,
            workflow_id=workflow.id,
            revision=revision,
            operation=result.operation,
            element_type=result.element_type,
            element_id=element_id,
            element_key=result.element_key,
            changes=result.changes,
            reason=text,
            actor_id=self._actor.actor_id,
            role_exercised=self._role_exercised(project_id),
            content_hash_before=before,
            content_hash_after=after,
        )
        workflow.revision = revision
        workflow.content_hash = after
        self._repo.save_edit(workflow, change)
        self._event(
            AuditEventType.WORKFLOW_EDITED,
            project_id,
            workflow,
            {
                "change_id": str(change.id),
                "revision": revision,
                "operation": result.operation,
                "element_type": result.element_type,
                "element_key": result.element_key,
                "fields": sorted(result.changes),
                "content_hash_before": before,
                "content_hash_after": after,
            },
        )
        return change

    def edit_for_element(
        self,
        project_id: ProjectId,
        workflow_id: uuid.UUID,
        *,
        element_type: str,
        element_id: uuid.UUID,
        changes: dict[str, Any] | None = None,
        remove: bool = False,
    ) -> WorkflowEdit:
        """Translate an element id (what a client names) into a keyed edit."""
        workflow = self._repo.get(project_id, workflow_id)
        if workflow is None:
            raise refused("WORKFLOW_NOT_FOUND", "workflow not found in this project")
        rows: list[Any]
        if element_type == "phase":
            rows = self._repo.phases(project_id, workflow.id)
        elif element_type == "activity":
            rows = self._repo.activities(project_id, workflow.id)
        elif element_type == "gate":
            rows = self._repo.gates(project_id, workflow.id)
        else:
            raise refused("EDIT_UNKNOWN_ELEMENT", f"unknown element type {element_type!r}")
        row = next((r for r in rows if r.id == element_id), None)
        if row is None:
            raise refused(
                "EDIT_UNKNOWN_ELEMENT", f"no {element_type} {element_id} in this workflow"
            )
        if remove:
            if element_type != "activity":
                raise refused("EDIT_REMOVES_MANDATORY", "only an activity can be removed")
            return RemoveActivity(row.key)
        if element_type == "phase":
            return UpdatePhase(row.key, changes or {})
        if element_type == "activity":
            return UpdateActivity(row.key, changes or {})
        return UpdateGate(row.key, changes or {})

    def phase_key(self, project_id: ProjectId, workflow_id: uuid.UUID, phase_id: uuid.UUID) -> str:
        phase = next(
            (p for p in self._repo.phases(project_id, workflow_id) if p.id == phase_id), None
        )
        if phase is None:
            raise refused("EDIT_UNKNOWN_ELEMENT", f"no phase {phase_id} in this workflow")
        return phase.key

    def _apply_rows(
        self,
        project_id: ProjectId,
        workflow: Workflow,
        view: WorkflowView,
        new_plan: WorkflowPlan,
        result: EditResult,
    ) -> uuid.UUID:
        now = utc_now()
        if result.element_type == "phase":
            row = next(p for p in view.phases if p.key == result.element_key)
            new = new_plan.phase(result.element_key)
            assert new is not None
            for field in result.changes:
                setattr(
                    row,
                    field,
                    list(getattr(new, field))
                    if isinstance(getattr(new, field), tuple)
                    else getattr(new, field),
                )
            row.origin = new.origin
            row.updated_at = now
            return row.id
        if result.element_type == "gate":
            gate_row = next(g for g in view.gates if g.key == result.element_key)
            _p, new_gate = next((p, g) for p, g in new_plan.gates() if g.key == result.element_key)
            for field in result.changes:
                value = getattr(new_gate, field)
                setattr(gate_row, field, list(value) if isinstance(value, tuple) else value)
            gate_row.origin = new_gate.origin
            gate_row.updated_at = now
            return gate_row.id
        if result.operation == "update":
            act_row = next(a for a in view.activities if a.key == result.element_key)
            _p, new_act = next(
                (p, a) for p, a in new_plan.activities() if a.key == result.element_key
            )
            for field in result.changes:
                value = getattr(new_act, field)
                setattr(act_row, field, list(value) if isinstance(value, tuple) else value)
            act_row.origin = new_act.origin
            act_row.updated_at = now
            return act_row.id
        if result.operation == "add":
            phase_plan, added = next(
                (p, a) for p, a in new_plan.activities() if a.key == result.element_key
            )
            phase_row = next(p for p in view.phases if p.key == phase_plan.key)
            used = [a.position for a in view.activities if a.phase_id == phase_row.id]
            activity = WorkflowActivity(
                id=uuid.uuid4(),
                project_id=project_id,
                workflow_id=workflow.id,
                phase_id=phase_row.id,
                position=max(used, default=0) + 1,
                key=added.key,
                kind=added.kind,
                name=added.name,
                description=added.description,
                responsible_roles=list(added.responsible_roles),
                deliverables=list(added.deliverables),
                mandatory=False,
                origin=added.origin,
            )
            self._repo.add_activity(activity)
            return activity.id
        removed = next(a for a in view.activities if a.key == result.element_key)
        self._repo.remove_activity(removed)
        return removed.id

    def _role_exercised(self, project_id: ProjectId) -> str:
        roles = self._actor.roles_in(project_id)
        if Role.PROJECT_MANAGER in roles:
            return str(Role.PROJECT_MANAGER)
        return "superuser" if self._actor.is_superuser else ",".join(sorted(str(r) for r in roles))

    def changes(self, project_id: ProjectId, workflow_id: uuid.UUID) -> list[WorkflowChange]:
        return self._repo.changes(project_id, workflow_id)

    # ------------------------------------------------------------------
    # export (FR-WFL-008)
    # ------------------------------------------------------------------
    def document(self, project_id: ProjectId, workflow: Workflow) -> Document:
        view = self.view(project_id, workflow)
        if view.plan.content_hash() != workflow.content_hash:
            raise refused(
                "WORKFLOW_INTEGRITY",
                "the workflow's rows no longer match its recorded content hash",
            )
        meta = WorkflowDocumentMeta(
            project_name=self._project_name(project_id),
            workflow_id=str(workflow.id),
            revision=workflow.revision,
            status=str(workflow.status),
            sdlc_run_id=str(workflow.sdlc_run_id),
            baseline_label=self._baseline_label(workflow),
            g6_decisions=self._g6_decisions(workflow),
            content_hash=workflow.content_hash,
            generated_at=_iso(workflow.created_at),
            last_changed_at=_iso(workflow.updated_at),
        )
        document = workflow_document(
            view.plan,
            meta,
            role_label=self._templates.role_label,
            source_label=view.source_labels,
            changes=[
                ChangeView(
                    revision=c.revision,
                    at=_iso(c.created_at),
                    role=c.role_exercised,
                    operation=c.operation,
                    element=f"{c.element_type} {c.element_key}",
                    fields=tuple(sorted(c.changes)),
                    reason=c.reason,
                )
                for c in view.changes
            ],
        )
        validate_document(document, frozenset())
        return document

    def export(self, project_id: ProjectId, workflow_id: uuid.UUID, fmt: ArtifactFormat) -> Export:
        require(
            self._actor,
            Action.WORKFLOW_EXPORT,
            ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=project_id),
        )
        workflow = self._repo.get(project_id, workflow_id)
        if workflow is None:
            raise refused("WORKFLOW_NOT_FOUND", "workflow not found in this project")
        document = self.document(project_id, workflow)
        markdown = render_markdown(document)
        try:
            assert_artefact_language(markdown)
        except ValueError as exc:
            raise refused("EXPORT_LANGUAGE", str(exc)) from exc
        stem = f"workflow-{workflow.candidate_key}-r{workflow.revision}"
        if fmt is ArtifactFormat.MARKDOWN:
            data = markdown.encode("utf-8")
            media, name = "text/markdown; charset=utf-8", safe_filename(stem, "md")
        elif fmt is ArtifactFormat.DOCX:
            data = render_docx(document, generated_at=workflow.updated_at)
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            name = safe_filename(stem, "docx")
        else:
            raise refused("EXPORT_FORMAT", "a workflow exports as Markdown or DOCX (FR-WFL-008)")
        digest = sha256_bytes(data)
        self._event(
            AuditEventType.WORKFLOW_EXPORTED,
            project_id,
            workflow,
            {
                "format": str(fmt),
                "sha256": digest,
                "bytes": len(data),
                "revision": workflow.revision,
            },
        )
        return Export(data, media, name, digest)

    # ------------------------------------------------------------------
    def _project_name(self, project_id: ProjectId) -> str:
        project = self._session.get(Project, project_id)
        return project.name if project is not None else str(project_id)

    def _baseline_label(self, workflow: Workflow) -> str:
        from reqpilot.domain.models.baseline import Baseline

        baseline = self._session.get(Baseline, workflow.baseline_id)
        return baseline.label if baseline is not None else str(workflow.baseline_id)

    def _g6_decisions(self, workflow: Workflow) -> int:
        from reqpilot.services.sdlc.service import SdlcService

        service = SdlcService(self._session, self._actor, self._sdlc_rules)
        run = service.get(ProjectId(workflow.project_id), workflow.sdlc_run_id)
        if run is None:
            return 0
        tasks = service.g6_tasks(ProjectId(workflow.project_id), run)
        return sum(1 for t in tasks if t.status is ApprovalTaskStatus.APPROVED)

    def _event(
        self,
        event_type: AuditEventType,
        project_id: ProjectId,
        workflow: Workflow,
        payload: dict[str, Any],
        *,
        graph_run_id: uuid.UUID | None = None,
    ) -> None:
        self._audit.append(
            event_type=event_type,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="workflow",
            subject_id=str(workflow.id),
            subject_version=str(workflow.revision),
            graph_run_id=graph_run_id,
            payload=payload,
        )


def _iso(value: dt.datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _label(source_type: str, row: Any) -> str:
    if source_type == SOURCE_COMPLIANCE_MAPPING:
        return f"mapping {row.control_key}"
    if source_type == SOURCE_RISK:
        return f"risk: {row.title}"
    if source_type == SOURCE_RISK_MITIGATION:
        text = " ".join(str(row.suggestion).split())
        return f"mitigation: {text[:117] + '...' if len(text) > 120 else text}"
    return f"{row.family} requirement"


__all__ = [
    "AddActivity",
    "GenerationOutcome",
    "WorkflowService",
    "WorkflowView",
    "input_fingerprint",
]
