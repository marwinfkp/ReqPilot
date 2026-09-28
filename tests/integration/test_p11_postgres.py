"""P11 on PostgreSQL: the purge trigger, the tombstone and approval guards, and the
new columns and enum values, against the migrated schema (skipped without a database).

Each test runs inside a transaction that is rolled back (``pg_session``), so the
purge exercised here deletes nothing that survives the test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.conftest import postgres_url
from tests.p8_helpers import P8World, make_p8_world
from tests.p11_helpers import identifier_text, leaked

from reqpilot.domain.enums import AuditEventType, DataSensitivity, MaskingStatus
from reqpilot.domain.ids import ProjectId, thread_id_for
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.guardrails import MaskingMapEntry, ProjectPurge
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.runs import GraphRun
from reqpilot.services.audit import AuditService
from reqpilot.services.guardrails.deletion import ProjectDeletionService
from reqpilot.services.guardrails.sessions import AuthSessionService

pytestmark = pytest.mark.integration

NAME = "P8 loan origination (synthetic)"


@pytest.fixture
def world(pg_session: Session) -> P8World:
    return make_p8_world(pg_session)


def _refused(session: Session, sql: str, **params: object) -> None:
    """The statement is refused by the database; the test transaction survives it."""
    with pytest.raises(DBAPIError), session.begin_nested():
        session.execute(text(sql), params)


def _ensure_checkpoint_tables() -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    from reqpilot.graph.builder import _psycopg_url

    url = postgres_url()
    assert url is not None
    with PostgresSaver.from_conn_string(_psycopg_url(url)) as saver:
        saver.setup()


def test_the_purge_trigger_removes_one_projects_content_and_its_checkpoints(
    world: P8World, pg_session: Session
) -> None:
    _ensure_checkpoint_tables()
    other = make_p8_world(pg_session, "Another lender (synthetic)")
    run_ids = list(
        pg_session.scalars(select(GraphRun.id).where(GraphRun.project_id == world.project_id))
    )
    other_run = pg_session.scalars(
        select(GraphRun.id).where(GraphRun.project_id == other.project_id)
    ).first()
    for thread in [thread_id_for(r) for r in run_ids] + [thread_id_for(other_run)]:  # type: ignore[arg-type]
        pg_session.execute(
            text(
                "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) "
                "VALUES (:t, :c, '{}'::jsonb)"
            ),
            {"t": thread, "c": uuid.uuid4().hex},
        )
    service = ProjectDeletionService(pg_session, world.project_manager)
    before_other = service.content_counts(ProjectId(other.project_id))
    events_before = len(AuditService(pg_session).list_for_project(world.project_id))

    receipt = service.delete(world.project_id, confirm_name=NAME)

    assert receipt.total_removed > 100 and receipt.checkpoint_threads == len(run_ids)
    assert sum(service.content_counts(ProjectId(world.project_id)).values()) == 0
    assert service.content_counts(ProjectId(other.project_id)) == before_other
    remaining = (
        pg_session.execute(
            text("SELECT thread_id FROM checkpoints WHERE thread_id = ANY(:t)"),
            {"t": [thread_id_for(r) for r in run_ids] + [thread_id_for(other_run)]},  # type: ignore[arg-type]
        )
        .scalars()
        .all()
    )
    assert remaining == [thread_id_for(other_run)], "only the deleted project's threads went"  # type: ignore[arg-type]
    events = AuditService(pg_session).list_for_project(world.project_id)
    assert len(events) == events_before + 1
    assert events[-1].event_type is AuditEventType.PROJECT_DELETED
    assert AuditService(pg_session).verify_project_chain(world.project_id) == (True, None)
    assert pg_session.scalar(select(func.count()).select_from(ProjectPurge)) >= 1


def test_the_guards_still_refuse_direct_deletes_and_the_purge_needs_a_tombstone(
    world: P8World, pg_session: Session
) -> None:
    project_id = str(world.project_id)
    # Tables with an earlier phase's guard (and P11's task guard) that hold rows here -
    # a trigger fires per row, so an empty table would prove nothing.
    for table in (
        "source_chunk",
        "requirement_classification",
        "compliance_mapping",
        "risk",
        "approval_task",
    ):
        count = pg_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE project_id = :p"),
            {"p": project_id},
        ).scalar_one()
        assert count > 0, table
        _refused(pg_session, f"DELETE FROM {table} WHERE project_id = :p", p=project_id)
    _refused(pg_session, "DELETE FROM audit_event WHERE project_id = :p", p=project_id)
    _refused(
        pg_session,
        "INSERT INTO project_purge (id, project_id, requested_by, requested_at) "
        "VALUES (:i, :p, :u, now())",
        i=str(uuid.uuid4()),
        p=project_id,
        u=str(world.project_manager.actor_id),
    )
    assert pg_session.get(Project, world.project_id).deleted_at is None  # type: ignore[union-attr]


def test_a_tombstone_is_final_in_the_database(world: P8World, pg_session: Session) -> None:
    ProjectDeletionService(pg_session, world.project_manager).delete(
        world.project_id, confirm_name=NAME
    )
    _refused(
        pg_session, "UPDATE project SET name = 'resurrected' WHERE id = :p", p=str(world.project_id)
    )
    _refused(
        pg_session, "UPDATE project SET deleted_at = NULL WHERE id = :p", p=str(world.project_id)
    )
    _refused(pg_session, "DELETE FROM project_purge WHERE project_id = :p", p=str(world.project_id))


def test_the_approval_records_cannot_be_forged_in_the_database(
    world: P8World, pg_session: Session
) -> None:
    task = pg_session.scalars(
        select(ApprovalTask).where(
            ApprovalTask.project_id == world.project_id, ApprovalTask.status == "OPEN"
        )
    ).first()
    assert task is not None
    t = str(task.id)
    _refused(pg_session, "UPDATE approval_task SET status = 'APPROVED' WHERE id = :t", t=t)
    _refused(pg_session, "UPDATE approval_task SET status = 'REJECTED' WHERE id = :t", t=t)
    _refused(
        pg_session, "UPDATE approval_task SET gate = 'G5_ARCHITECTURE_CRITICAL' WHERE id = :t", t=t
    )
    _refused(pg_session, "DELETE FROM approval_task WHERE id = :t", t=t)
    _refused(
        pg_session,
        "INSERT INTO approval_decision (id, task_id, project_id, decided_by, role_exercised, "
        "decision, subject_version_hash, decided_at) "
        "VALUES (:i, :t, :p, :u, :r, 'APPROVE', :h, now())",
        i=str(uuid.uuid4()),
        t=t,
        p=str(world.project_id),
        u=str(uuid.uuid4()),  # nobody in the project
        r=task.required_role.name,
        h=task.subject_version_hash,
    )
    decided = world.decide(task)
    _refused(
        pg_session,
        "UPDATE approval_decision SET decision = 'REJECT' WHERE id = :d",
        d=str(decided.decision.id),
    )
    _refused(pg_session, "DELETE FROM approval_decision WHERE id = :d", d=str(decided.decision.id))
    _refused(pg_session, "UPDATE approval_task SET status = 'OPEN' WHERE id = :t", t=t)


def test_masking_columns_enum_values_and_sessions_on_postgres(
    world: P8World, pg_session: Session
) -> None:
    from tests.p3_helpers import ingest

    document = ingest(
        pg_session,
        world.analyst,
        world.project_id,
        identifier_text(),
        title="Identifiers (synthetic)",
        sensitivity=DataSensitivity.UNCLASSIFIED,
    )
    assert document.masking_status is MaskingStatus.MASKED and leaked(document.text) == []
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(MaskingMapEntry)
            .where(MaskingMapEntry.source_id == document.id)
        )
        == 13
    )
    values = set(
        pg_session.execute(
            text("SELECT unnest(enum_range(NULL::audit_event_type_enum))::text")
        ).scalars()
    )
    assert {"INJECTION_SUSPECTED", "AUTH_SESSION_ISSUED", "AUTH_SESSION_REVOKED"} <= values
    column = pg_session.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'utterance' AND column_name = 'masking_status'"
        )
    ).scalar_one()
    assert "NOT_MASKED" in column, "rows recorded before P11 say they were not masked"
    issued = AuthSessionService(pg_session).issue(world.analyst.actor_id)
    assert AuthSessionService(pg_session).resolve(issued.token).id == world.analyst.actor_id
