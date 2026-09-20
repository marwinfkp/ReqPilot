"""Database and audit-service integration tests (ADR-003, ADR-010).

Runs against in-memory SQLite so the suite needs no Docker. The behaviour under
test - model mappings, relationships, hash chaining through a real session,
transaction rollback - is dialect-independent.

The PostgreSQL-specific guarantees (the append-only trigger and the ``REVOKE``)
are tested separately and skip when no PostgreSQL is configured; see
``test_postgres_specific.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ActorKind,
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    GraphRunStatus,
    Role,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.ids import new_graph_run_id, thread_id_for
from reqpilot.domain.models import AgentRun, AuditEvent, GraphRun, Project, ProjectMember, User
from reqpilot.services.audit import AuditService

pytestmark = pytest.mark.integration


def make_project(session: Session, name: str = "Loan Origination") -> Project:
    project = Project(name=name, domain="loan_origination")
    session.add(project)
    session.flush()
    return project


def make_user(session: Session, email: str = "analyst@example.test") -> User:
    user = User(email=email, display_name="Test Analyst")
    session.add(user)
    session.flush()
    return user


# --- schema and mappings -------------------------------------------------


def test_schema_creates_and_core_tables_exist(sqlite_engine) -> None:
    names = set(sqlite_engine.dialect.get_table_names(sqlite_engine.connect()))
    assert {
        "app_user",
        "project",
        "project_member",
        "audit_event",
        "graph_run",
        "agent_run",
    } <= names


def test_project_membership_round_trips(db_session: Session) -> None:
    project = make_project(db_session)
    user = make_user(db_session)
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.ANALYST))
    db_session.flush()

    loaded = db_session.scalars(
        select(ProjectMember).where(ProjectMember.project_id == project.id)
    ).all()
    assert len(loaded) == 1
    assert loaded[0].role is Role.ANALYST


def test_a_user_may_hold_several_roles_in_one_project(db_session: Session) -> None:
    """Realistic for a student team; the unique constraint covers the role too."""
    project = make_project(db_session)
    user = make_user(db_session)
    db_session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id, role=Role.ANALYST),
            ProjectMember(project_id=project.id, user_id=user.id, role=Role.PROJECT_MANAGER),
        ]
    )
    db_session.flush()
    count = db_session.scalar(
        select(func.count()).select_from(ProjectMember).where(ProjectMember.user_id == user.id)
    )
    assert count == 2


def test_graph_run_thread_id_follows_the_convention(db_session: Session) -> None:
    project = make_project(db_session)
    run_id = new_graph_run_id()
    run = GraphRun(
        id=run_id,
        project_id=project.id,
        graph_name="smoke",
        thread_id=thread_id_for(run_id),
        status=GraphRunStatus.PENDING,
        failure_count=0,
    )
    db_session.add(run)
    db_session.flush()
    assert run.thread_id == str(run.id)


def test_agent_run_links_to_its_graph_run(db_session: Session) -> None:
    project = make_project(db_session)
    run_id = new_graph_run_id()
    run = GraphRun(
        id=run_id,
        project_id=project.id,
        graph_name="smoke",
        thread_id=thread_id_for(run_id),
        status=GraphRunStatus.RUNNING,
        failure_count=0,
    )
    db_session.add(run)
    db_session.flush()

    db_session.add(
        AgentRun(
            graph_run_id=run.id,
            node="start",
            role=AgentRole.COORDINATOR,
            input_refs={},
            output_refs={},
            evidence_ids=[],
            status=AgentRunStatus.OK,
        )
    )
    db_session.flush()
    db_session.refresh(run)
    assert len(run.agent_runs) == 1


# --- transactions --------------------------------------------------------


def test_rollback_discards_writes(db_session: Session) -> None:
    make_project(db_session, "Discarded")
    db_session.rollback()
    count = db_session.scalar(select(func.count()).select_from(Project))
    assert count == 0


# --- audit service -------------------------------------------------------


def test_append_creates_a_first_link_with_no_predecessor(db_session: Session) -> None:
    project = make_project(db_session)
    audit = AuditService(db_session)

    event = audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="user-1",
        project_id=project.id,
        subject_type="project",
        subject_id=str(project.id),
    )
    assert event.prev_hash is None
    assert len(event.row_hash) == 64


def test_each_event_references_its_predecessor(db_session: Session) -> None:
    """append -> compute hash -> persist -> next event references previous hash."""
    project = make_project(db_session)
    audit = AuditService(db_session)

    first = audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="user-1",
        project_id=project.id,
    )
    second = audit.append(
        event_type=AuditEventType.MEMBER_ADDED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="user-1",
        project_id=project.id,
    )
    third = audit.append(
        event_type=AuditEventType.RUN_STARTED,
        actor_kind=ActorKind.SYSTEM,
        actor_ref="system",
        project_id=project.id,
    )

    assert second.prev_hash == first.row_hash
    assert third.prev_hash == second.row_hash


def test_chain_verifies_through_the_service(db_session: Session) -> None:
    project = make_project(db_session)
    audit = AuditService(db_session)
    for _ in range(5):
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.AGENT_ROLE,
            actor_ref=str(AgentRole.COORDINATOR),
            project_id=project.id,
        )
    ok, index = audit.verify_project_chain(project.id)
    assert ok is True
    assert index is None


def test_chains_are_independent_per_project(db_session: Session) -> None:
    """One project's events must not chain into another's."""
    a = make_project(db_session, "A")
    b = make_project(db_session, "B")
    audit = AuditService(db_session)

    audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="u",
        project_id=a.id,
    )
    first_b = audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="u",
        project_id=b.id,
    )
    assert first_b.prev_hash is None

    assert audit.verify_project_chain(a.id)[0] is True
    assert audit.verify_project_chain(b.id)[0] is True


def test_tampering_with_a_persisted_row_is_detected(db_session: Session) -> None:
    """The ORM cannot stop a determined writer; the chain still catches it."""
    project = make_project(db_session)
    audit = AuditService(db_session)
    for _ in range(3):
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.SYSTEM,
            actor_ref="system",
            project_id=project.id,
        )
    db_session.flush()

    events = audit.list_for_project(project.id)
    events[1].payload = {"ref": "tampered"}
    db_session.flush()

    ok, index = audit.verify_project_chain(project.id)
    assert ok is False
    assert index == 1


def test_audit_service_exposes_no_mutation_method() -> None:
    """Layer 1 of immutability: there is no update or delete path at all."""
    surface = {name for name in dir(AuditService) if not name.startswith("_")}
    assert not {"update", "delete", "remove", "edit", "modify"} & surface


def test_explicit_mutation_guard_raises() -> None:
    with pytest.raises(ImmutableRecordError, match="append-only"):
        AuditService.forbid_mutation()


def test_payload_carrying_content_is_rejected(db_session: Session) -> None:
    """Audit payloads carry references only, never content (architecture O.1)."""
    project = make_project(db_session)
    audit = AuditService(db_session)
    with pytest.raises(ValueError, match="references only"):
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.AGENT_ROLE,
            actor_ref="extraction",
            project_id=project.id,
            payload={"statement": "The system shall ..."},
        )


def test_payload_carrying_a_secret_is_rejected(db_session: Session) -> None:
    project = make_project(db_session)
    audit = AuditService(db_session)
    with pytest.raises(ValueError, match="references only"):
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.SYSTEM,
            actor_ref="system",
            project_id=project.id,
            payload={"api_key": "sk-123"},
        )


def test_reference_payload_is_accepted(db_session: Session) -> None:
    project = make_project(db_session)
    audit = AuditService(db_session)
    event = audit.append(
        event_type=AuditEventType.NODE_COMPLETED,
        actor_kind=ActorKind.AGENT_ROLE,
        actor_ref="extraction",
        project_id=project.id,
        payload={"requirement_ids": ["a", "b"], "count": 2},
    )
    assert event.payload["count"] == 2


def test_sequence_numbers_are_monotonic_per_project(db_session: Session) -> None:
    """Chain order must not depend on wall-clock resolution.

    Several appends inside one transaction can share a timestamp on a
    coarse-resolution clock, so position is an explicit sequence number.
    """
    project = make_project(db_session)
    audit = AuditService(db_session)
    events = [
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.SYSTEM,
            actor_ref="system",
            project_id=project.id,
        )
        for _ in range(6)
    ]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6]


def test_sequences_restart_per_project(db_session: Session) -> None:
    a = make_project(db_session, "A")
    b = make_project(db_session, "B")
    audit = AuditService(db_session)

    audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="u",
        project_id=a.id,
    )
    first_b = audit.append(
        event_type=AuditEventType.PROJECT_CREATED,
        actor_kind=ActorKind.HUMAN,
        actor_ref="u",
        project_id=b.id,
    )
    assert first_b.seq == 1


def test_rapid_appends_still_verify(db_session: Session) -> None:
    """Regression: 20 appends in a tight loop must produce a verifiable chain."""
    project = make_project(db_session)
    audit = AuditService(db_session)
    for _ in range(20):
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.SYSTEM,
            actor_ref="system",
            project_id=project.id,
        )
    assert audit.verify_project_chain(project.id) == (True, None)


def test_events_are_listed_in_chain_order(db_session: Session) -> None:
    project = make_project(db_session)
    audit = AuditService(db_session)
    created = [
        audit.append(
            event_type=AuditEventType.NODE_COMPLETED,
            actor_kind=ActorKind.SYSTEM,
            actor_ref="system",
            project_id=project.id,
        )
        for _ in range(4)
    ]
    listed = audit.list_for_project(project.id)
    assert [e.id for e in listed] == [e.id for e in created]


def test_audit_event_model_has_no_updated_at() -> None:
    """An append-only row has no concept of being updated."""
    assert not hasattr(AuditEvent, "updated_at")
