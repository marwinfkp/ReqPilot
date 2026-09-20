"""Database engine, session factory and health check (architecture ADR-003).

Lives in the repository layer because that is the layer that owns data access.
No domain module imports this - the domain stays usable without a database,
which is what lets the policy and hashing logic be unit-tested offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from reqpilot.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


#: Seconds to wait for a TCP connection before giving up. Deliberately short:
#: a health check that hangs cannot report unhealthiness, and a developer with
#: no database running should get an answer immediately rather than a stall.
CONNECT_TIMEOUT_SECONDS = 5


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        kwargs: dict[str, object] = {
            "pool_pre_ping": True,  # a stale pooled connection is not a query error
            "future": True,
        }
        # SQLite takes neither a pool size nor a connect timeout.
        if not settings.database_url.startswith("sqlite"):
            kwargs["pool_size"] = settings.database_pool_size
            kwargs["connect_args"] = {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
        _engine = create_engine(settings.database_url, **kwargs)  # type: ignore[arg-type]
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings), expire_on_commit=False, future=True
        )
    return _session_factory


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """A transactional scope.

    Commits on success, rolls back on any exception. The architecture's
    transaction rule (T.1) is that a node's domain writes and its audit events
    share one transaction, so that an audited action always happened and an
    unaudited one never did - this is the helper that makes that the easy path.
    """
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_health(settings: Settings | None = None) -> dict[str, object]:
    """Return a health report for the database connection.

    Never raises: a health check that raises cannot report unhealthiness.
    """
    report: dict[str, object] = {"connected": False, "pgvector": False, "error": None}
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            report["connected"] = True
            # pgvector is required by the retrieval subsystem in a later roadmap
            # phase. P0 only reports whether the extension is present.
            row = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
            report["pgvector"] = row is not None
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def reset_engine() -> None:
    """Dispose of the cached engine and session factory. For tests only."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
