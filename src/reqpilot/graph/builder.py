"""Graph construction and checkpointer selection (architecture ADR-001, C.7).

P0 provides the conventions the four real graphs will be built with, and one
minimal smoke graph to prove the machinery works. The elicitation, analysis,
documentation and SDLC graphs themselves belong to later roadmap phases.

Checkpointer selection is a configuration boundary, not a decision made at each
call site: ``memory`` for offline tests and local development, ``postgres`` for
anything durable.
"""

from __future__ import annotations

from typing import Any

from reqpilot.config import Settings, get_settings
from reqpilot.domain.ids import GraphRunId, thread_id_for


def build_checkpointer(settings: Settings | None = None) -> Any:
    """Return the configured LangGraph checkpointer.

    ``memory`` needs no database, which is what lets the graph smoke tests run
    offline in CI. ``postgres`` is the durable backend the architecture
    specifies for real runs.
    """
    settings = settings or get_settings()

    if settings.checkpoint_backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if settings.checkpoint_backend == "postgres":
        # Imported lazily: the Postgres checkpointer requires a live connection,
        # and P0's offline tests must not need one.
        from langgraph.checkpoint.postgres import PostgresSaver

        return PostgresSaver.from_conn_string(settings.database_url)

    raise ValueError(f"unknown checkpoint backend {settings.checkpoint_backend!r}")


def run_config(run_id: GraphRunId) -> dict[str, Any]:
    """Return the LangGraph invocation config for a run.

    One thread per run, never reused, thread id derived from the run id
    (architecture C.7) - so a checkpoint can always be traced to its
    ``graph_run`` row.
    """
    return {"configurable": {"thread_id": thread_id_for(run_id)}}
