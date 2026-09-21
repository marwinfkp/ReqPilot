"""P1, end to end, on a live PostgreSQL - and the migration that made that possible.

Added in P2, when a live PostgreSQL first became available for verification.
Before this file existed, P1's services had only ever run on SQLite, which
stores enum columns as unconstrained strings. On PostgreSQL two P1 enum types
did not match what the application writes, so no P1 audit event and no approval
task could be inserted (repaired by migration ``0003_p1_postgres_enum_repair``).

Three guards:

1. The P1 requirement-to-baseline workflow on the shared, migrated database.
2. **The P1 exit and governance tests, unchanged**, on PostgreSQL: every one of
   them that uses the database (40 of 41; the other is a pure ``policy.can``
   check) runs here against ``pg_session``. The frozen P1 rules - G1
   co-approval by an Analyst *and* a Compliance Officer, one task per role,
   segregation of duties, exact-version binding, the lifecycle, project
   isolation - are thereby proved on the real engine, not only on SQLite.
3. Migration ``0003`` on a **fresh** database: it reproduces both defects at
   ``0002``, changes nothing but the two enum types (triggers, functions,
   constraints, indexes, grants and columns are byte-identical), reverses, and a
   database migrated from empty to head runs the P1 exit scenario.

Skips without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import inspect
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from tests.security import test_p1_governance as p1_governance
from tests.workflow import test_p1_exit_test as p1_exit
from tests.workflow.test_p1_exit_test import (
    content,
    drive_to_validated,
    make_member,
    make_project,
    task_for,
)

from reqpilot.domain.enums import ApprovalDecisionType, AuditEventType, Gate, Role
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.baseline import BaselineService
from reqpilot.services.requirements import RequirementService

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

P1_REVISION = "0002_p1_requirements_repository"
REPAIR_REVISION = "0003_p1_postgres_enum_repair"

#: The event types P1 raises, which ``0002`` never registered with PostgreSQL.
P1_AUDIT_EVENT_TYPES = {
    "REQUIREMENT_CREATED",
    "REQUIREMENT_VERSION_CREATED",
    "REQUIREMENT_WITHDRAWN",
    "REQUIREMENT_SUPERSEDED",
    "STATE_TRANSITION",
    "APPROVAL_TASK_CREATED",
    "APPROVAL_GRANTED",
    "APPROVAL_REJECTED",
    "APPROVAL_MODIFIED",
    "GATE_PASSED",
    "BASELINE_COMMITTED",
    "BASELINE_MEMBER_ADDED",
}


def build_scenario(session: Session, tag: str = "pg") -> dict:
    """The P1 scenario both suites expect: one project, two analysts (one authors,
    one reviews), a compliance officer and a security reviewer."""
    project = make_project(session)
    return {
        "project_id": ProjectId(project.id),
        "project": project,
        "author": make_member(session, project, Role.ANALYST, f"{tag}-author@example.test"),
        "reviewer": make_member(session, project, Role.ANALYST, f"{tag}-reviewer@example.test"),
        "officer": make_member(
            session, project, Role.COMPLIANCE_OFFICER, f"{tag}-officer@example.test"
        ),
        "security": make_member(
            session, project, Role.SECURITY_REVIEWER, f"{tag}-security@example.test"
        ),
    }


def build_two_projects(session: Session) -> dict:
    """The governance suite's isolation scenario: a requirement in A, a member of B."""
    a = make_project(session, "Project A")
    b = make_project(session, "Project B")
    author_a = make_member(session, a, Role.ANALYST, "pg-aa@example.test")
    member_b = make_member(session, b, Role.ANALYST, "pg-bb@example.test")
    requirement, version = RequirementService(session, author_a).create_requirement(
        project_id=ProjectId(a.id), domain="LOAN", content=content("The system shall isolate.")
    )
    return {
        "a": ProjectId(a.id),
        "b": ProjectId(b.id),
        "author_a": author_a,
        "member_b": member_b,
        "requirement": requirement,
        "version": version,
    }


# ---------------------------------------------------------------------------
# 1. The P1 workflow on the shared database
# ---------------------------------------------------------------------------


