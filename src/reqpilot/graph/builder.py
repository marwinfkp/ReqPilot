"""Graph construction and checkpointer selection (architecture ADR-001, C.7).

Checkpointer selection is a configuration boundary, not a decision made at each
call site: ``memory`` for offline tests and local development, ``postgres`` for
anything durable.

Two lifetimes exist:

* a batch run (``analysis_graph``) starts and ends inside one request, so a
  fresh checkpointer per run is enough;
* an interview (``elicitation_graph``, P4) is one thread that is interrupted at
  every question and resumed by a later request - possibly after a restart. Its
  checkpointer must outlive the request: with ``postgres`` it is the database
  (architecture C.7); with ``memory`` it is one saver shared by the process,
  which is what the offline tests use.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from reqpilot.config import Settings, get_settings
from reqpilot.domain.ids import GraphRunId, thread_id_for

_SHARED_MEMORY_SAVER: Any = None
_POSTGRES_READY: set[str] = set()


def build_checkpointer(settings: Settings | None = None) -> Any:
    """A fresh in-memory checkpointer for a single-request run.

    ``memory`` needs no database, which is what lets the graph smoke tests run
    offline in CI. A durable (``postgres``) checkpointer holds a connection, so
    it is only available as a scope: :func:`checkpointer_scope`.
    """
    settings = settings or get_settings()
    if settings.checkpoint_backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if settings.checkpoint_backend == "postgres":
        raise ValueError(
            "the postgres checkpointer holds a connection; use checkpointer_scope(settings)"
        )
    raise ValueError(f"unknown checkpoint backend {settings.checkpoint_backend!r}")


def shared_memory_checkpointer() -> Any:
    """The process-wide in-memory saver that keeps interview threads between requests."""
    global _SHARED_MEMORY_SAVER
    if _SHARED_MEMORY_SAVER is None:
        from langgraph.checkpoint.memory import MemorySaver

        _SHARED_MEMORY_SAVER = MemorySaver()
    return _SHARED_MEMORY_SAVER


def _psycopg_url(database_url: str) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def checkpointer_scope(
    settings: Settings | None = None, *, shared_memory: bool = False
) -> Iterator[Any]:
    """A checkpointer for the duration of one request.

    ``postgres``: a :class:`PostgresSaver` on its own autocommit connection to
    the application database (architecture C.7), its tables created on first
    use in the process. ``memory``: the process-wide saver when the thread must
    outlive the request (``shared_memory=True``), else a fresh one.
    """
    settings = settings or get_settings()
    if settings.checkpoint_backend == "memory":
        yield shared_memory_checkpointer() if shared_memory else build_checkpointer(settings)
        return
    if settings.checkpoint_backend != "postgres":
        raise ValueError(f"unknown checkpoint backend {settings.checkpoint_backend!r}")
    # Imported lazily: the Postgres checkpointer requires a live connection, and
    # the offline tests must not need one.
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    url = _psycopg_url(settings.database_url)
    with psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row) as conn:
        saver = PostgresSaver(conn)
        if url not in _POSTGRES_READY:
            saver.setup()
            _POSTGRES_READY.add(url)
        yield saver


def run_config(run_id: GraphRunId) -> dict[str, Any]:
    """Return the LangGraph invocation config for a run.

    One thread per run, never reused, thread id derived from the run id
    (architecture C.7) - so a checkpoint can always be traced to its
    ``graph_run`` row.
    """
    return {"configurable": {"thread_id": thread_id_for(run_id)}}
