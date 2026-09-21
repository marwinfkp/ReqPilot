"""Persistence, versioning and migration for the requirements repository.

Runs against in-memory SQLite: the behaviour under test - mappings, version
numbering, immutability, transaction semantics, migration content - is
dialect-independent. The PostgreSQL-only baseline trigger is covered in
``test_postgres_specific.py`` and skips visibly without a server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.integration.test_migrations import FOUNDATION_TABLES, alembic_config, sqlite_url
from tests.workflow.test_p1_exit_test import (
    content,
    drive_to_validated,
    make_member,
    make_project,
)

from reqpilot.config import get_settings
from reqpilot.domain.enums import ApprovalDecisionType, Role
from reqpilot.domain.errors import ImmutableVersionError, ReqPilotError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models import (
    ApprovalDecision,
    ApprovalTask,
    Base,
    Baseline,
    BaselineMember,
    Requirement,
    RequirementVersion,
)
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.services.approval import ApprovalService
from reqpilot.services.requirements import RequirementService, assert_version_unmodified

pytestmark = pytest.mark.integration

#: Tables the requirements-repository phase is responsible for.
P1_TABLES = {
    "requirement",
    "requirement_version",
    "approval_task",
    "approval_decision",
    "baseline",
    "baseline_member",
}

#: Tables belonging to later roadmap phases. None may appear yet.
FUTURE_PHASE_TABLES = {
    "knowledge_item",
    "knowledge_chunk",
    "normative_source",
    "control",
    "source_document",
    "source_chunk",
    "utterance",
    "compliance_mapping",
    "risk",
    "risk_mitigation",
    "sdlc_run",
    "sdlc_factor",
    "workflow",
    "artifact",
    "artifact_version",
    "evaluation_run",
}


@pytest.fixture
def scenario(db_session: Session):
    project = make_project(db_session)
    return {
        "project_id": project.id,
        "project": project,
        "author": make_member(db_session, project, Role.ANALYST, "a@example.test"),
        "reviewer": make_member(db_session, project, Role.ANALYST, "r@example.test"),
        "officer": make_member(db_session, project, Role.COMPLIANCE_OFFICER, "c@example.test"),
    }


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    url = sqlite_url(tmp_path / "p1.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    command.upgrade(alembic_config(url), "head")
    from sqlalchemy import create_engine

    engine = create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_migration_adds_the_p1_tables(migrated) -> None:
    present = set(inspect(migrated).get_table_names())
    assert present >= P1_TABLES


def test_migration_leaves_the_foundation_tables_intact(migrated) -> None:
    """P1 is additive: the foundation migration is not rewritten."""
    present = set(inspect(migrated).get_table_names())
    assert present >= FOUNDATION_TABLES


def test_migration_creates_no_future_phase_tables(migrated) -> None:
    present = set(inspect(migrated).get_table_names())
    leaked = present & FUTURE_PHASE_TABLES
    assert not leaked, f"tables from a later roadmap phase appeared: {sorted(leaked)}"


def test_migration_matches_the_orm_models(migrated) -> None:
    inspector = inspect(migrated)
    for table_name, table in Base.metadata.tables.items():
        assert table_name in inspector.get_table_names(), f"{table_name} missing"
        migrated_columns = {c["name"] for c in inspector.get_columns(table_name)}
        model_columns = {c.name for c in table.columns}
        assert model_columns == migrated_columns, (
            f"{table_name} columns differ.\n"
            f"  only in models:    {sorted(model_columns - migrated_columns)}\n"
            f"  only in migration: {sorted(migrated_columns - model_columns)}"
        )


def test_downgrade_removes_only_the_p1_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling P1 back must leave the foundation standing."""
    url = sqlite_url(tmp_path / "down.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "0001_p0_foundation")

    from sqlalchemy import create_engine

    engine = create_engine(url, future=True)
    try:
        present = set(inspect(engine).get_table_names())
        assert not (present & P1_TABLES), "downgrade left P1 tables behind"
        assert present >= FOUNDATION_TABLES, "downgrade damaged the foundation"
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_upgrade_downgrade_upgrade_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = sqlite_url(tmp_path / "round.db")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "0001_p0_foundation")
    command.upgrade(config, "head")

    from sqlalchemy import create_engine

    engine = create_engine(url, future=True)
    try:
        assert set(inspect(engine).get_table_names()) >= P1_TABLES
    finally:
        engine.dispose()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Persistence and versioning