def test_requirement_to_co_approved_baseline_on_postgres(pg_session: Session) -> None:
    session = pg_session
    project = make_project(session)
    project_id = ProjectId(project.id)
    author = make_member(session, project, Role.ANALYST, "pg-author@example.test")
    reviewer = make_member(session, project, Role.ANALYST, "pg-reviewer@example.test")
    officer = make_member(session, project, Role.COMPLIANCE_OFFICER, "pg-officer@example.test")

    requirements = RequirementService(session, author)
    _requirement, version = requirements.create_requirement(
        project_id=project_id,
        domain="LOAN",
        content=content("The system shall verify the applicant's identity before disbursal."),
    )
    drive_to_validated(requirements, project_id, version.id)

    tasks = ApprovalService(session, author).submit_versions_for_baseline(
        project_id=project_id, version_ids=[version.id]
    )
    assert len(tasks) == 2, "G1 co-approval raises one task per required role"

    ApprovalService(session, reviewer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    outcome = ApprovalService(session, officer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.COMPLIANCE_OFFICER).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="pg-release-1",
    )

    assert outcome.baseline_id is not None
    session.refresh(version)
    assert version.state is RequirementState.BASELINED
    members = BaselineService(session, officer).members(project_id, outcome.baseline_id)
    assert [m.requirement_version_id for m in members] == [version.id]

    audit = AuditService(session)
    types = {e.event_type for e in audit.list_for_project(project.id)}
    assert {
        AuditEventType.REQUIREMENT_CREATED,
        AuditEventType.STATE_TRANSITION,
        AuditEventType.APPROVAL_TASK_CREATED,
        AuditEventType.APPROVAL_GRANTED,
        AuditEventType.GATE_PASSED,
        AuditEventType.BASELINE_COMMITTED,
    } <= types
    assert audit.verify_project_chain(project.id) == (True, None)


# ---------------------------------------------------------------------------
# 2. The P1 exit and governance suites, unchanged, on PostgreSQL
# ---------------------------------------------------------------------------


#: The fixtures those tests take, each of which this module can supply.
SUPPLIED_FIXTURES = {"db_session", "scenario", "two_projects", "monkeypatch"}


def _p1_database_tests() -> Iterator[object]:
    for module in (p1_exit, p1_governance):
        short = module.__name__.rsplit(".", 1)[-1]
        for name, fn in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            params = set(inspect.signature(fn).parameters)
            if "db_session" in params and params <= SUPPLIED_FIXTURES:
                yield pytest.param(fn, id=f"{short}::{name}")


P1_DATABASE_TESTS = list(_p1_database_tests())


def test_the_p1_suites_are_found() -> None:
    """Guard against the parametrisation silently collecting nothing."""
    names = {p.values[0].__name__ for p in P1_DATABASE_TESTS}  # type: ignore[attr-defined]
    assert {
        "test_p1_exit_scenario",
        "test_analyst_approval_alone_cannot_baseline",
        "test_compliance_approval_alone_cannot_baseline",
        "test_author_cannot_approve_their_own_version",
        "test_the_same_role_cannot_approve_twice",
        "test_rejection_sends_the_version_to_rejected",
        "test_an_approval_cannot_be_reused_against_a_later_version",
        "test_a_rejected_version_cannot_enter_a_baseline",
        "test_a_policy_denial_stops_a_human_approval",
        "test_project_b_member_cannot_read",
        "test_scoped_lookup_of_a_foreign_id_returns_nothing",
    } <= names
    assert len(names) == 40


@pytest.mark.parametrize("p1_test", P1_DATABASE_TESTS)
def test_the_frozen_p1_rules_hold_on_postgres(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch, p1_test: Callable[..., None]
) -> None:
    params = inspect.signature(p1_test).parameters
    kwargs: dict[str, object] = {"db_session": pg_session}
    if "scenario" in params:
        kwargs["scenario"] = build_scenario(pg_session)
    if "two_projects" in params:
        kwargs["two_projects"] = build_two_projects(pg_session)
    if "monkeypatch" in params:
        kwargs["monkeypatch"] = monkeypatch
    p1_test(**kwargs)


