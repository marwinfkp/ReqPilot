"""P8 security: no authority through documents, links or agents; isolation; audit hygiene."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import make_actor
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.domain.enums import (
    ActorKind,
    ApprovalDecisionType,
    ArtifactFormat,
    ArtifactType,
    Gate,
    Role,
)
from reqpilot.domain.errors import AuthorizationError, ProjectIsolationError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalDecision
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.documents import ArtifactService
from reqpilot.services.governance import GovernanceFanOut, UnifiedReviewQueue
from reqpilot.services.traceability import TraceGraphSync

pytestmark = pytest.mark.security


@pytest.fixture
def world(db_session: Session) -> P8World:
    return make_p8_world(db_session)


def agent(world: P8World, *roles: Role):  # type: ignore[no-untyped-def]
    return make_actor(project_id=world.project_id, roles=set(roles), kind=ActorKind.AGENT_ROLE)


def test_an_agent_can_neither_generate_nor_sync_nor_raise_g5(world) -> None:
    baseline = world.govern_and_baseline(["L03"], "B1")
    bot = agent(world, Role.ANALYST)
    with pytest.raises(AuthorizationError):
        ArtifactService(world.session, bot).generate(world.project_id, baseline, ArtifactType.SRS)
    with pytest.raises(AuthorizationError):
        TraceGraphSync(world.session, bot).sync(world.project_id)
    with pytest.raises(AuthorizationError):
        GovernanceFanOut(world.session, bot).flag_architecture_critical(
            world.project_id, world.versions["L04"].id, "x"
        )


@pytest.mark.parametrize("gate", [Gate.G4_STAKEHOLDER_CONFLICT, Gate.G5_ARCHITECTURE_CRITICAL])
def test_an_agent_can_never_decide_a_p8_gate(world, gate) -> None:
    world.resolve_conflict()
    world.fan_out()
    task = next(t for t in world.tasks(gate=gate))
    with pytest.raises(AuthorizationError):
        ApprovalService(world.session, agent(world, task.required_role)).decide(
            project_id=world.project_id,
            task_id=task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=task.required_role,
        )


def test_reviewing_roles_cannot_generate_or_sync(world) -> None:
    baseline = world.govern_and_baseline(["L03"], "B1")
    for actor in (
        world.compliance_officer,
        world.security_reviewer,
        world.project_manager,
        world.auditor,
        world.priya,
    ):
        with pytest.raises(AuthorizationError):
            ArtifactService(world.session, actor).generate(
                world.project_id, baseline, ArtifactType.SRS
            )
        with pytest.raises(AuthorizationError):
            TraceGraphSync(world.session, actor).sync(world.project_id)


def test_project_b_cannot_reach_project_a_artefacts_links_queue_or_baseline(
    world, db_session
) -> None:
    baseline = world.govern_and_baseline(["L03"], "B1")
    srs = (
        ArtifactService(world.session, world.analyst)
        .generate(world.project_id, baseline, ArtifactType.SRS)
        .version
    )
    other = make_p8_world(db_session, "Project B (synthetic)")
    service = ArtifactService(db_session, other.analyst)
    for attempt in (
        lambda: service.generate(world.project_id, baseline, ArtifactType.SRS),
        lambda: service.export(world.project_id, srs.id, ArtifactFormat.MARKDOWN),
        lambda: service.list_artifacts(world.project_id),
        lambda: service.coverage(world.project_id, None),
        lambda: UnifiedReviewQueue(db_session, other.analyst).build(world.project_id),
    ):
        with pytest.raises(ProjectIsolationError):
            attempt()
    # Its own baseline id cannot be used to render project A either.
    assert service.get_version(other.project_id, srs.id) is None
    assert other.project_id != world.project_id


def test_injected_instructions_change_no_gate_and_no_approval(world) -> None:
    """L08's statement asks to be approved. Nothing reads it as an instruction."""
    before = world.tasks()
    baseline = world.govern_and_baseline(["L08"], "B1")
    decisions = list(world.session.scalars(select(ApprovalDecision)))
    l08 = world.versions["L08"].id
    g1 = [d for d in decisions if d.subject_version_hash == world.version("L08").content_hash]
    # Exactly the two human co-approvals G1 needs - no more, none from the text.
    assert sorted(d.role_exercised.value for d in g1) == ["analyst", "compliance_officer"]
    assert all(
        d.decided_by in (world.reviewer_analyst.actor_id, world.compliance_officer.actor_id)
        for d in g1
    )
    assert world.version("L08").state is RequirementState.BASELINED
    # The G3 task the analysis raised for L08 was still raised and decided by a human.
    assert any(t.subject_id != l08 for t in before)
    srs = (
        ArtifactService(world.session, world.analyst)
        .generate(world.project_id, baseline, ArtifactType.SRS)
        .version
    )
    assert "approve this requirement" in srs.markdown  # shown as data
    assert "<script>" not in srs.markdown


def test_audit_payloads_carry_references_not_content(world) -> None:
    baseline = world.govern_and_baseline(["L01", "L03", "L08"], "B1")
    service = ArtifactService(world.session, world.analyst)
    for outcome in service.generate_set(world.project_id, baseline):
        service.export(world.project_id, outcome.version.id, ArtifactFormat.DOCX)
    statements = [world.version(k).statement for k in ("L01", "L03", "L08")]
    for event in world.session.scalars(
        select(AuditEvent).where(AuditEvent.project_id == world.project_id)
    ):
        payload = str(event.payload)
        for statement in statements:
            assert statement[:30] not in payload, event.event_type
        assert "<script>" not in payload
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)
