"""Project deletion and retention (``FR-ADM-006``; architecture P.2, G.18; roadmap P11).

The approved design: *project deletion removes content rows and vectors but
retains audit events with content-bearing payload fields redacted, preserving
the accountability record without retaining the data* (P.2). In order:

1. **Authorise.** ``PROJECT_DELETE`` is the Project Manager's and a human
   decision (policy rules 1, 14): isolation is checked first, no agent or
   pipeline actor can delete. The caller must also repeat the project's name.
2. **Tombstone.** The ``project`` row stays - ``audit_event`` is append-only and
   hash-chained per project (ADR-010), and every row hash covers its
   ``project_id``, so the row the chain points at cannot go. Its name and
   description are replaced, its scope cleared, and ``deleted_at`` set; a
   tombstone never changes again (trigger and ORM guard).
3. **Purge.** Every content row of *this project only*: requirements and
   versions, sources, segments, utterances and sessions, stakeholders,
   findings, conflicts, mappings, evidence links, risks, baselines, artefacts,
   trace links, SDLC runs, workflows, approval tasks and decisions, graph and
   agent runs, the unmasking map - and the LangGraph checkpoints of the
   project's runs. On PostgreSQL this is the ``project_purge`` trigger of
   migration 0013, which runs nested so the earlier phases' append-only guards
   let it through (their ``pg_trigger_depth() > 1`` branch); elsewhere the same
   deletes run directly. There are no project vectors to remove: nothing embeds
   project text (J.1 - only the curated knowledge base is retrieved over), and
   the shared knowledge base is not the project's and is untouched.
4. **Verify**, in the same transaction, that nothing of the project's content
   remains - counted over the live schema, not over the list the purge used -
   and fail the whole deletion otherwise. Steps 2-4 run in one savepoint: a
   failure deletes nothing and leaves no tombstone.
5. **Record** ``PROJECT_DELETED`` with counts only.

**What is retained:** the tombstone, ``project_member`` (who held which role -
the accountability the audit trail refers to), and the audit trail, readable
only through the redacting readers (:mod:`reqpilot.security.redaction`).
**Retention period:** none is asserted. The approved documents define *what*
deletion removes and retains, not how long anything is kept; P11 implements
deletion on request and invents no schedule or legal retention period.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Table, delete, func, inspect, select, text
from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, AuditEventType, ResourceType
from reqpilot.domain.errors import DeletionError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId, thread_id_for
from reqpilot.domain.models import Base
from reqpilot.domain.models.base import as_utc, utc_now
from reqpilot.domain.models.guardrails import ProjectPurge
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.runs import AgentRun, GraphRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.services.audit import AuditService

#: Identifies the deletion semantics recorded on each ``PROJECT_DELETED`` event.
DELETION_POLICY = "p11-project-deletion@1"

#: Project-scoped tables whose rows survive a deletion (P.2), and the record of it.
RETAINED_TABLES = frozenset({"audit_event", "project_member", "project", "project_purge"})

#: The LangGraph checkpoint store (architecture C.7), keyed by thread id.
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def content_tables() -> list[Table]:
    """Every project-scoped content table, children before parents."""
    return [
        table
        for table in reversed(Base.metadata.sorted_tables)
        if "project_id" in table.c and table.name not in RETAINED_TABLES
    ]


@dataclass(frozen=True)
class DeletionReceipt:
    """What a deletion removed and kept. Counts and ids only."""

    project_id: uuid.UUID
    deleted_at: dt.datetime
    deleted_by: uuid.UUID
    already_deleted: bool
    rows_removed: dict[str, int] = field(default_factory=dict)
    checkpoint_threads: int = 0
    retained: tuple[str, ...] = ("audit_event (redacted on read)", "project_member", "project")

    @property
    def total_removed(self) -> int:
        return sum(self.rows_removed.values())


class ProjectDeletionService:
    def __init__(
        self,
        session: Session,
        actor: Actor,
        *,
        forget_threads: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self._session = session
        self._actor = actor
        #: The orchestration layer's hook for its in-process checkpointer (the
        #: offline ``memory`` backend). Services may not import the graph layer,
        #: so the caller supplies it; the PostgreSQL store is purged here, in the
        #: same transaction.
        self._forget_threads = forget_threads

    # ------------------------------------------------------------------
    def delete(self, project_id: ProjectId, *, confirm_name: str) -> DeletionReceipt:
        """Delete ``project_id``'s content (see the module docstring). Idempotent."""
        require(
            self._actor,
            Action.PROJECT_DELETE,
            ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id),
        )
        project = self._session.get(Project, project_id)
        if project is None:
            raise ProjectIsolationError("not found")
        if project.deleted_at is not None:
            return self._receipt_of_earlier(project)
        if confirm_name.strip() != project.name:
            raise DeletionError("the confirmation does not match the project's name")

        self._session.flush()
        with self._session.begin_nested():
            counts = self.content_counts(project_id)
            run_ids = self._run_ids(project_id)
            agent_runs = self._agent_run_count(run_ids)
            now = utc_now()
            project.deleted_at = now
            project.deleted_by = self._actor.actor_id
            project.name = f"Deleted project {uuid.UUID(str(project_id)).hex[:8]}"
            project.description = None
            project.jurisdiction_scope = []
            project.kb_version_pin = None
            self._session.flush()
            checkpoints = self._delete_checkpoints(run_ids)
            self._purge(project_id, run_ids)
            leftover = self.content_counts(project_id)
            leftover_runs = self._agent_run_count(run_ids)
            remaining = {table: n for table, n in leftover.items() if n}
            if leftover_runs:
                remaining["agent_run"] = leftover_runs
            if remaining or self._checkpoint_count(run_ids):
                raise DeletionError(
                    "project content remained after the purge; nothing was deleted "
                    f"(tables: {sorted(remaining) or ['checkpoints']})"
                )
            removed = {table: n for table, n in counts.items() if n}
            if agent_runs:
                removed["agent_run"] = agent_runs
            AuditService(self._session).append(
                event_type=AuditEventType.PROJECT_DELETED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type="project",
                subject_id=str(project_id),
                payload={
                    "policy": DELETION_POLICY,
                    "rows_removed": dict(sorted(removed.items())),
                    "checkpoint_threads": checkpoints,
                    "retained": ["audit_event", "project_member", "project"],
                },
            )
        # Stale identities of purged rows must not be read as if they still existed.
        self._session.expire_all()
        self._forget_memory_threads(run_ids)
        return DeletionReceipt(
            project_id=uuid.UUID(str(project_id)),
            deleted_at=now,
            deleted_by=self._actor.actor_id,
            already_deleted=False,
            rows_removed=dict(sorted(removed.items())),
            checkpoint_threads=checkpoints,
        )

    # ------------------------------------------------------------------
    def content_counts(self, project_id: ProjectId) -> dict[str, int]:
        """Rows per content table for ``project_id`` - over the live schema."""
        counts: dict[str, int] = {}
        for table in content_tables():
            counts[table.name] = int(
                self._session.execute(
                    select(func.count()).select_from(table).where(table.c.project_id == project_id)
                ).scalar_one()
            )
        return counts

    def _run_ids(self, project_id: ProjectId) -> list[uuid.UUID]:
        return list(
            self._session.scalars(select(GraphRun.id).where(GraphRun.project_id == project_id))
        )

    def _agent_run_count(self, run_ids: list[uuid.UUID]) -> int:
        if not run_ids:
            return 0
        return int(
            self._session.execute(
                select(func.count()).select_from(AgentRun).where(AgentRun.graph_run_id.in_(run_ids))
            ).scalar_one()
        )

    def _purge(self, project_id: ProjectId, run_ids: list[uuid.UUID]) -> None:
        self._session.add(ProjectPurge(project_id=project_id, requested_by=self._actor.actor_id))
        self._session.flush()
        if self._is_postgres():
            return  # the project_purge_cascade trigger has run (migration 0013)
        if run_ids:
            self._session.execute(delete(AgentRun).where(AgentRun.graph_run_id.in_(run_ids)))
        for table in content_tables():
            self._session.execute(delete(table).where(table.c.project_id == project_id))

    # -- checkpoints (architecture C.7) ----------------------------------------
    def _checkpoint_tables(self) -> list[str]:
        if not self._is_postgres():
            return []
        present = set(inspect(self._session.connection()).get_table_names())
        return [t for t in CHECKPOINT_TABLES if t in present]

    def _delete_checkpoints(self, run_ids: list[uuid.UUID]) -> int:
        threads = [thread_id_for(r) for r in run_ids]  # type: ignore[arg-type]
        if not threads:
            return 0
        found = self._checkpoint_count(run_ids)
        for table in self._checkpoint_tables():
            self._session.execute(
                text(f"DELETE FROM {table} WHERE thread_id = ANY(:threads)"),  # noqa: S608
                {"threads": threads},
            )
        return found

    def _checkpoint_count(self, run_ids: list[uuid.UUID]) -> int:
        threads = [thread_id_for(r) for r in run_ids]  # type: ignore[arg-type]
        if not threads or "checkpoints" not in self._checkpoint_tables():
            return 0
        return int(
            self._session.execute(
                text("SELECT count(DISTINCT thread_id) FROM checkpoints WHERE thread_id = ANY(:t)"),
                {"t": threads},
            ).scalar_one()
        )

    def _forget_memory_threads(self, run_ids: list[uuid.UUID]) -> None:
        """The in-process checkpointer used offline keeps interview threads too."""
        if self._forget_threads is not None and run_ids:
            self._forget_threads([thread_id_for(r) for r in run_ids])  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    def _receipt_of_earlier(self, project: Project) -> DeletionReceipt:
        assert project.deleted_at is not None and project.deleted_by is not None
        return DeletionReceipt(
            project_id=project.id,
            deleted_at=as_utc(project.deleted_at),
            deleted_by=project.deleted_by,
            already_deleted=True,
        )

    def _is_postgres(self) -> bool:
        return self._session.get_bind().dialect.name == "postgresql"


def is_deleted(session: Session, project_id: Any) -> bool:
    project = session.get(Project, project_id)
    return project is not None and project.deleted_at is not None