# ---------------------------------------------------------------------------
# 3. Migration 0003 on a fresh database
# ---------------------------------------------------------------------------

#: Everything a migration could use to weaken audit immutability (triggers and
#: their functions, grants), RBAC or isolation (constraints, foreign keys,
#: grants), or the P1 lifecycle (check constraints, columns). ``0003`` must leave
#: every one of them exactly as ``0002`` created it.
SCHEMA_SNAPSHOT = {
    "triggers": (
        "SELECT c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal ORDER BY 1, 2"
    ),
    "functions": (
        "SELECT p.proname, md5(p.prosrc) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' ORDER BY 1, 2"
    ),
    "constraints": (
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' ORDER BY 1, 2"
    ),
    "grants": (
        "SELECT table_name, grantee, privilege_type FROM information_schema.table_privileges "
        "WHERE table_schema = 'public' ORDER BY 1, 2, 3"
    ),
    "columns": (
        "SELECT table_name, column_name, udt_name, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
    ),
    "row_security": "SELECT tablename, policyname, qual FROM pg_policies ORDER BY 1, 2",
}


def schema_snapshot(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as conn:
        return {
            name: [tuple(row) for row in conn.execute(text(sql))]
            for name, sql in SCHEMA_SNAPSHOT.items()
        }


def enum_labels(engine: Engine, type_name: str) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text(f"SELECT unnest(enum_range(NULL::{type_name}))::text")))


def alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def fresh_database() -> Iterator[str]:
    """An empty PostgreSQL database of its own, dropped afterwards.

    The shared test database is already at head; proving what ``0003`` does
    needs one that starts empty. Skips if the server refuses ``CREATE DATABASE``.
    """
    url = os.environ.get("REQPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("no PostgreSQL available: set REQPILOT_TEST_DATABASE_URL")
    base = make_url(url)
    name = f"reqpilot_mig_{uuid.uuid4().hex[:12]}"
    server = create_engine(base, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with server.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        server.dispose()
        pytest.skip(f"cannot create a scratch database for the migration test: {exc}")
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with server.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        server.dispose()


def test_0003_repairs_only_the_two_enum_types_and_reverses(fresh_database: str) -> None:
    config = alembic_config(fresh_database)
    engine = create_engine(fresh_database, poolclass=NullPool)
    gate_names = [g.name for g in Gate]
    try:
        # At 0002 both P1 defects are present.
        command.upgrade(config, P1_REVISION)
        at_0002 = schema_snapshot(engine)
        audit_at_0002 = enum_labels(engine, "audit_event_type_enum")
        assert enum_labels(engine, "gate_enum") == [f"G{n}" for n in range(1, 9)]
        assert not P1_AUDIT_EVENT_TYPES & set(audit_at_0002)

        # 0003 repairs both, additively for the audit type, by rename for gates.
        command.upgrade(config, REPAIR_REVISION)
        assert enum_labels(engine, "gate_enum") == gate_names
        audit_at_0003 = enum_labels(engine, "audit_event_type_enum")
        assert audit_at_0003[: len(audit_at_0002)] == audit_at_0002, "nothing removed or renamed"
        assert set(audit_at_0003) == set(audit_at_0002) | P1_AUDIT_EVENT_TYPES
        assert schema_snapshot(engine) == at_0002, "0003 must change nothing but enum labels"

        # It reverses: gate labels go back; the schema is still identical.
        command.downgrade(config, P1_REVISION)
        assert enum_labels(engine, "gate_enum") == [f"G{n}" for n in range(1, 9)]
        assert schema_snapshot(engine) == at_0002

        command.upgrade(config, "head")
        assert enum_labels(engine, "gate_enum") == gate_names
    finally:
        engine.dispose()


def test_a_database_migrated_from_empty_runs_the_p1_exit_scenario(fresh_database: str) -> None:
    command.upgrade(alembic_config(fresh_database), "head")
    engine = create_engine(fresh_database, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            session = Session(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                p1_exit.test_p1_exit_scenario(session, build_scenario(session, "fresh"))
            finally:
                session.close()
                transaction.rollback()
    finally:
        engine.dispose()
