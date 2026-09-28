"""Approval task and decision tables (architecture G.7, `[DESIGN] D6`).

``ApprovalRecord`` is deliberately split in two: a **task** is the request, a
**decision** is the outcome. They have different lifetimes and different
cardinalities - a task can be open, aged and reported on, and a gate under dual
control collects several decisions before it passes.

``approval_decision`` is **append-only**, exactly like ``audit_event``: the
application exposes no update or delete path, and a decision's
``subject_version_hash`` is what makes "approved *exactly this version*"
verifiable afterwards.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint, event, inspect
from sqlalchemy import Enum as SAEnum
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import ApprovalDecisionType, ApprovalTaskStatus, Gate, Role
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk


class ApprovalTask(Base):
    """A pending human decision at a gate (architecture M.3)."""

    __tablename__ = "approval_task"
    __table_args__ = (
        Index("ix_approval_task_project_status", "project_id", "status"),
        Index("ix_approval_task_group", "task_group_id"),
        Index("ix_approval_task_subject", "project_id", "subject_type", "subject_id"),
        # P6: referenceable with its project, so a G2/G3 subject links only to a
        # task of its own project (composite foreign key).
        UniqueConstraint("id", "project_id", name="id_project"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )

    gate: Mapped[Gate] = mapped_column(SAEnum(Gate, name="gate_enum"), nullable=False)

    #: Tasks raised by one submission share a group id, so a gate that spans a
    #: set of subjects passes only when every member has passed. Architecture
    #: M.3 uses the same mechanism for G6's multi-role grouping.
    task_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Human-readable version label shown to the reviewer, e.g. ``"3"``.
    subject_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: The exact-version binding captured **when the task was raised**. A
    #: decision must still match this, so an edit after submission invalidates
    #: the task rather than silently widening the approval.
    subject_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The single role a decider must exercise to close **this** task.
    #:
    #: Singular by design (architecture G.7). A gate that needs several roles is
    #: represented as one task per role sharing a ``task_group_id`` - the same
    #: mechanism architecture M.3 specifies for G6 - rather than by widening this
    #: column. That keeps "who must sign this off" a single, checkable value.
    required_role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum", create_type=False), nullable=False
    )

    status: Mapped[ApprovalTaskStatus] = mapped_column(
        SAEnum(ApprovalTaskStatus, name="approval_task_status_enum"),
        nullable=False,
        default=ApprovalTaskStatus.OPEN,
        # P11: the guard below needs the value before a change, even when expired.
        active_history=True,
    )
    #: Whether this task blocks progress. G1-G8 are all blocking in the
    #: approved design; the column exists because the architecture names it.
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: P8: the one person who may decide this task, when the gate names a
    #: specific party rather than a role - the affected stakeholder of a G4
    #: conflict (architecture M.3: "Analyst + affected stakeholders"). Null for
    #: every other task, which any holder of ``required_role`` may decide.
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    decisions: Mapped[list[ApprovalDecision]] = relationship(
        back_populates="task", order_by="ApprovalDecision.decided_at"
    )

    def is_terminal(self) -> bool:
        """Whether the task has been decided and may not be decided again."""
        return self.status in (ApprovalTaskStatus.APPROVED, ApprovalTaskStatus.REJECTED)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ApprovalTask(id={self.id}, gate={self.gate}, status={self.status})"


class ApprovalDecision(Base):
    """One human decision against one task. Append-only.

    There is no update path and no delete path. A decider who changes their mind
    does not edit this row; a new task is raised.
    """

    __tablename__ = "approval_decision"
    __table_args__ = (Index("ix_approval_decision_task", "task_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("approval_task.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised for project-scoped queries and isolation checks.
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )

    decided_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: **Which** role the decider exercised. A person may hold several roles in
    #: a project, so recording the exercised role is what keeps role-appropriate
    #: approval meaningful and auditable (architecture G.2).
    role_exercised: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum", create_type=False), nullable=False
    )

    decision: Mapped[ApprovalDecisionType] = mapped_column(
        SAEnum(ApprovalDecisionType, name="approval_decision_type_enum"), nullable=False
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The binding. Recorded from the subject at decision time and compared with
    #: the task's hash, so a decision can never cover a version the decider did
    #: not see.
    subject_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    decided_at: Mapped[dt.datetime] = created_at_column()

    task: Mapped[ApprovalTask] = relationship(back_populates="decisions")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"ApprovalDecision(id={self.id}, decision={self.decision}, role={self.role_exercised})"
        )


# ---------------------------------------------------------------------------
# P11: the records of the one approval path cannot be forged or rewritten
# ---------------------------------------------------------------------------

#: What a task *is*: fixed when it is raised. ``subject_version_hash`` is left out
#: deliberately: a changed binding can only make the approval service refuse
#: (``StaleApprovalError`` - it re-checks the binding against the subject at decision
#: time), and the P1/P8 security tests rely on simulating exactly that tampering.
_TASK_IDENTITY = (
    "project_id",
    "gate",
    "task_group_id",
    "subject_type",
    "subject_id",
    "required_role",
    "blocking",
)

#: A task closes as APPROVED or REJECTED only with the decision that closes it.
_CLOSING_DECISIONS: dict[ApprovalTaskStatus, frozenset[ApprovalDecisionType]] = {
    ApprovalTaskStatus.APPROVED: frozenset({ApprovalDecisionType.APPROVE}),
    ApprovalTaskStatus.REJECTED: frozenset(
        {ApprovalDecisionType.REJECT, ApprovalDecisionType.MODIFY}
    ),
}


def _has_decision(
    session: Session, task_id: uuid.UUID, kinds: frozenset[ApprovalDecisionType]
) -> bool:
    for obj in session.new:
        if isinstance(obj, ApprovalDecision) and obj.task_id == task_id and obj.decision in kinds:
            return True
    with session.no_autoflush:
        found = session.scalars(
            sa_select(ApprovalDecision.id).where(
                ApprovalDecision.task_id == task_id, ApprovalDecision.decision.in_(kinds)
            )
        ).first()
    return found is not None


def _check_new_decision(session: Session, decision: ApprovalDecision) -> None:
    from reqpilot.domain.models.identity import ProjectMember

    with session.no_autoflush:
        task = session.get(ApprovalTask, decision.task_id)
        if task is None or task.project_id != decision.project_id:
            raise ImmutableRecordError("a decision names an approval task of its own project")
        if task.status is not ApprovalTaskStatus.OPEN:
            raise ImmutableRecordError(f"a {task.status} approval task takes no further decision")
        if decision.role_exercised is not task.required_role:
            raise ImmutableRecordError("a decision is recorded only in the task's own role")
        holds = session.scalars(
            sa_select(ProjectMember.id).where(
                ProjectMember.project_id == decision.project_id,
                ProjectMember.user_id == decision.decided_by,
                ProjectMember.role == decision.role_exercised,
            )
        ).first()
    if holds is None:
        raise ImmutableRecordError(
            "a decision is recorded only for a project member holding the role exercised"
        )


@event.listens_for(Session, "before_flush")
def _guard_approval_records(session: Session, _context: object, _instances: object) -> None:
    """Architecture J.1 / M.2 as a property of the records (P11; mirrored in PostgreSQL).

    * ``approval_decision`` is append-only: never updated, never deleted - and a
      new one is recorded only against an ``OPEN`` task of the same project, in
      the task's own role, by a project member who holds that role.
    * A task is never deleted through the ORM, and what it is - its gate, subject,
      exact-version binding, role, group and ``blocking`` - never changes.
    * A task moves only out of ``OPEN``; to ``APPROVED`` only with an ``APPROVE``
      decision for it, to ``REJECTED`` only with a ``REJECT``/``MODIFY`` one;
      ``CANCELLED`` (which passes nothing) needs none. So a status written by any
      path other than the approval service cannot make a gate pass.

    (The PostgreSQL project purge deletes these rows nested in a trigger, which the
    database-level guard allows; the ORM never deletes them.)
    """
    for obj in session.new:
        if isinstance(obj, ApprovalDecision):
            _check_new_decision(session, obj)
    for obj in session.deleted:
        if isinstance(obj, (ApprovalDecision, ApprovalTask)):
            raise ImmutableRecordError(
                f"{type(obj).__tablename__} rows are never deleted (architecture M.2)"
            )
    for obj in session.dirty:
        if isinstance(obj, ApprovalDecision) and session.is_modified(obj):
            raise ImmutableRecordError("approval_decision is append-only (architecture G.7)")
        if not isinstance(obj, ApprovalTask) or not session.is_modified(obj):
            continue
        state = inspect(obj)
        for name in _TASK_IDENTITY:
            if state.attrs[name].history.has_changes():
                raise ImmutableRecordError(f"an approval task's {name} never changes")
        history = state.attrs.status.history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        if before is not None and before is not ApprovalTaskStatus.OPEN:
            raise ImmutableRecordError(f"a {before} approval task cannot change again")
        needed = _CLOSING_DECISIONS.get(obj.status)
        if needed is not None and not _has_decision(session, obj.id, needed):
            raise ImmutableRecordError(
                f"an approval task becomes {obj.status} only with the decision that closes it"
            )
