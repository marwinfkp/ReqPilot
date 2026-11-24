"""SQLAlchemy engine/session setup.

PostgreSQL (+pgvector) is the intended database. SQLite also works (used by the
automated tests); in that case vectors are stored as JSON and similarity is
computed in Python.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config

IS_POSTGRES = config.DATABASE_URL.startswith("postgresql")

engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    # SQLite needs this so the background analysis thread can use the DB.
    connect_args={} if IS_POSTGRES else {"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create the pgvector extension (Postgres only) and all tables.

    ReqPilot deliberately uses `create_all` instead of a migration framework to
    keep the project small; it only creates tables that do not exist yet.
    """
    from app import models  # noqa: F401  (register the ORM classes)

    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
