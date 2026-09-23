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

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import ApprovalDecisionType, ApprovalTaskStatus, Gate, Role
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
    )
    #: Whether this task blocks progress. G1-G8 are all blocking in the
    #: approved design; the column exists because the architecture names it.
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
