"""Stakeholders, interview sessions, utterances, quality findings and clarifications.

Tables for roadmap phase P4 (architecture G.3, G.4; C.4; E #2, E #4):

* ``stakeholder`` - a person whose needs are elicited, in one project (G.3).
  ``user_id`` optionally links the stakeholder to a ReqPilot user holding the
  Stakeholder role, so that user may answer their own sessions and nobody
  else's; without a link an analyst records answers on their behalf
  (``FR-ELI-005``).
* ``interview_session`` - one adaptive interview driven by ``elicitation_graph``,
  or one clarification round trip (G.3). Mutable, as G.3 says: status, coverage
  and the pending question change as the interview proceeds. Its topic coverage
  is written only by the deterministic coverage tracker.
* ``utterance`` - every question asked and every answer given, with speaker,
  role, timestamp, session and sequence (``FR-ELI-004``). **Append-only**: a
  traceability root that requirements cite (G.3).
* ``quality_finding`` - a defect recorded against one requirement version (G.4).
  In P4 only an analyst records findings; detecting them is quality analysis,
  roadmap P5. Content is immutable.
* ``clarification`` - a targeted question bound to one requirement version and
  one finding (``FR-CLR-001``), the open-issues list (``FR-CLR-002``). Content
  is immutable; the status moves once, from ``open`` to ``answered`` or
  ``dismissed``.

Project consistency is enforced by composite foreign keys: a session's
stakeholder, an utterance's session and a clarification's finding must belong to
the same project (or version) as the row that references them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import (
    ClarificationStatus,
    DataSensitivity,
    FindingDetector,
    FindingSeverity,
    InterviewSessionKind,
    InterviewSessionStatus,
    QualityFindingStatus,
    QualityFindingType,
    ReanalysisStatus,
    SpeakerKind,
    StakeholderAuthority,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk


def _updated_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Stakeholder(Base):
    """A person whose requirements are elicited, within one project (G.3)."""

    __tablename__ = "stakeholder"
    __table_args__ = (
        # Target of the composite foreign keys that keep sessions in-project.
        UniqueConstraint("id", "project_id", name="id_project"),
        CheckConstraint("length(name) >= 1", name="name_not_empty"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The stakeholder's business role - a key of the interview templates
    #: (``rules/data/interview_templates.yaml``), e.g. ``product_owner``.
    stakeholder_role: Mapped[str] = mapped_column(String(50), nullable=False)
    authority_level: Mapped[StakeholderAuthority] = mapped_column(
        SAEnum(StakeholderAuthority, name="stakeholder_authority_enum"), nullable=False
    )
    #: The ReqPilot user who *is* this stakeholder, if they answer for themselves.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class InterviewSession(Base):
    """One interview (``elicitation_graph``) or one clarification round trip (G.3)."""

    __tablename__ = "interview_session"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["stakeholder_id", "project_id"],
            ["stakeholder.id", "stakeholder.project_id"],
            name="fk_interview_session_stakeholder_same_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("followups_this_topic >= 0", name="followups_not_negative"),
        CheckConstraint(
            "(kind = 'INTERVIEW' AND template_id IS NOT NULL) OR kind = 'CLARIFICATION'",
            name="interview_has_template",
        ),
        Index("ix_interview_session_project_status", "project_id", "status"),
    )

    #: Fixed at creation; everything else follows the interview.
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "stakeholder_id",
            "kind",
            "template_id",
            "template_version",
            "sensitivity",
            "created_at",
            "started_by",
        }
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stakeholder_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[InterviewSessionKind] = mapped_column(
        SAEnum(InterviewSessionKind, name="interview_session_kind_enum"), nullable=False
    )
    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Declared when the session is created (``FR-ING-004``). Decides whether the
    #: session's text may leave the machine unmasked (the gateway's egress rule).
    sensitivity: Mapped[DataSensitivity] = mapped_column(
        SAEnum(DataSensitivity, name="data_sensitivity_enum"), nullable=False
    )
    status: Mapped[InterviewSessionStatus] = mapped_column(
        SAEnum(InterviewSessionStatus, name="interview_session_status_enum"), nullable=False
    )
    #: ``{topic_id: {status, required, priority, questions, followups,
    #: last_assessment, last_addressed_seq}}`` - written only by the tracker.
    topic_coverage: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    current_topic: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The question utterance awaiting an answer, if any.
    pending_question_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: Deterministic follow-up counter for the current topic (C.4).
    followups_this_topic: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The issue the last assessment found, which the next follow-up addresses.
    #: Model-derived text: kept here in the database, never in graph state.
    pending_issue: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The answer utterance recorded but not yet assessed, if any.
    unassessed_answer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    stall_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    questions_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    followups_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Utterance(Base):
    """One question or answer in a session (``FR-ELI-004``). Append-only (G.3)."""

    __tablename__ = "utterance"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="session_seq"),
        ForeignKeyConstraint(
            ["session_id", "project_id"],
            ["interview_session.id", "interview_session.project_id"],
            name="fk_utterance_session_same_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint("length(text) >= 1", name="text_not_empty"),
        # A question is the system's; an answer is a stakeholder's, and someone
        # (the stakeholder, or an analyst on their behalf) recorded it.
        CheckConstraint(
            "(speaker_kind = 'SYSTEM' AND speaker_ref IS NULL) OR "
            "(speaker_kind = 'STAKEHOLDER' AND speaker_ref IS NOT NULL "
            "AND recorded_by IS NOT NULL)",
            name="speaker_consistent",
        ),
        Index("ix_utterance_project_session_seq", "project_id", "session_id", "seq"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_kind: Mapped[SpeakerKind] = mapped_column(
        SAEnum(SpeakerKind, name="speaker_kind_enum"), nullable=False
    )
    #: The stakeholder who spoke (null for the system's questions).
    speaker_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The speaker's stakeholder role at the time, for an answer.
    stakeholder_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_followup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The question this answer replies to.
    replies_to_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("utterance.id", ondelete="CASCADE"), nullable=True
    )
    #: The user who typed an answer: the stakeholder themself, or an analyst.
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: True when an analyst recorded the answer on the stakeholder's behalf.
    on_behalf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: For a question: the agent run that proposed it.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class QualityFinding(Base):
    """A defect recorded against one requirement version (G.4).

    P4 defined the interface the clarification loop needs, with analyst-recorded
    findings. P5 adds the detector that writes the same rows: a deterministic
    rule (``detected_by = rule``) or a model proposal that passed deterministic
    validation (``detected_by = agent``), with its evidence, review signal and
    run provenance. A finding is *about* a version; it never changes the version.
    """

    __tablename__ = "quality_finding"
    __table_args__ = (
        UniqueConstraint("id", "requirement_version_id", name="id_version"),
        CheckConstraint("length(rationale) >= 1", name="rationale_not_empty"),
        CheckConstraint(
            "status = 'OPEN' OR length(coalesce(resolution_reason, '')) >= 1",
            name="closed_has_reason",
        ),
        # P5: the other version of a (near-)duplicate is in the same project.
        ForeignKeyConstraint(
            ["related_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_quality_finding_related_same_project",
            ondelete="CASCADE",
        ),
        Index("ix_quality_finding_project_version", "project_id", "requirement_version_id"),
        Index("ix_quality_finding_project_status", "project_id", "status"),
    )

    #: Only the status and its resolution may change, once (P5: resolve, dismiss).
    MUTABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"status", "updated_at", "resolution_reason", "resolved_by", "resolved_at"}
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=False
    )
    finding_type: Mapped[QualityFindingType] = mapped_column(
        SAEnum(QualityFindingType, name="quality_finding_type_enum"), nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        SAEnum(FindingSeverity, name="finding_severity_enum"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    #: The words of the statement the finding is about, if it is about some.
    span_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[QualityFindingStatus] = mapped_column(
        SAEnum(QualityFindingStatus, name="quality_finding_status_enum"), nullable=False
    )
    detected_by: Mapped[FindingDetector] = mapped_column(
        SAEnum(FindingDetector, name="finding_detector_enum"), nullable=False
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # --- P5: detection provenance --------------------------------------------
    #: The deterministic rule, or the prompt, that produced the finding.
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Heuristic review-prioritisation signal in [0, 1] - not a probability.
    review_signal: Mapped[float | None] = mapped_column(nullable=True)
    #: ``[{"kind": "statement_span", "quote", "start", "end"}, ...]`` - what the
    #: detector saw, located in the version's own statement.
    evidence: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    #: The other version of a duplicate / near-duplicate, in the same project.
    related_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    # --- P5: human resolution ---------------------------------------------------
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class Clarification(Base):
    """A targeted question bound to one version and one finding (``FR-CLR-001``).

    Its age is derived from ``created_at``; nothing stores it (``FR-CLR-002``).
    """

    __tablename__ = "clarification"
    __table_args__ = (
        ForeignKeyConstraint(
            ["quality_finding_id", "requirement_version_id"],
            ["quality_finding.id", "quality_finding.requirement_version_id"],
            name="fk_clarification_finding_same_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["asked_of_stakeholder_id", "project_id"],
            ["stakeholder.id", "stakeholder.project_id"],
            name="fk_clarification_stakeholder_same_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(question) >= 1", name="question_not_empty"),
        CheckConstraint(
            "status <> 'ANSWERED' OR answer_utterance_id IS NOT NULL", name="answered_has_answer"
        ),
        CheckConstraint(
            "status <> 'DISMISSED' OR length(coalesce(dismissed_reason, '')) >= 1",
            name="dismissed_has_reason",
        ),
        # Duplicate-open-question prevention (architecture E #4): at most one open
        # clarification per finding.
        Index(
            "uq_clarification_open_per_finding",
            "quality_finding_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
            sqlite_where=text("status = 'OPEN'"),
        ),
        Index("ix_clarification_project_status", "project_id", "status"),
    )

    #: Written once, when the clarification is raised.
    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "requirement_version_id",
            "quality_finding_id",
            "question",
            "expected_answer_shape",
            "asked_of_stakeholder_id",
            "session_id",
            "question_utterance_id",
            "raised_by",
            "agent_run_id",
            "created_at",
        }
    )
    #: Written when the status moves from ``open``.
    RESOLUTION_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "status",
            "answer_utterance_id",
            "answered_by",
            "answered_at",
            "dismissed_reason",
            "dismissed_by",
            "dismissed_at",
        }
    )
    #: The re-analysis outcome, which may be retried after a failure.
    REANALYSIS_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"reanalysis_status", "reanalysis_run_id", "resulting_version_id"}
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="CASCADE"), nullable=False
    )
    quality_finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer_shape: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[ClarificationStatus] = mapped_column(
        SAEnum(ClarificationStatus, name="clarification_status_enum"), nullable=False
    )
    asked_of_stakeholder_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: The analyst responsible for the issue (``FR-CLR-002``).
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    raised_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: The clarification session holding the question and answer utterances.
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("interview_session.id", ondelete="CASCADE"), nullable=False
    )
    question_utterance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("utterance.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    answer_utterance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("utterance.id", ondelete="CASCADE"), nullable=True
    )
    answered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    answered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dismissed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reanalysis_status: Mapped[ReanalysisStatus | None] = mapped_column(
        SAEnum(ReanalysisStatus, name="reanalysis_status_enum"), nullable=True
    )
    reanalysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requirement_version.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


# ---------------------------------------------------------------------------
# Immutability, enforced in the ORM (and again by the database's triggers)
# ---------------------------------------------------------------------------


def _changed(instance: Base) -> set[str]:
    state = inspect(instance)
    return {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}


def _previous(instance: Base, attribute: str) -> object:
    deleted = inspect(instance).attrs[attribute].history.deleted
    return deleted[0] if deleted else None


@event.listens_for(Session, "before_flush")
def _guard_elicitation_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse any flush that rewrites elicitation history.

    Registered on the ``Session`` class, so no session can skip it. A project
    deletion removes these rows through the database's cascade, not here.
    """
    for instance in session.deleted:
        if isinstance(instance, (Utterance, InterviewSession, QualityFinding, Clarification)):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are never deleted")

    for instance in session.dirty:
        changed = _changed(instance)
        if not changed:
            continue
        if isinstance(instance, Utterance):
            raise ImmutableRecordError(
                "utterances are append-only (architecture G.3); record a new utterance instead"
            )
        if isinstance(instance, InterviewSession) and changed & InterviewSession.IDENTITY_FIELDS:
            raise ImmutableRecordError("a session's project, stakeholder and template are fixed")
        if isinstance(instance, QualityFinding):
            if changed - QualityFinding.MUTABLE_FIELDS:
                raise ImmutableRecordError("a quality finding's content is immutable")
            if _previous(instance, "status") not in (None, QualityFindingStatus.OPEN) or (
                changed & {"resolution_reason", "resolved_by", "resolved_at"}
                and "status" not in changed
            ):
                raise ImmutableRecordError("a quality finding is resolved or dismissed once")
        if isinstance(instance, Clarification):
            if changed & Clarification.CONTENT_FIELDS:
                raise ImmutableRecordError("a clarification's question and binding are immutable")
            if changed & Clarification.RESOLUTION_FIELDS:
                if _previous(instance, "status") not in (None, ClarificationStatus.OPEN):
                    raise ImmutableRecordError("a clarification is answered or dismissed once")
                if "status" not in changed:
                    raise ImmutableRecordError("a clarification's resolution is written once")
