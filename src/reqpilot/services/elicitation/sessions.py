"""Interview sessions and their utterances (P4; architecture C.4, G.3; FR-ELI-001..006).

The durable side of an interview. Every interview fact lives here, in the
database: the session's status, its topic coverage, the current topic, the
follow-up counter, the pending question, and every utterance. The
``elicitation_graph`` checkpoint holds ids and counters that mirror these, and
the graph re-derives its position from them whenever the two could disagree.

Two kinds of caller:

* **people** - an Analyst creates, pauses and resumes a session; a stakeholder
  (or an Analyst on their behalf) answers the pending question. These methods
  authorise through the policy and through the "only their own session" rule;
* **the elicitation pipeline** - acting as the run's system actor, it records
  the questions role #2 proposed and the coverage the tracker computed. It
  cannot answer, pause or resume anything (policy rule 7).

Answers are persisted **before** the graph resumes, and the resume value carries
only their id: no answer text ever enters a checkpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain import coverage as tracker
from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    DataSensitivity,
    InterviewSessionKind,
    InterviewSessionStatus,
    SpeakerKind,
)
from reqpilot.domain.errors import ElicitationError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import InterviewSession, Stakeholder, Utterance
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.elicitation import (
    InterviewSessionRepository,
    StakeholderRepository,
    UtteranceRepository,
)
from reqpilot.rules.elicitation import ElicitationRules, InterviewTemplate
from reqpilot.services.audit import AuditService
from reqpilot.services.elicitation.stakeholders import (
    answering_on_behalf,
    is_linked_user,
    is_stakeholder_only,
    require_may_view,
)


def topic_plan(template: InterviewTemplate) -> list[tracker.TopicPlan]:
    return [tracker.TopicPlan(t.topic_id, t.priority, t.required) for t in template.topics]


@dataclass(frozen=True)
class CoverageView:
    """Live coverage of one session (``FR-ELI-006``)."""

    session_id: uuid.UUID
    status: InterviewSessionStatus
    current_topic: str | None
    followups_this_topic: int
    max_followups: int
    summary: tracker.CoverageSummary
    entries: dict[str, dict]


class InterviewSessionService:
    def __init__(self, session: Session, actor: Actor, rules: ElicitationRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._sessions = InterviewSessionRepository(session, actor)
        self._utterances = UtteranceRepository(session, actor)
        self._stakeholders = StakeholderRepository(session, actor)
        self._audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    def get(self, project_id: ProjectId, session_id: uuid.UUID) -> InterviewSession | None:
        """The session if this actor may see it; ``None`` otherwise (never 403)."""
        row = self._sessions.get(project_id, session_id)
        if row is None:
            return None
        if is_stakeholder_only(self._actor, project_id) and not is_linked_user(
            self._actor, self._stakeholder(row)
        ):
            return None
        return row

    def require(self, project_id: ProjectId, session_id: uuid.UUID) -> InterviewSession:
        row = self.get(project_id, session_id)
        if row is None:
            raise ProjectIsolationError("not found")
        return row

    def list_sessions(
        self, project_id: ProjectId, *, kind: InterviewSessionKind | None = None
    ) -> list[InterviewSession]:
        rows = self._sessions.list_for_project(project_id, kind=kind)
        if is_stakeholder_only(self._actor, project_id):
            return [r for r in rows if is_linked_user(self._actor, self._stakeholder(r))]
        return rows

    def utterances(self, row: InterviewSession) -> list[Utterance]:
        return self._utterances.for_session(ProjectId(row.project_id), row.id)

    def utterance(self, row: InterviewSession, utterance_id: uuid.UUID) -> Utterance | None:
        found = self._utterances.get(ProjectId(row.project_id), utterance_id)
        return found if found is not None and found.session_id == row.id else None

    def stakeholder(self, row: InterviewSession) -> Stakeholder:
        return self._stakeholder(row)

    def coverage(self, row: InterviewSession) -> CoverageView:
        return CoverageView(
            session_id=row.id,
            status=row.status,
            current_topic=row.current_topic,
            followups_this_topic=row.followups_this_topic,
            max_followups=self._rules.max_followups_per_topic,
            summary=tracker.summarise(row.topic_coverage),
            entries={k: dict(v) for k, v in row.topic_coverage.items()},
        )

    def template(self, row: InterviewSession) -> InterviewTemplate:
        if row.template_id is None:
            raise ElicitationError("a clarification session has no interview template")
        template = self._rules.template(row.template_id)
        if template.version != row.template_version:
            raise ElicitationError(
                f"the session uses {row.template_id}@{row.template_version}, which is not the "
                f"loaded template version {template.version}"
            )
        return template

    # -- people ------------------------------------------------------------
    def create(
        self,
        *,
        project_id: ProjectId,
        stakeholder_id: uuid.UUID,
        template_id: str | None,
        sensitivity: DataSensitivity,
    ) -> InterviewSession:
        """A new interview (``FR-ELI-001``): the stakeholder's role template, not started."""
        require(
            self._actor,
            Action.SESSION_CREATE,
            ResourceRef(resource_type=self._sessions.resource_type, project_id=project_id),
        )
        stakeholder = self._stakeholders.get(project_id, stakeholder_id)
        if stakeholder is None:
            raise ProjectIsolationError("stakeholder not found in this project")
        if template_id is None:
            candidates = self._rules.templates_for_role(stakeholder.stakeholder_role)
            if not candidates:
                raise ElicitationError(f"no template for role {stakeholder.stakeholder_role!r}")
            template = candidates[0]
        else:
            template = self._rules.template(template_id)
        if template.stakeholder_role != stakeholder.stakeholder_role:
            raise ElicitationError(
                f"template {template.ref} is for {template.stakeholder_role!r}, but the "
                f"stakeholder's role is {stakeholder.stakeholder_role!r} (FR-ELI-001)"
            )
        row = self._sessions.add(
            InterviewSession(
                project_id=project_id,
                stakeholder_id=stakeholder.id,
                kind=InterviewSessionKind.INTERVIEW,
                template_id=template.template_id,
                template_version=template.version,
                sensitivity=sensitivity,
                status=InterviewSessionStatus.ACTIVE,
                topic_coverage=tracker.initial_coverage(topic_plan(template)),
                followups_this_topic=0,
                questions_asked=0,
                followups_asked=0,
                started_by=self._actor.actor_id,
            ),
            action=Action.SESSION_CREATE,
        )
        self._event(
            AuditEventType.INTERVIEW_SESSION_CREATED,
            row,
            {
                "stakeholder_id": str(stakeholder.id),
                "template": template.ref,
                "rules": self._rules.ref,
                "sensitivity": str(sensitivity),
                "topics": len(template.topics),
            },
        )
        return row

    def pause(self, row: InterviewSession) -> InterviewSession:
        """``FR-ELI-005``. The graph stays suspended at its checkpoint."""
        self._require_manage(row)
        if row.status is not InterviewSessionStatus.ACTIVE:
            raise ElicitationError(
                f"only an active session can be paused; this one is {row.status}"
            )
        row.status = InterviewSessionStatus.PAUSED
        row.updated_at = utc_now()
        self._sessions.save(row, action=Action.SESSION_MANAGE)
        self._event(AuditEventType.INTERVIEW_SESSION_PAUSED, row, {"topic": row.current_topic})
        return row

    def reactivate(self, row: InterviewSession) -> InterviewSession:
        """Resume a paused session, or retry a stalled one (``FR-ELI-005``)."""
        self._require_manage(row)
        if row.status not in (InterviewSessionStatus.PAUSED, InterviewSessionStatus.STALLED):
            raise ElicitationError(
                f"only a paused or stalled session can be resumed; this one is {row.status}"
            )
        previous = row.status
        row.status = InterviewSessionStatus.ACTIVE
        row.stall_reason = None
        row.updated_at = utc_now()
        self._sessions.save(row, action=Action.SESSION_MANAGE)
        self._event(
            AuditEventType.INTERVIEW_SESSION_RESUMED,
            row,
            {"from": str(previous), "pending_question": row.pending_question_id is not None},
        )
        return row

    def record_answer(self, row: InterviewSession, text: str) -> Utterance:
        """Persist the answer to the pending question (``FR-ELI-004``, ``FR-ELI-005``).

        Refused unless the session is active and a question is pending. The
        speaker is always the session's stakeholder; who typed it is recorded
        separately, so an analyst's entry on the stakeholder's behalf never
        passes for the stakeholder's own.
        """
        project_id = ProjectId(row.project_id)
        require(
            self._actor,
            Action.SESSION_ANSWER,
            ResourceRef(resource_type=self._sessions.resource_type, project_id=project_id),
        )
        stakeholder = self._stakeholder(row)
        on_behalf = answering_on_behalf(self._actor, project_id, stakeholder)
        if row.kind is not InterviewSessionKind.INTERVIEW:
            raise ElicitationError("clarifications are answered through their own endpoint")
        if row.status is not InterviewSessionStatus.ACTIVE:
            raise ElicitationError(f"the session is {row.status}; it does not accept answers")
        if row.pending_question_id is None or row.unassessed_answer_id is not None:
            raise ElicitationError("no question is awaiting an answer in this session")
        text = text.strip()
        if not text:
            raise ElicitationError("an answer cannot be empty")
        if len(text) > self._rules.max_answer_chars:
            raise ElicitationError(
                f"an answer is at most {self._rules.max_answer_chars} characters"
            )
        question = self.utterance(row, row.pending_question_id)
        if question is None:  # pragma: no cover - the pointer is written with the row
            raise ElicitationError("the pending question is missing")
        utterance = self._utterances.add(
            Utterance(
                project_id=project_id,
                session_id=row.id,
                seq=self._utterances.next_seq(project_id, row.id),
                speaker_kind=SpeakerKind.STAKEHOLDER,
                speaker_ref=stakeholder.id,
                stakeholder_role=stakeholder.stakeholder_role,
                text=text,
                topic_id=question.topic_id,
                is_followup=question.is_followup,
                replies_to_id=question.id,
                recorded_by=self._actor.actor_id,
                on_behalf=on_behalf,
            ),
            action=Action.SESSION_ANSWER,
        )
        row.unassessed_answer_id = utterance.id
        row.updated_at = utc_now()
        self._sessions.save(row, action=Action.SESSION_ANSWER)
        self._event(
            AuditEventType.UTTERANCE_RECORDED,
            row,
            {
                "utterance_id": str(utterance.id),
                "seq": utterance.seq,
                "speaker_kind": str(SpeakerKind.STAKEHOLDER),
                "on_behalf": on_behalf,
                "topic": question.topic_id,
            },
        )
        return utterance

    # -- the pipeline --------------------------------------------------------
    def record_question(
        self,
        row: InterviewSession,
        *,
        text: str,
        topic_id: str,
        is_followup: bool,
        agent_run_id: uuid.UUID | None,
    ) -> Utterance:
        """Persist a question role #2 proposed and validation accepted."""
        project_id = ProjectId(row.project_id)
        utterance = self._utterances.add(
            Utterance(
                project_id=project_id,
                session_id=row.id,
                seq=self._utterances.next_seq(project_id, row.id),
                speaker_kind=SpeakerKind.SYSTEM,
                text=text,
                topic_id=topic_id,
                is_followup=is_followup,
                agent_run_id=agent_run_id,
                on_behalf=False,
            ),
            action=Action.UTTERANCE_RECORD,
        )
        row.pending_question_id = utterance.id
        row.topic_coverage = tracker.record_question(
            row.topic_coverage, topic_id, is_followup=is_followup
        )
        row.questions_asked += 1
        row.followups_asked += 1 if is_followup else 0
        self.save_progress(row)
        self._event(
            AuditEventType.QUESTION_GENERATED,
            row,
            {
                "utterance_id": str(utterance.id),
                "seq": utterance.seq,
                "topic": topic_id,
                "is_followup": is_followup,
            },
            agent_run_id=agent_run_id,
        )
        return utterance

    def save_progress(self, row: InterviewSession) -> None:
        row.updated_at = utc_now()
        self._sessions.save(row, action=Action.RUN_RECORD)

    def mark_stalled(self, row: InterviewSession, reason: str) -> None:
        row.status = InterviewSessionStatus.STALLED
        row.stall_reason = reason[:300]
        self.save_progress(row)
        self._event(AuditEventType.INTERVIEW_SESSION_STALLED, row, {"reason_code": reason[:100]})

    def mark_completed(self, row: InterviewSession) -> None:
        row.status = InterviewSessionStatus.COMPLETED
        row.completed_at = utc_now()
        row.current_topic = None
        row.pending_question_id = None
        self.save_progress(row)
        summary = tracker.summarise(row.topic_coverage)
        self._event(
            AuditEventType.INTERVIEW_SESSION_COMPLETED,
            row,
            {
                "covered": len(summary.covered),
                "unresolved": len(summary.unresolved),
                "questions": row.questions_asked,
                "followups": row.followups_asked,
            },
        )

    def audit(
        self,
        event: AuditEventType,
        row: InterviewSession,
        payload: dict,
        *,
        agent_run_id: uuid.UUID | None = None,
    ) -> None:
        self._event(event, row, payload, agent_run_id=agent_run_id)

    # -- internals -----------------------------------------------------------
    def _stakeholder(self, row: InterviewSession) -> Stakeholder:
        stakeholder = self._stakeholders.get(ProjectId(row.project_id), row.stakeholder_id)
        if stakeholder is None:  # pragma: no cover - a composite foreign key
            raise ElicitationError("the session's stakeholder is missing")
        return stakeholder

    def _require_manage(self, row: InterviewSession) -> None:
        project_id = ProjectId(row.project_id)
        require(
            self._actor,
            Action.SESSION_MANAGE,
            ResourceRef(resource_type=self._sessions.resource_type, project_id=project_id),
        )
        require_may_view(self._actor, project_id, self._stakeholder(row))

    def _event(
        self,
        event: AuditEventType,
        row: InterviewSession,
        payload: dict,
        *,
        agent_run_id: uuid.UUID | None = None,
    ) -> None:
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=ProjectId(row.project_id),
            subject_type="interview_session",
            subject_id=str(row.id),
            graph_run_id=row.graph_run_id,
            agent_run_id=agent_run_id,
            payload=payload,
        )
