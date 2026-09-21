"""Graph run and agent run records (architecture G.7, C.7).

These two tables are the durable record of orchestration. They exist in P0
because the audit trail, not the LangGraph checkpoint, is the record of what
happened: checkpoints are prunable, these rows are not.

``agent_run`` carries the fields that make evaluation metrics computable later
without new instrumentation - prompt version, model version, evidence ids,
tokens, latency (architecture R.2).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import AgentRole, AgentRunStatus, GraphRunStatus
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class GraphRun(Base):
    """One execution of one graph.

    ``thread_id`` is the LangGraph thread and equals ``str(id)`` - one thread per
    run, never reused (architecture C.7). Storing it explicitly means a
    checkpoint can always be traced back to its run without inferring the
    convention.
    """

    __tablename__ = "graph_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    graph_name: Mapped[str] = mapped_column(String(100), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[GraphRunStatus] = mapped_column(
        SAEnum(GraphRunStatus, name="graph_run_status_enum"),
        nullable=False,
        default=GraphRunStatus.PENDING,
    )
    started_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    started_at: Mapped[dt.datetime] = created_at_column()
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    #: Consecutive failures of the current node; the run is failed rather than
    #: retried forever once this exceeds the configured maximum (architecture C.8).
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="graph_run", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    """One invocation of one agent role at one node. Append-only in practice.

    Written for every node execution, including deterministic ones, so that the
    audit trail covers the whole run rather than only its LLM parts.
    """

    __tablename__ = "agent_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    graph_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[AgentRole] = mapped_column(
        SAEnum(AgentRole, name="agent_role_enum"), nullable=False
    )

    # Provenance of the generation, for reproducibility and evaluation (R.2).
    # Nullable because deterministic nodes have no prompt or model.
    prompt_template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    #: Id references only, never payload text (same rule as audit payloads).
    input_refs: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    output_refs: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)

    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[AgentRunStatus] = mapped_column(
        SAEnum(AgentRunStatus, name="agent_run_status_enum"), nullable=False
    )
    started_at: Mapped[dt.datetime] = created_at_column()

    # Added by the extraction phase, the first to call a model (G.7, ET-08).
    #: Provider calls made, including the one schema repair and transient retries.
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Why the invocation failed, as a stable code (never model text).
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Heuristic review signal, **not** a calibrated probability (Phase 0 H.1).
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Estimated cost from configured prices; null when no price is configured.
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    graph_run: Mapped[GraphRun] = relationship(back_populates="agent_runs")
