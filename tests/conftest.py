"""Shared test fixtures.

Design rule for the whole suite (ADR-012): the ``unit``, ``integration``,
``workflow`` and ``security`` marks must run with **zero external API calls**.
The ``llm`` mark is opt-in and excluded by ``addopts``.

Database-backed tests use SQLite in memory where the behaviour under test is
dialect-independent, and skip cleanly when a real PostgreSQL instance is not
reachable. That keeps the suite runnable on a machine with no Docker while
still exercising the real engine when one is available.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from reqpilot.config import AppEnv, LLMProvider, Settings
from reqpilot.domain.enums import ActorKind, Role
from reqpilot.domain.ids import ActorId, ProjectId, new_project_id, new_uuid
from reqpilot.domain.models import Base
from reqpilot.domain.policy import Actor


@pytest.fixture
def settings() -> Settings:
    """Test settings. No credentials, stub provider, in-memory checkpointing."""
    return Settings(
        REQPILOT_ENV=AppEnv.TEST,
        REQPILOT_SECRET_KEY="test-secret-not-real",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        LLM_PROVIDER=LLMProvider.STUB,
        LANGGRAPH_CHECKPOINT_BACKEND="memory",
    )


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """An in-memory SQLite engine with the full schema created.

    Used for behaviour that does not depend on PostgreSQL semantics - the audit
    hash chain, model mappings, relationships. Foreign keys are enabled
    explicitly because SQLite leaves them off by default, which would silently
    weaken the isolation tests.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=sqlite_engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def project_id() -> ProjectId:
    return new_project_id()


@pytest.fixture
def other_project_id() -> ProjectId:
    return new_project_id()


def make_actor(
    *,
    project_id: ProjectId,
    roles: set[Role],
    kind: ActorKind = ActorKind.HUMAN,
) -> Actor:
    """Build an actor holding ``roles`` in exactly one project."""
    return Actor(
        actor_id=ActorId(new_uuid()),
        kind=kind,
        roles_by_project={project_id: frozenset(roles)},
    )


@pytest.fixture
def analyst(project_id: ProjectId) -> Actor:
    return make_actor(project_id=project_id, roles={Role.ANALYST})


@pytest.fixture
def auditor(project_id: ProjectId) -> Actor:
    return make_actor(project_id=project_id, roles={Role.AUDITOR})


@pytest.fixture
def agent_actor(project_id: ProjectId) -> Actor:
    """A non-human actor - used to prove agent roles can never decide a gate."""
    return make_actor(
        project_id=project_id,
        roles={Role.COMPLIANCE_OFFICER},
        kind=ActorKind.AGENT_ROLE,
    )


def postgres_url() -> str | None:
    """Return a PostgreSQL URL if one is configured for integration tests."""
    return os.environ.get("REQPILOT_TEST_DATABASE_URL")


requires_postgres = pytest.mark.skipif(
    postgres_url() is None,
    reason=(
        "no PostgreSQL available: set REQPILOT_TEST_DATABASE_URL to run "
        "PostgreSQL-specific integration tests (see docs/03-p0-foundations.md)"
    ),
)
