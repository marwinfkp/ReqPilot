"""P10 generation, persistence, traceability, editing and export on the real P3-P9 path.

The high-regulation world (``tests/p10_helpers.py``) goes through extraction,
analysis, the governed gates, G1, the SDLC recommendation and G6 exactly as the
product runs them; these tests then exercise ``sdlc_graph``'s workflow mode and the
workflow service against the persisted rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.p10_helpers import P10World, make_p10_world

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    ArtifactFormat,
    AuditEventType,
    GraphRunStatus,
    Role,
    SdlcRunStatus,
    WorkflowStatus,
)
from reqpilot.domain.errors import AuthorizationError, WorkflowError
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.compliance import ComplianceMapping
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.models.sdlc import SdlcCandidate
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowChange,
    WorkflowGate,
    WorkflowPhase,
)
from reqpilot.domain.workflow.edits import AddActivity, RemoveActivity, UpdateGate, UpdatePhase
from reqpilot.services.traceability import TraceGraphSync

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session) -> P10World:
    return make_p10_world(db_session)


def events(world: P10World, event_type: AuditEventType) -> list[AuditEvent]:
    return list(
        world.session.scalars(
            select(AuditEvent)
            .where(AuditEvent.project_id == world.project_id, AuditEvent.event_type == event_type)
            .order_by(AuditEvent.seq)
        )
    )


def count(session: Session, model: type, **where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return int(session.scalar(stmt) or 0)


# --- generation ---------------------------------------------------------------------------


def test_the_workflow_of_a_g6_selection_is_generated_persisted_and_retrievable(
    world: P10World,
) -> None:
    run = world.select()
    summary = world.generate(run.id)
    assert summary.status is GraphRunStatus.COMPLETED, summary.errors
    assert summary.workflow_id is not None and not summary.workflow_reused

    service = world.workflows()
    workflow = service.get(world.project_id, summary.workflow_id)
    assert workflow is not None
    assert workflow.sdlc_run_id == run.id and workflow.candidate_key == run.selected_candidate
    candidate = world.session.get(SdlcCandidate, workflow.selected_candidate_id)
    assert candidate is not None and candidate.rank == 1
    assert candidate.candidate_key == run.selected_candidate
    assert workflow.status is WorkflowStatus.OPEN_ITEMS, "the world has open compliance gaps"
    assert workflow.revision == 1 and workflow.content_hash == workflow.generated_hash

    view = service.view(world.project_id, workflow)
    assert [p.position for p in view.phases] == list(range(1, len(view.phases) + 1))
    assert view.plan.content_hash() == workflow.content_hash
    assert {f.code for f in view.plan.open_items} >= {"COMPLIANCE_GAP_OPEN"}
    assert len(events(world, AuditEventType.WORKFLOW_GENERATED)) == 1
    nodes = {
        e.payload.get("node")
        for e in world.session.scalars(
            select(AuditEvent).where(AuditEvent.graph_run_id == summary.graph_run_id)
        )
    }
    assert {"generate_workflow", "emit_artefacts"} <= nodes


def test_generation_is_refused_until_all_four_g6_approvals_exist(world: P10World) -> None:
    run = world.rank()
    refused = world.generate(run.id)
    assert refused.workflow_id is None and refused.status is GraphRunStatus.FAILED
    assert {f["code"] for f in refused.workflow_findings} == {"G6_NOT_PASSED"}
    assert count(world.session, Workflow, project_id=world.project_id) == 0
    assert len(events(world, AuditEventType.WORKFLOW_GENERATION_REFUSED)) == 1

    tasks = world.p9.g6_tasks(ApprovalTaskStatus.OPEN)
    assert len(tasks) == 4
    for task in tasks[:3]:
        world.p9.decide(task)
    assert world.generate(run.id).workflow_id is None, "three of four is not G6"
    world.p9.decide(tasks[3])
    world.session.refresh(run)
    assert run.status is SdlcRunStatus.SELECTED
    assert world.generate(run.id).workflow_id is not None


def test_a_rejected_g6_never_generates_a_workflow(world: P10World) -> None:
    run = world.rank()
    task = world.p9.g6_tasks(ApprovalTaskStatus.OPEN)[0]
    world.p9.decide(task, ApprovalDecisionType.REJECT, justification="Not this model (synthetic).")
    world.session.refresh(run)
    assert run.status is SdlcRunStatus.REJECTED
    summary = world.generate(run.id)
    assert summary.workflow_id is None
    assert {f["code"] for f in summary.workflow_findings} == {"G6_NOT_PASSED"}


def test_the_workflow_is_the_selected_candidates_never_the_runner_up(world: P10World) -> None:
    run = world.select()
    summary = world.generate(run.id)
    workflow = world.workflows().get(world.project_id, summary.workflow_id)  # type: ignore[arg-type]
    assert workflow is not None
    assert workflow.candidate_key == run.selected_candidate == run.top_candidate
    assert workflow.candidate_key != run.runner_up_candidate
    phase_keys = [
        p.key
        for p in world.session.scalars(
            select(WorkflowPhase)
            .where(WorkflowPhase.workflow_id == workflow.id)
            .order_by(WorkflowPhase.position)
        )
    ]
    from reqpilot.rules.workflow import packaged_workflow_templates

    template = packaged_workflow_templates().templates[run.selected_candidate]  # type: ignore[index]
    assert phase_keys == [p.key for p in template.phases]


def test_generation_is_idempotent_and_writes_no_duplicate_rows_or_links(world: P10World) -> None:
    run = world.select()
    first = world.generate(run.id)
    links = count(world.session, TraceabilityLink, project_id=world.project_id)
    activities = count(world.session, WorkflowActivity, project_id=world.project_id)
    again = world.generate(run.id)
    assert again.workflow_id == first.workflow_id and again.workflow_reused
    assert count(world.session, Workflow, project_id=world.project_id) == 1
    assert count(world.session, WorkflowActivity, project_id=world.project_id) == activities
    assert count(world.session, TraceabilityLink, project_id=world.project_id) == links
    result = TraceGraphSync(world.session, world.analyst).sync(world.project_id)
    assert result.by_link_type.get("REALISED_AS", 0) == 0, "a sync finds nothing new"
    assert len(events(world, AuditEventType.WORKFLOW_GENERATED)) == 1


def test_the_trace_links_resolve_to_the_records_that_caused_them(world: P10World) -> None:
    run = world.select()
    summary = world.generate(run.id)
    workflow_id = str(summary.workflow_id)
    links = list(
        world.session.scalars(
            select(TraceabilityLink).where(TraceabilityLink.project_id == world.project_id)
        )
    )
    realised = [lnk for lnk in links if lnk.link_type == "REALISED_AS"]
    assert len(realised) == 1 and realised[0].to_id == workflow_id
    candidate = world.session.get(SdlcCandidate, uuid.UUID(realised[0].from_id))
    assert candidate is not None and candidate.candidate_key == run.selected_candidate
    gates = {str(g.id): g for g in world.session.scalars(select(WorkflowGate))}
    for link in (lnk for lnk in links if lnk.link_type == "REQUIRES_CHECKPOINT"):
        assert link.to_id in gates and gates[link.to_id].kind == "compliance_checkpoint"
        mapping = world.session.get(ComplianceMapping, uuid.UUID(link.from_id))
        assert mapping is not None and mapping.project_id == world.project_id
        assert gates[link.to_id].key == f"checkpoint:{mapping.control_key}"
    activities = {str(a.id): a for a in world.session.scalars(select(WorkflowActivity))}
    risk_links = [
        lnk for lnk in links if lnk.link_type == "REQUIRES_ACTIVITY" and lnk.from_type == "risk"
    ]
    assert risk_links
    for link in risk_links:
        assert activities[link.to_id].kind in ("risk_treatment", "risk_verification")
        assert world.session.get(Risk, uuid.UUID(link.from_id)) is not None


def test_a_project_managers_workflow_gets_its_links_at_the_next_trace_sync(
    world: P10World,
) -> None:
    run = world.select()
    summary = world.generate(run.id, actor=world.manager)
    assert summary.workflow_id is not None
    before = [
        lnk
        for lnk in world.session.scalars(select(TraceabilityLink))
        if lnk.link_type == "REALISED_AS"
    ]
    assert not before, "a Project Manager asserts no trace link (policy rule 11)"
    TraceGraphSync(world.session, world.analyst).sync(world.project_id)
    after = [
        lnk
        for lnk in world.session.scalars(select(TraceabilityLink))
        if lnk.link_type in ("REALISED_AS", "REQUIRES_CHECKPOINT", "REQUIRES_ACTIVITY")
    ]
    assert after and all(lnk.origin == "workflow_source" for lnk in after)


def test_a_new_g6_selection_supersedes_the_live_workflow_and_keeps_it(world: P10World) -> None:
    first_run = world.select()
    first = world.generate(first_run.id)
    manager = world.workflows(world.manager)
    [phase] = [
        p
        for p in world.session.scalars(
            select(WorkflowPhase).where(WorkflowPhase.workflow_id == first.workflow_id)
        )
        if p.position == 1
    ]
    manager.edit(
        world.project_id,
        first.workflow_id,  # type: ignore[arg-type]
        UpdatePhase(phase.key, {"description": "Kickoff with the regulator present (synthetic)."}),
        reason="Regulator asked to attend (synthetic).",
    )
    second_run = world.p9.runner().override(
        actor=world.analyst,
        project_id=world.project_id,
        run_id=first_run.id,
        factor="stakeholder_availability",
        new_score=3,
        reason="A second product owner joined (synthetic).",
        role=Role.ANALYST,
    )
    new_run = world.p9.service().get(world.project_id, second_run.sdlc_run_id)
    assert new_run is not None
    world.approve_g6(new_run)
    world.session.refresh(first_run)
    assert first_run.status is SdlcRunStatus.SUPERSEDED
    assert world.generate(first_run.id).workflow_id is None, "a superseded selection is refused"
    second = world.generate(new_run.id)
    assert second.workflow_id is not None and second.workflow_id != first.workflow_id
    old = world.session.get(Workflow, first.workflow_id)
    new = world.session.get(Workflow, second.workflow_id)
    assert old is not None and new is not None
    assert old.status is WorkflowStatus.SUPERSEDED and new.supersedes_workflow_id == old.id
    assert count(world.session, WorkflowChange, workflow_id=old.id) == 1, "its log is kept"
    with pytest.raises(WorkflowError, match="superseded"):
        manager.edit(
            world.project_id,
            old.id,
            UpdatePhase(phase.key, {"name": "x"}),
            reason="try (synthetic)",
        )
    assert len(events(world, AuditEventType.WORKFLOW_SUPERSEDED)) == 1


def test_a_high_risk_awaiting_g8_refuses_generation(world: P10World) -> None:
    run = world.select()
    template = next(
        r
        for r in world.session.scalars(select(Risk).where(Risk.project_id == world.project_id))
        if str(r.severity) == "high" and r.requirement_version_id is not None
    )
    columns = {c.key: getattr(template, c.key) for c in Risk.__table__.columns}
    columns.update(
        id=uuid.uuid4(),
        title="A late HIGH risk still under review (synthetic)",
        title_key="a late high risk still under review synthetic",
        status="under_review",
        approval_task_id=None,
        decided_by=None,
        decided_at=None,
        decision_rationale=None,
    )
    world.session.add(Risk(**columns))
    world.session.flush()
    summary = world.generate(run.id)
    assert summary.workflow_id is None
    # Two layers refuse it: P9's authority check on the approved scope (an unreviewed
    # high-severity risk blocks it) and, behind it, the workflow's own G8 check.
    [finding] = summary.workflow_findings
    assert finding["code"] in {"SOURCES_NOT_GOVERNED", "G8_PENDING"}
    assert "risk" in finding["message"].lower()
    assert count(world.session, Workflow, project_id=world.project_id) == 0


# --- editing -----------------------------------------------------------------------------


def _generated(world: P10World) -> Workflow:
    run = world.select()
    summary = world.generate(run.id)
    workflow = world.session.get(Workflow, summary.workflow_id)
    assert workflow is not None
    return workflow


def test_a_project_manager_edit_is_logged_and_the_generated_workflow_is_kept(
    world: P10World,
) -> None:
    workflow = _generated(world)
    generated = dict(workflow.generated_structure)
    manager = world.workflows(world.manager)
    phase = next(p for p in manager.view(world.project_id, workflow).phases if p.position == 2)
    before = list(phase.exit_criteria)
    change = manager.edit(
        world.project_id,
        workflow.id,
        UpdatePhase(phase.key, {"exit_criteria": ["System design reviewed with the regulator"]}),
        reason="The regulator reviews the system design (synthetic marker qzxw-4410).",
    )
    assert change.revision == 2 and change.operation == "update" and change.element_id == phase.id
    assert change.role_exercised == "project_manager" and change.actor_id == world.manager.actor_id
    assert change.changes["exit_criteria"]["after"] == ["System design reviewed with the regulator"]
    assert change.changes["exit_criteria"]["before"] == before
    world.session.refresh(workflow)
    assert workflow.revision == 2 and workflow.content_hash == change.content_hash_after
    assert (
        workflow.generated_structure == generated
        and workflow.generated_hash != workflow.content_hash
    )
    [edited] = events(world, AuditEventType.WORKFLOW_EDITED)
    assert edited.payload["fields"] == ["exit_criteria"] and edited.payload["revision"] == 2
    assert "qzxw" not in str(edited.payload), "the audit event references; it does not copy text"
    markdown = manager.export(world.project_id, workflow.id, ArtifactFormat.MARKDOWN).data.decode()
    assert "System design reviewed with the regulator" in markdown
    assert "qzxw-4410" in markdown, "the export's change log carries the reason"


def test_only_the_project_manager_edits_and_every_edit_needs_a_reason(world: P10World) -> None:
    workflow = _generated(world)
    phase = world.workflows().view(world.project_id, workflow).phases[0]
    with pytest.raises(AuthorizationError):
        world.workflows(world.analyst).edit(
            world.project_id, workflow.id, UpdatePhase(phase.key, {"name": "x"}), reason="r"
        )
    with pytest.raises(WorkflowError, match="reason"):
        world.workflows(world.manager).edit(
            world.project_id, workflow.id, UpdatePhase(phase.key, {"name": "x"}), reason="  "
        )
    assert count(world.session, WorkflowChange, workflow_id=workflow.id) == 0


def test_mandatory_elements_and_provenance_survive_every_edit(world: P10World) -> None:
    workflow = _generated(world)
    manager = world.workflows(world.manager)
    view = manager.view(world.project_id, workflow)
    treatment = next(a for a in view.activities if a.kind == "risk_treatment")
    with pytest.raises(WorkflowError, match="cannot be removed"):
        manager.edit(world.project_id, workflow.id, RemoveActivity(treatment.key), reason="r")
    ready = next(g for g in view.gates if g.kind == "production_readiness")
    with pytest.raises(WorkflowError, match="GATE_APPROVERS_MISSING"):
        manager.edit(
            world.project_id, workflow.id, UpdateGate(ready.key, {"approver_roles": []}), reason="r"
        )
    world.session.refresh(workflow)
    assert workflow.revision == 1, "a refused edit changes nothing"
    reworded = manager.edit(
        world.project_id,
        workflow.id,
        UpdateGate(ready.key, {"name": "Go-live approval"}),
        reason="House terminology (synthetic).",
    )
    assert reworded.revision == 2
    after = manager.view(world.project_id, workflow)
    gate = next(g for g in after.plan.gates() if g[1].key == ready.key)[1]
    assert (
        gate.name == "Go-live approval" and gate.kind == "production_readiness" and gate.mandatory
    )


def test_a_manual_activity_can_be_added_and_removed(world: P10World) -> None:
    workflow = _generated(world)
    manager = world.workflows(world.manager)
    phase = manager.view(world.project_id, workflow).phases[0]
    added = manager.edit(
        world.project_id,
        workflow.id,
        AddActivity(phase.key, "Regulator walkthrough", "", ("compliance_officer",), ("Minutes",)),
        reason="Requested by the regulator (synthetic).",
    )
    assert added.operation == "add"
    row = world.session.get(WorkflowActivity, added.element_id)
    assert row is not None and row.kind == "manual" and row.origin == "manual" and not row.mandatory
    removed = manager.edit(
        world.project_id, workflow.id, RemoveActivity(row.key), reason="No longer needed."
    )
    assert removed.operation == "remove" and removed.revision == 3
    assert world.session.get(WorkflowActivity, added.element_id) is None
    assert [c.revision for c in manager.changes(world.project_id, workflow.id)] == [2, 3]


# --- export -----------------------------------------------------------------------------


def test_markdown_and_docx_exports_carry_the_whole_workflow(world: P10World) -> None:
    from reqpilot.artifacts.docx import docx_text

    workflow = _generated(world)
    service = world.workflows()
    view = service.view(world.project_id, workflow)
    markdown = service.export(world.project_id, workflow.id, ArtifactFormat.MARKDOWN)
    assert markdown.media_type.startswith("text/markdown") and markdown.filename.endswith(".md")
    text = markdown.data.decode()
    for phase in view.plan.phases:
        assert phase.name in text
    for _p, gate in view.plan.gates():
        assert gate.name in text
    assert "not ReqPilot gates G1-G8" in text and "Open items" in text
    docx = service.export(world.project_id, workflow.id, ArtifactFormat.DOCX)
    assert docx.filename.endswith(".docx")
    words = docx_text(docx.data)
    assert "Production-readiness approval" in words
    assert all(g.name in words for _p, g in view.plan.gates())
    assert docx.data == service.export(world.project_id, workflow.id, ArtifactFormat.DOCX).data
    with pytest.raises(WorkflowError):
        service.export(world.project_id, workflow.id, ArtifactFormat.CSV)
    exported = events(world, AuditEventType.WORKFLOW_EXPORTED)
    assert [e.payload["format"] for e in exported] == ["markdown", "docx", "docx"]