# ---------------------------------------------------------------------------


def test_requirement_and_first_version_persist(db_session: Session, scenario) -> None:
    service = RequirementService(db_session, scenario["author"])
    requirement, version = service.create_requirement(
        project_id=scenario["project_id"],
        domain="LOAN",
        content=content("The system shall persist."),
    )
    db_session.flush()

    loaded = db_session.get(Requirement, requirement.id)
    assert loaded is not None
    assert loaded.human_id == "FR-LOAN-001"
    assert loaded.current_version_id == version.id
    assert loaded.baselined_version_id is None

    stored = db_session.get(RequirementVersion, version.id)
    assert stored is not None
    assert stored.version_no == 1
    assert stored.state is RequirementState.CANDIDATE
    assert len(stored.content_hash) == 64


def test_version_numbers_increment_within_a_requirement(db_session: Session, scenario) -> None:
    service = RequirementService(db_session, scenario["author"])
    requirement, _ = service.create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("v1")
    )
    for i in range(2, 5):
        version = service.create_version(
            project_id=scenario["project_id"],
            requirement_id=requirement.id,
            content=content(f"v{i}"),
            change_reason=f"edit {i}",
        )
        assert version.version_no == i

    history = service.version_history(scenario["project_id"], requirement.id)
    assert [v.version_no for v in history] == [1, 2, 3, 4]


def test_version_numbers_are_unique_per_requirement(db_session: Session, scenario) -> None:
    """The database refuses a duplicate, not just the service."""
    service = RequirementService(db_session, scenario["author"])
    requirement, version = service.create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("v1")
    )
    db_session.add(
        RequirementVersion(
            requirement_id=requirement.id,
            project_id=scenario["project_id"],
            version_no=version.version_no,
            state=RequirementState.CANDIDATE,
            statement="duplicate",
            dependencies=[],
            assumptions=[],
            source_refs=[],
            content_hash="x" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_human_ids_are_unique_within_a_project(db_session: Session, scenario) -> None:
    service = RequirementService(db_session, scenario["author"])
    service.create_requirement(
        project_id=scenario["project_id"],
        domain="LOAN",
        human_id="FR-LOAN-042",
        content=content("first"),
    )
    with pytest.raises(ReqPilotError, match="already used"):
        service.create_requirement(
            project_id=scenario["project_id"],
            domain="LOAN",
            human_id="FR-LOAN-042",
            content=content("second"),
        )


def test_the_same_human_id_may_exist_in_two_projects(db_session: Session, scenario) -> None:
    """Uniqueness is per project: two projects may each have FR-LOAN-001."""
    RequirementService(db_session, scenario["author"]).create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("a")
    )
    other = make_project(db_session, "Other")
    other_author = make_member(db_session, other, Role.ANALYST, "o@example.test")
    requirement, _ = RequirementService(db_session, other_author).create_requirement(
        project_id=other.id, domain="LOAN", content=content("b")
    )
    assert requirement.human_id == "FR-LOAN-001"


def test_non_functional_ids_use_their_own_series(db_session: Session, scenario) -> None:
    service = RequirementService(db_session, scenario["author"])
    functional, _ = service.create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("f")
    )
    non_functional, _ = service.create_requirement(
        project_id=scenario["project_id"],
        domain="LOAN",
        kind=RequirementKind.NON_FUNCTIONAL,
        content=content("nf"),
    )
    assert functional.human_id == "FR-LOAN-001"
    assert non_functional.human_id == "NFR-LOAN-001"


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_an_edit_never_touches_the_predecessor(db_session: Session, scenario) -> None:
    service = RequirementService(db_session, scenario["author"])
    requirement, v1 = service.create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("original wording")
    )
    original_hash, original_statement = v1.content_hash, v1.statement

    service.create_version(
        project_id=scenario["project_id"],
        requirement_id=requirement.id,
        content=content("new wording"),
        change_reason="reword",
    )
    db_session.flush()
    db_session.refresh(v1)

    assert v1.statement == original_statement
    assert v1.content_hash == original_hash
    assert v1.version_no == 1


