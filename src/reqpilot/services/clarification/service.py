"""The clarification loop's deterministic side (P4; architecture E #4, G.4, H.3).

Role #4 proposes a question; everything else is here:

* **binding** - a clarification is bound to one requirement version and one open
  quality finding of that version (``FR-CLR-001``); the version must be the
  requirement's current one;
* **duplicate prevention** - at most one open clarification per finding
  (E #4; also a partial unique index in the database);
* **the open-issues list** - status, assignee, the stakeholder asked, and an age
  derived from ``created_at`` (``FR-CLR-002``);
* **answers** - persisted as an utterance in the clarification's own session,
  so the answer is a traceability root like any other stakeholder statement
  (architecture edge ``clarification ANSWERED_BY utterance``); answered once;
* **dismissal** - an Analyst, with a recorded reason (``FR-CLR-004``); dismissing
  changes neither the requirement nor the finding;
* **lifecycle** - raising a clarification moves the version to
  ``CLARIFICATION_REQUIRED`` through the P1 transition table, as the Analyst.

Re-analysis after an answer (``FR-CLR-003``) runs in ``analysis_graph`` and
records its outcome here with :meth:`ClarificationService.record_reanalysis`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    ClarificationStatus,
    DataSensitivity,
    InterviewSessionKind,
    InterviewSessionStatus,
    QualityFindingStatus,
    ReanalysisStatus,
    SpeakerKind,
)
from reqpilot.domain.errors import ClarificationError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import (
    Clarification,
    InterviewSession,
    QualityFinding,
    Stakeholder,
    Utterance,
)
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.elicitation import (
    ClarificationRepository,
    InterviewSessionRepository,
    QualityFindingRepository,
    StakeholderRepository,
    UtteranceRepository,
)
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.audit import AuditService
from reqpilot.services.elicitation.provenance import source_facts
from reqpilot.services.elicitation.stakeholders import (
    answering_on_behalf,
    is_linked_user,
    is_stakeholder_only,
)
from reqpilot.services.requirements import RequirementService

#: States a version may be in to have a clarification raised against it, with
#: the P1 transitions (H.3) that take it to CLARIFICATION_REQUIRED. A version at
#: or past VALIDATED is changed through a new version (and G7 once approved),
#: not clarified in place.
RAISE_PATHS: dict[RequirementState, tuple[RequirementState, ...]] = {
    RequirementState.CLASSIFIED: (
        RequirementState.ANALYZED,
        RequirementState.CLARIFICATION_REQUIRED,
    ),
    RequirementState.ANALYZED: (RequirementState.CLARIFICATION_REQUIRED,),
    RequirementState.CLARIFIED: (
        RequirementState.ANALYZED,
        RequirementState.CLARIFICATION_REQUIRED,
    ),
    RequirementState.REJECTED: (RequirementState.CLARIFICATION_REQUIRED,),
    RequirementState.CLARIFICATION_REQUIRED: (),
}


@dataclass(frozen=True)
class RaiseContext:
    """Everything a clarification question is generated from - and nothing more."""

    project_id: ProjectId
    finding: QualityFinding
    version: RequirementVersion
    requirement: Requirement
    stakeholder: Stakeholder
    prior_questions: tuple[str, ...]
    masked: bool
    synthetic: bool


@dataclass(frozen=True)
class IssueView:
    """One row of the open-issues list (``FR-CLR-002``)."""

    clarification: Clarification
    requirement_human_id: str
    version_no: int
    statement: str
    finding: QualityFinding
    stakeholder_name: str
    answer: str | None
    resulting_version_no: int | None
    age: dt.timedelta


class ClarificationService:
    def __init__(self, session: Session, actor: Actor, rules: ElicitationRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._clarifications = ClarificationRepository(session, actor)
        self._findings = QualityFindingRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._stakeholders = StakeholderRepository(session, actor)
        self._sessions = InterviewSessionRepository(session, actor)
        self._utterances = UtteranceRepository(session, actor)
        self._audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    def get(self, project_id: ProjectId, clarification_id: uuid.UUID) -> Clarification | None:
        clarification = self._clarifications.get(project_id, clarification_id)
        if clarification is None:
            return None
        if is_stakeholder_only(self._actor, project_id) and not is_linked_user(
            self._actor, self._stakeholder(clarification)
        ):
            return None
        return clarification

    def issues(
        self, project_id: ProjectId, *, status: ClarificationStatus | None = None
    ) -> list[IssueView]:
        now = utc_now()
        views = []
        for clarification in self._clarifications.list_for_project(project_id, status=status):
            stakeholder = self._stakeholder(clarification)
            if is_stakeholder_only(self._actor, project_id) and not is_linked_user(
                self._actor, stakeholder
            ):
                continue
            views.append(self._view(project_id, clarification, stakeholder, now))
        return views

    def issue(self, project_id: ProjectId, clarification: Clarification) -> IssueView:
        return self._view(project_id, clarification, self._stakeholder(clarification), utc_now())

    # -- raise -------------------------------------------------------------
    def prepare_raise(
        self, *, project_id: ProjectId, finding_id: uuid.UUID, asked_of: uuid.UUID
    ) -> RaiseContext:
        """Check that a clarification may be raised, and gather its (only) inputs."""
        require(
            self._actor,
            Action.CLARIFICATION_RAISE,
            ResourceRef(resource_type=self._clarifications.resource_type, project_id=project_id),
        )
        finding = self._findings.get(project_id, finding_id)
        if finding is None:
            raise ProjectIsolationError("quality finding not found in this project")
        if finding.status is not QualityFindingStatus.OPEN:
            raise ClarificationError(f"the finding is {finding.status}; only open findings")
        if self._clarifications.open_for_finding(project_id, finding.id) is not None:
            raise ClarificationError(
                "an open clarification already exists for this finding (architecture E #4)"
            )
        version = self._versions.get(project_id, finding.requirement_version_id)
        requirement = (
            self._requirements.get(project_id, version.requirement_id) if version else None
        )
        if version is None or requirement is None:  # pragma: no cover - foreign keys
            raise ProjectIsolationError("requirement version not found in this project")
        if requirement.current_version_id != version.id:
            raise ClarificationError("the finding is on a version that is no longer current")
        if version.state not in RAISE_PATHS:
            raise ClarificationError(
                f"a {version.state} version is not clarified in place; create a new version"
            )
        stakeholder = self._stakeholders.get(project_id, asked_of)
        if stakeholder is None:
            raise ProjectIsolationError("stakeholder not found in this project")
        prior = tuple(c.question for c in self._clarifications.for_version(project_id, version.id))
        masked, synthetic = source_facts(
            self._session, self._actor, project_id, version.source_refs or []
        )
        return RaiseContext(
            project_id, finding, version, requirement, stakeholder, prior, masked, synthetic
        )

    def record_raised(
        self,
        context: RaiseContext,
        *,
        question: str,
        expected_answer_shape: str,
        agent_run_id: uuid.UUID | None,
    ) -> Clarification:
        project_id = context.project_id
        now = utc_now()
        clarification_session = self._sessions.add(
            InterviewSession(
                project_id=project_id,
                stakeholder_id=context.stakeholder.id,
                kind=InterviewSessionKind.CLARIFICATION,
                sensitivity=(
                    DataSensitivity.SYNTHETIC if context.synthetic else DataSensitivity.UNCLASSIFIED
                ),
                status=InterviewSessionStatus.ACTIVE,
                topic_coverage={},
                followups_this_topic=0,
                questions_asked=1,
                followups_asked=0,
                started_by=self._actor.actor_id,
            ),
            action=Action.CLARIFICATION_RAISE,
        )
        question_utterance = self._utterances.add(
            Utterance(
                project_id=project_id,
                session_id=clarification_session.id,
                seq=1,
                speaker_kind=SpeakerKind.SYSTEM,
                text=question,
                is_followup=False,
                agent_run_id=agent_run_id,
                on_behalf=False,
            ),
            action=Action.CLARIFICATION_RAISE,
        )
        clarification_session.pending_question_id = question_utterance.id
        clarification = self._clarifications.add(
            Clarification(
                project_id=project_id,
                requirement_version_id=context.version.id,
                quality_finding_id=context.finding.id,
                question=question,
                expected_answer_shape=expected_answer_shape,
                status=ClarificationStatus.OPEN,
                asked_of_stakeholder_id=context.stakeholder.id,
                assignee_user_id=self._actor.actor_id,
                raised_by=self._actor.actor_id,
                session_id=clarification_session.id,
                question_utterance_id=question_utterance.id,
                agent_run_id=agent_run_id,
                created_at=now,
                updated_at=now,
            )
        )
        # The version enters CLARIFICATION_REQUIRED through the P1 table, as the
        # Analyst who raised it (H.3). Already there: nothing to do.
        requirements = RequirementService(self._session, self._actor)
        for target in RAISE_PATHS[context.version.state]:
            requirements.transition(
                project_id=project_id, version_id=context.version.id, target=target
            )
        self._event(
            AuditEventType.CLARIFICATION_RAISED,
            clarification,
            {
                "clarification_id": str(clarification.id),
                "finding_id": str(context.finding.id),
                "finding_type": str(context.finding.finding_type),
                "asked_of": str(context.stakeholder.id),
                "question_utterance_id": str(question_utterance.id),
            },
            agent_run_id=agent_run_id,
        )
        return clarification

    # -- answer and dismiss ------------------------------------------------
    def answer(
        self, *, project_id: ProjectId, clarification_id: uuid.UUID, text: str
    ) -> tuple[Clarification, Utterance]:
        """Record the answer (once) as an utterance; re-analysis follows (``FR-CLR-003``)."""
        require(
            self._actor,
            Action.CLARIFICATION_ANSWER,
            ResourceRef(resource_type=self._clarifications.resource_type, project_id=project_id),
        )
        clarification = self._clarifications.get(project_id, clarification_id)
        if clarification is None:
            raise ProjectIsolationError("not found")
        stakeholder = self._stakeholder(clarification)
        on_behalf = answering_on_behalf(self._actor, project_id, stakeholder)
        if clarification.status is ClarificationStatus.ANSWERED:
            raise ClarificationError("this clarification has already been answered")
        if clarification.status is ClarificationStatus.DISMISSED:
            raise ClarificationError("a dismissed clarification cannot be answered")
        text = text.strip()
        if not text:
            raise ClarificationError("an answer cannot be empty")
        if len(text) > self._rules.clarification_max_answer_chars:
            raise ClarificationError("the answer is too long")
        clarification_session = self._session_of(clarification)
        answer = self._utterances.add(
            Utterance(
                project_id=project_id,
                session_id=clarification_session.id,
                seq=self._utterances.next_seq(project_id, clarification_session.id),
                speaker_kind=SpeakerKind.STAKEHOLDER,
                speaker_ref=stakeholder.id,
                stakeholder_role=stakeholder.stakeholder_role,
                text=text,
                is_followup=False,
                replies_to_id=clarification.question_utterance_id,
                recorded_by=self._actor.actor_id,
                on_behalf=on_behalf,
            ),
            action=Action.CLARIFICATION_ANSWER,
        )
        now = utc_now()
        clarification.status = ClarificationStatus.ANSWERED
        clarification.answer_utterance_id = answer.id
        clarification.answered_by = self._actor.actor_id
        clarification.answered_at = now
        clarification.reanalysis_status = ReanalysisStatus.PENDING
        clarification.updated_at = now
        self._clarifications.save(clarification, action=Action.CLARIFICATION_ANSWER)
        self._close_session(clarification_session, Action.CLARIFICATION_ANSWER)
        self._audit.append(
            event_type=AuditEventType.UTTERANCE_RECORDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="interview_session",
            subject_id=str(clarification_session.id),
            payload={
                "utterance_id": str(answer.id),
                "seq": answer.seq,
                "speaker_kind": str(SpeakerKind.STAKEHOLDER),
                "on_behalf": on_behalf,
                "clarification_id": str(clarification.id),
            },
        )
        self._event(
            AuditEventType.CLARIFICATION_ANSWERED,
            clarification,
            {
                "clarification_id": str(clarification.id),
                "answer_utterance_id": str(answer.id),
                "on_behalf": on_behalf,
            },
        )
        return clarification, answer

    def dismiss(
        self, *, project_id: ProjectId, clarification_id: uuid.UUID, reason: str
    ) -> Clarification:
        """``FR-CLR-004``. The requirement and its finding are left exactly as they are."""
        require(
            self._actor,
            Action.CLARIFICATION_DISMISS,
            ResourceRef(resource_type=self._clarifications.resource_type, project_id=project_id),
        )
        clarification = self._clarifications.get(project_id, clarification_id)
        if clarification is None:
            raise ProjectIsolationError("not found")
        reason = " ".join(reason.split())
        if not reason:
            raise ClarificationError("dismissing a clarification requires a reason")
        if clarification.status is not ClarificationStatus.OPEN:
            raise ClarificationError(f"the clarification is {clarification.status}, not open")
        now = utc_now()
        clarification.status = ClarificationStatus.DISMISSED
        clarification.dismissed_reason = reason[:2000]
        clarification.dismissed_by = self._actor.actor_id
        clarification.dismissed_at = now
        clarification.updated_at = now
        self._clarifications.save(clarification, action=Action.CLARIFICATION_DISMISS)
        self._close_session(self._session_of(clarification), Action.CLARIFICATION_DISMISS)
        self._event(
            AuditEventType.CLARIFICATION_DISMISSED,
            clarification,
            {"clarification_id": str(clarification.id), "reason_chars": len(reason)},
        )
        return clarification

    # -- re-analysis bookkeeping (the pipeline) -------------------------------
    def record_reanalysis(
        self,
        clarification: Clarification,
        *,
        status: ReanalysisStatus,
        run_id: uuid.UUID,
        version_id: uuid.UUID | None,
    ) -> None:
        clarification.reanalysis_status = status
        clarification.reanalysis_run_id = run_id
        clarification.resulting_version_id = (
            version_id if status is ReanalysisStatus.NEW_VERSION else None
        )
        clarification.updated_at = utc_now()
        self._clarifications.save(clarification, action=Action.RUN_RECORD)
        self._event(
            AuditEventType.CLARIFICATION_REANALYSED,
            clarification,
            {
                "clarification_id": str(clarification.id),
                "outcome": str(status),
                "resulting_version_id": str(version_id) if version_id else None,
            },
            graph_run_id=run_id,
        )

    def answer_utterance(self, clarification: Clarification) -> Utterance | None:
        if clarification.answer_utterance_id is None:
            return None
        return self._utterances.get(
            ProjectId(clarification.project_id), clarification.answer_utterance_id
        )

    def question_utterance(self, clarification: Clarification) -> Utterance | None:
        return self._utterances.get(
            ProjectId(clarification.project_id), clarification.question_utterance_id
        )

    def stakeholder_of(self, clarification: Clarification) -> Stakeholder:
        return self._stakeholder(clarification)

    # -- internals -----------------------------------------------------------
    def _view(
        self,
        project_id: ProjectId,
        clarification: Clarification,
        stakeholder: Stakeholder,
        now: dt.datetime,
    ) -> IssueView:
        version = self._versions.get(project_id, clarification.requirement_version_id)
        requirement = (
            self._requirements.get(project_id, version.requirement_id) if version else None
        )
        finding = self._findings.get(project_id, clarification.quality_finding_id)
        if version is None or requirement is None or finding is None:  # pragma: no cover
            raise ClarificationError("a clarification's binding is missing")
        answer = self.answer_utterance(clarification)
        resulting = (
            self._versions.get(project_id, clarification.resulting_version_id)
            if clarification.resulting_version_id
            else None
        )
        created = clarification.created_at
        if created.tzinfo is None:  # SQLite returns naive timestamps
            created = created.replace(tzinfo=dt.UTC)
        return IssueView(
            clarification=clarification,
            requirement_human_id=requirement.human_id,
            version_no=version.version_no,
            statement=version.statement,
            finding=finding,
            stakeholder_name=stakeholder.name,
            answer=answer.text if answer else None,
            resulting_version_no=resulting.version_no if resulting else None,
            age=now - created,
        )

    def _stakeholder(self, clarification: Clarification) -> Stakeholder:
        stakeholder = self._stakeholders.get(
            ProjectId(clarification.project_id), clarification.asked_of_stakeholder_id
        )
        if stakeholder is None:  # pragma: no cover - a composite foreign key
            raise ClarificationError("the clarification's stakeholder is missing")
        return stakeholder

    def _session_of(self, clarification: Clarification) -> InterviewSession:
        row = self._sessions.get(ProjectId(clarification.project_id), clarification.session_id)
        if row is None:  # pragma: no cover - a foreign key
            raise ClarificationError("the clarification's session is missing")
        return row

    def _close_session(self, row: InterviewSession, action: Action) -> None:
        row.status = InterviewSessionStatus.COMPLETED
        row.pending_question_id = None
        row.completed_at = utc_now()
        row.updated_at = row.completed_at
        self._sessions.save(row, action=action)

    def _event(
        self,
        event: AuditEventType,
        clarification: Clarification,
        payload: dict,
        *,
        agent_run_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
    ) -> None:
        self._audit.append(
            event_type=event,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=ProjectId(clarification.project_id),
            subject_type="requirement_version",
            subject_id=str(clarification.requirement_version_id),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload=payload,
        )