def test_the_immutability_guard_detects_an_in_place_edit(db_session: Session, scenario) -> None:
    """The guard exists so 'we never edit versions' is provable, not merely said."""
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("original")
    )
    db_session.flush()

    assert_version_unmodified(version)  # clean

    version.statement = "edited in place"
    with pytest.raises(ImmutableVersionError, match="immutable"):
        assert_version_unmodified(version)
    db_session.rollback()


def test_immutable_field_list_covers_the_governed_content(db_session: Session) -> None:
    fields = RequirementVersion.IMMUTABLE_FIELDS
    for name in ("statement", "source_refs", "version_no", "content_hash", "created_by"):
        assert name in fields
    # State and the supersession pointer legitimately change.
    assert "state" not in fields
    assert "superseded_by_id" not in fields


# ---------------------------------------------------------------------------
# Approval and baseline persistence
# ---------------------------------------------------------------------------


def test_approval_task_and_decisions_persist(db_session: Session, scenario) -> None:
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall persist tasks.")
    )
    drive_to_validated(service, project_id, version.id)
    tasks = ApprovalService(db_session, scenario["author"]).submit_versions_for_baseline(
        project_id=project_id, version_ids=[version.id]
    )
    db_session.flush()

    # G1 is a co-approval gate: one task per required role, one shared group.
    assert len(tasks) == 2
    assert {t.required_role for t in tasks} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}
    assert len({t.task_group_id for t in tasks}) == 1

    analyst_task = next(t for t in tasks if t.required_role is Role.ANALYST)
    stored = db_session.get(ApprovalTask, analyst_task.id)
    assert stored is not None
    assert stored.subject_version_hash == version.content_hash
    assert stored.task_group_id is not None
    assert stored.required_role is Role.ANALYST

    ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=project_id,
        task_id=next(t for t in tasks if t.required_role is Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    db_session.flush()

    decisions = list(
        db_session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.task_id
                == next(t for t in tasks if t.required_role is Role.ANALYST).id
            )
        )
    )
    assert len(decisions) == 1
    assert decisions[0].role_exercised is Role.ANALYST
    assert decisions[0].subject_version_hash == version.content_hash


def test_baseline_and_members_persist(db_session: Session, scenario) -> None:
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall be baselined.")
    )
    drive_to_validated(service, project_id, version.id)
    tasks = ApprovalService(db_session, scenario["author"]).submit_versions_for_baseline(
        project_id=project_id, version_ids=[version.id]
    )
    ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=project_id,
        task_id=next(t for t in tasks if t.required_role is Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    outcome = ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=next(t for t in tasks if t.required_role is Role.COMPLIANCE_OFFICER).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="persisted",
    )
    db_session.flush()

    baseline = db_session.get(Baseline, outcome.baseline_id)
    assert baseline is not None
    assert baseline.approval_decision_id == outcome.decision.id

    members = list(
        db_session.scalars(select(BaselineMember).where(BaselineMember.baseline_id == baseline.id))
    )
    assert len(members) == 1
    assert members[0].requirement_version_id == version.id
    assert members[0].version_hash == version.content_hash


def test_a_duplicate_baseline_label_is_refused(db_session: Session, scenario) -> None:
    from reqpilot.services.baseline import BaselineService

    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall label once.")
    )
    drive_to_validated(service, project_id, version.id)
    tasks = ApprovalService(db_session, scenario["author"]).submit_versions_for_baseline(
        project_id=project_id, version_ids=[version.id]
    )
    ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=project_id,
        task_id=next(t for t in tasks if t.required_role is Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    outcome = ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=next(t for t in tasks if t.required_role is Role.COMPLIANCE_OFFICER).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="unique-label",
    )
    with pytest.raises(ReqPilotError, match="already exists"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=project_id,
            label="unique-label",
            version_ids=[version.id],
            approval_decision_id=outcome.decision.id,
        )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


def test_rollback_discards_a_requirement(db_session: Session, scenario) -> None:
    RequirementService(db_session, scenario["author"]).create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("discard me")
    )
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(Requirement)) == 0


def test_rollback_discards_the_audit_events_too(db_session: Session, scenario) -> None:
    """Domain writes and their audit events share a transaction (architecture T.1)."""
    from reqpilot.domain.models import AuditEvent

    RequirementService(db_session, scenario["author"]).create_requirement(
        project_id=scenario["project_id"], domain="LOAN", content=content("discard me too")
    )
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) > 0
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0
