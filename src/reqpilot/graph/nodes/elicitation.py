"""The nodes of ``elicitation_graph`` (architecture C.4, E #2; FR-ELI-001..006).

========================  ==============  ======================================
Node                      Kind            What it does
========================  ==============  ======================================
``load_session``          deterministic   re-derives the position from the DB
``select_next_topic``     deterministic   the coverage tracker picks the topic
``generate_question``     LLM (#2)        proposes a question; code validates it
``await_answer``          **interrupt**   suspends until a person answers
``record_utterance``      deterministic   binds the persisted answer
``assess_answer``         LLM (#2)        proposes an assessment; the tracker acts
``end_interview``         deterministic   coverage complete
``stall``                 deterministic   a step failed safely; an analyst retries
========================  ==============  ======================================

"The LLM proposes; deterministic code disposes": the model's question is asked
only if validation accepts it, and its assessment only *informs* the coverage
tracker, which alone moves coverage and the follow-up counter. Routers read the
flags these nodes set, never model text.

Graph state carries ids and counters; the durable ``interview_session`` row is
the source of truth, and ``load_session`` re-derives the graph's position from
it, so a lost or stale checkpoint degrades to a safe restart, never to a
duplicated question or a skipped answer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt
from sqlalchemy.orm import Session

from reqpilot.agents.contracts.elicitation import (
    AnswerAssessmentInput,
    InterviewTurnInput,
    TurnView,
)
from reqpilot.agents.roles import StakeholderInteractionRole
from reqpilot.agents.validation import validate_assessment, validate_question
from reqpilot.domain import coverage as tracker
from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    AnswerStatus,
    AuditEventType,
    DataSensitivity,
    GraphRunStatus,
    InterviewSessionStatus,
    SpeakerKind,
    TopicStatus,
)
from reqpilot.domain.errors import EgressRefusedError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import InterviewSession, Utterance
from reqpilot.domain.policy import Actor
from reqpilot.graph.state import ElicitationState
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import StructuredResult
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.elicitation import InterviewSessionService
from reqpilot.services.extraction import RunLog


@dataclass
class ElicitationContext:
    """The transient tier of one request's part of an interview (D.1). Never checkpointed."""

    session: Session
    actor: Actor
    log: RunLog
    gateway: LLMGateway
    rules: ElicitationRules
    session_id: uuid.UUID

    @property
    def project_id(self) -> ProjectId:
        return self.log.project_id

    @property
    def sessions(self) -> InterviewSessionService:
        return InterviewSessionService(self.session, self.actor, self.rules)


def _statuses(row: InterviewSession) -> dict[str, str]:
    return {topic: str(entry["status"]) for topic, entry in row.topic_coverage.items()}


class ElicitationNodes:
    def __init__(self, ctx: ElicitationContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    def load_session(self, state: ElicitationState) -> dict[str, Any]:
        """Where is the interview? Answered from the durable row alone."""
        node, ctx = "load_session", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        if row.status is InterviewSessionStatus.COMPLETED:
            point = "end_interview"
        elif row.status is not InterviewSessionStatus.ACTIVE:
            return self._failed(node, "session_not_active")
        elif row.unassessed_answer_id is not None:
            point = "assess_answer"
        elif row.pending_question_id is not None:
            point = "await_answer"
        elif (
            row.current_topic is not None
            and tracker.status_of(row.topic_coverage, row.current_topic) is TopicStatus.IN_PROGRESS
        ):
            # A topic was selected (or a follow-up earned) and its question not
            # yet recorded: generate it now.
            point = "generate_question"
        else:
            point = "select_next_topic"
        self._deterministic(node, {"resume_point": point})
        ctx.log.node_completed(node, resume_point=point)
        return {
            "session_id": str(row.id),
            "stakeholder_id": str(row.stakeholder_id),
            "topic_coverage": _statuses(row),
            "current_topic": row.current_topic,
            "followups_this_topic": row.followups_this_topic,
            "pending_question_id": str(row.pending_question_id)
            if row.pending_question_id
            else None,
            "answer_utterance_id": str(row.unassessed_answer_id)
            if row.unassessed_answer_id
            else None,
            "awaiting_followup": row.followups_this_topic > 0,
            "resume_point": point,
            "complete": point == "end_interview",
            "failure": None,
            "current_node": node,
        }

    # ------------------------------------------------------------------
    def select_next_topic(self, state: ElicitationState) -> dict[str, Any]:
        """The coverage tracker - not the model - picks the next topic (C.4)."""
        node, ctx = "select_next_topic", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        topic = tracker.select_next_topic(row.topic_coverage)
        if topic is None:
            self._deterministic(node, {"complete": True})
            ctx.log.node_completed(node, complete=True)
            return {"complete": True, "current_topic": None, "current_node": node}
        if tracker.status_of(row.topic_coverage, topic) is TopicStatus.NOT_STARTED:
            row.topic_coverage = tracker.start_topic(row.topic_coverage, topic)
        row.current_topic = topic
        row.followups_this_topic = 0
        row.pending_issue = None
        ctx.sessions.save_progress(row)
        self._deterministic(node, {"topic": topic})
        ctx.log.node_completed(node, topic=topic)
        return {
            "current_topic": topic,
            "followups_this_topic": 0,
            "awaiting_followup": False,
            "topic_coverage": _statuses(row),
            "complete": False,
            "current_node": node,
        }

    # ------------------------------------------------------------------
    def generate_question(self, state: ElicitationState) -> dict[str, Any]:
        """Role #2 proposes; validation disposes; at most ``max_question_attempts``."""
        node, ctx = "generate_question", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        service = ctx.sessions
        template = service.template(row)
        topic_id = row.current_topic
        if topic_id is None:
            return self._failed(node, "no_current_topic")
        topic = ctx.rules.topics[topic_id]
        template_topic = template.topic(topic_id)
        if template_topic is None:  # pragma: no cover - coverage comes from the template
            return self._failed(node, "topic_not_in_template")
        is_followup = row.followups_this_topic > 0
        utterances = service.utterances(row)
        summary = tracker.summarise(row.topic_coverage)
        turn = InterviewTurnInput(
            template_title=template.title,
            topic_id=topic_id,
            topic_title=topic.title,
            topic_description=topic.description,
            expected_answer_shape=template_topic.expected_answer_shape,
            covered_topics=summary.covered + summary.unresolved,
            remaining_topics=summary.remaining,
            recent_turns=tuple(
                TurnView(
                    speaker="interviewer"
                    if u.speaker_kind is SpeakerKind.SYSTEM
                    else "stakeholder",
                    text=u.text,
                    topic_id=u.topic_id,
                )
                for u in utterances[-ctx.rules.recent_turns_in_prompt :]
            ),
            is_followup=is_followup,
            followup_depth=row.followups_this_topic,
            max_followups=ctx.rules.max_followups_per_topic,
            issue=row.pending_issue if is_followup else None,
            synthetic=row.sensitivity is DataSensitivity.SYNTHETIC,
            masked=False,
        )
        asked = [u.text for u in utterances if u.speaker_kind is SpeakerKind.SYSTEM]
        last_answer = next(
            (
                u.text
                for u in reversed(utterances)
                if u.speaker_kind is SpeakerKind.STAKEHOLDER and u.topic_id == topic_id
            ),
            None,
        )
        role = StakeholderInteractionRole(ctx.gateway)
        findings: tuple[str, ...] = ()
        for attempt in range(1, ctx.rules.max_question_attempts + 1):
            started = utc_now()
            try:
                result = role.propose_question(turn)
            except EgressRefusedError:
                return self._failed(node, "egress_refused", role=role.role, started=started)
            if result.value is None:
                self._llm(node, role.role, started, result, {"topic": topic_id, "attempt": attempt})
                return self._failed(node, str(result.error_code))
            decision = validate_question(
                result.value,
                expected_topic=topic,
                expected_followup=is_followup,
                asked_before=asked,
                issue=row.pending_issue,
                last_answer=last_answer,
                rules=ctx.rules,
            )
            agent_run = self._llm(
                node,
                role.role,
                started,
                result,
                {"topic": topic_id, "is_followup": is_followup, "attempt": attempt},
                output_refs={"accepted": decision.accepted, "findings": list(decision.findings)},
                status=AgentRunStatus.OK if decision.accepted else AgentRunStatus.PARTIAL,
            )
            if decision.accepted:
                question = service.record_question(
                    row,
                    text=decision.question,
                    topic_id=topic_id,
                    is_followup=is_followup,
                    agent_run_id=agent_run.id,
                )
                ctx.log.node_completed(node, topic=topic_id, is_followup=is_followup)
                return {
                    "pending_question_id": str(question.id),
                    "topic_coverage": _statuses(row),
                    "current_node": node,
                }
            findings = decision.findings
        # Never ask a question validation refused; never loop: stall for a person.
        return self._failed(node, f"question_rejected: {findings[0] if findings else 'invalid'}")

    # ------------------------------------------------------------------
    def await_answer(self, state: ElicitationState) -> dict[str, Any]:
        """A real LangGraph interrupt (C.4, C.7). Side-effect free before it.

        The interrupt carries ids only. The resume value is built by the server,
        never taken from a client, and carries only the id of the answer the
        server has already persisted; it is checked against this thread's own
        pending question before anything continues.
        """
        pending = state.get("pending_question_id")
        value = interrupt({"session_id": state.get("session_id"), "question_id": pending})
        node = "await_answer"
        self.ctx.log.node_started(node)
        if (
            not isinstance(value, dict)
            or set(value) != {"answer_utterance_id", "question_utterance_id"}
            or value.get("question_utterance_id") != pending
        ):
            return self._failed(node, "invalid_resume")
        self.ctx.log.node_completed(node)
        return {"answer_utterance_id": str(value["answer_utterance_id"]), "current_node": node}

    # ------------------------------------------------------------------
    def record_utterance(self, state: ElicitationState) -> dict[str, Any]:
        """Bind the persisted answer to the session (``FR-ELI-004``)."""
        node, ctx = "record_utterance", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        answer = self._answer(row, state.get("answer_utterance_id"))
        if answer is None:
            return self._failed(node, "answer_mismatch")
        if answer.topic_id is not None:
            row.topic_coverage = tracker.record_answer(
                row.topic_coverage, answer.topic_id, seq=answer.seq
            )
        row.pending_question_id = None
        ctx.sessions.save_progress(row)
        self._deterministic(node, {"answer_seq": answer.seq})
        ctx.log.node_completed(node, answer_seq=answer.seq)
        return {"pending_question_id": None, "current_node": node}

    # ------------------------------------------------------------------
    def assess_answer(self, state: ElicitationState) -> dict[str, Any]:
        """Role #2 proposes an assessment; the coverage tracker decides (FR-ELI-003)."""
        node, ctx = "assess_answer", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        service = ctx.sessions
        answer = self._answer(row, str(row.unassessed_answer_id or ""))
        if answer is None or row.current_topic is None:
            return self._failed(node, "no_answer_to_assess")
        topic_id = row.current_topic
        topic = ctx.rules.topics[topic_id]
        template_topic = service.template(row).topic(topic_id)
        question = service.utterance(row, answer.replies_to_id) if answer.replies_to_id else None
        earlier = tuple(
            u.text
            for u in service.utterances(row)
            if u.speaker_kind is SpeakerKind.STAKEHOLDER
            and u.topic_id == topic_id
            and u.id != answer.id
        )
        item = AnswerAssessmentInput(
            topic_id=topic_id,
            topic_title=topic.title,
            topic_description=topic.description,
            expected_answer_shape=template_topic.expected_answer_shape if template_topic else "",
            question=question.text if question else "",
            answer=answer.text,
            earlier_answers=earlier,
            synthetic=row.sensitivity is DataSensitivity.SYNTHETIC,
            masked=False,
        )
        role = StakeholderInteractionRole(ctx.gateway)
        status: AnswerStatus | None = None
        issue: str | None = None
        for attempt in range(1, ctx.rules.max_question_attempts + 1):
            started = utc_now()
            try:
                result = role.assess_answer(item)
            except EgressRefusedError:
                return self._failed(node, "egress_refused", role=role.role, started=started)
            if result.value is None:
                self._llm(node, role.role, started, result, {"topic": topic_id, "attempt": attempt})
                return self._failed(node, str(result.error_code))
            decision = validate_assessment(result.value, expected_topic=topic_id)
            self._llm(
                node,
                role.role,
                started,
                result,
                {"topic": topic_id, "answer_seq": answer.seq, "attempt": attempt},
                output_refs={"status": str(decision.status), "findings": list(decision.findings)},
                status=AgentRunStatus.OK if decision.accepted else AgentRunStatus.PARTIAL,
            )
            if decision.accepted:
                status, issue = decision.status, decision.issue
                break
        if status is None:
            return self._failed(node, "assessment_rejected")

        outcome = tracker.decide_after_assessment(
            status,
            followups_this_topic=row.followups_this_topic,
            max_followups=ctx.rules.max_followups_per_topic,
        )
        row.topic_coverage = tracker.apply_outcome(row.topic_coverage, topic_id, outcome, status)
        row.followups_this_topic = outcome.followups_this_topic
        row.pending_issue = issue if outcome.route is tracker.Route.FOLLOW_UP else None
        row.unassessed_answer_id = None
        if outcome.route is tracker.Route.ADVANCE:
            row.current_topic = None
        service.save_progress(row)
        service.audit(
            AuditEventType.ANSWER_ASSESSED,
            row,
            {
                "answer_utterance_id": str(answer.id),
                "topic": topic_id,
                "status": str(status),
                "route": str(outcome.route),
                "topic_status": str(outcome.topic_status),
                "followups_this_topic": outcome.followups_this_topic,
                "max_followups": ctx.rules.max_followups_per_topic,
            },
        )
        ctx.log.node_completed(node, status=str(status), route=str(outcome.route))
        return {
            "answer_utterance_id": None,
            "followups_this_topic": outcome.followups_this_topic,
            "awaiting_followup": outcome.route is tracker.Route.FOLLOW_UP,
            "last_assessment": str(status),
            "topic_coverage": _statuses(row),
            "current_topic": row.current_topic,
            "current_node": node,
        }

    # ------------------------------------------------------------------
    def end_interview(self, state: ElicitationState) -> dict[str, Any]:
        node, ctx = "end_interview", self.ctx
        ctx.log.node_started(node)
        row = self._row()
        if row.status is not InterviewSessionStatus.COMPLETED:
            ctx.sessions.mark_completed(row)
        self._deterministic(node, {"complete": True})
        ctx.log.node_completed(node)
        if ctx.log.run.status is not GraphRunStatus.COMPLETED:
            ctx.log.finish(
                GraphRunStatus.COMPLETED,
                questions=row.questions_asked,
                followups=row.followups_asked,
            )
        return {"complete": True, "current_node": node}

    def stall(self, state: ElicitationState) -> dict[str, Any]:
        """A step failed safely. The session waits for an analyst; nothing advanced."""
        node, ctx = "stall", self.ctx
        reason = state.get("failure") or "unknown"
        row = self._row()
        if row.status is InterviewSessionStatus.ACTIVE:
            ctx.sessions.mark_stalled(row, reason)
        ctx.log.stall(reason)
        return {"current_node": node}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _row(self) -> InterviewSession:
        return self.ctx.sessions.require(self.ctx.project_id, self.ctx.session_id)

    def _answer(self, row: InterviewSession, raw: str | None) -> Utterance | None:
        """The answer awaiting assessment - only if it is exactly the one on record."""
        if not raw or row.unassessed_answer_id is None or str(row.unassessed_answer_id) != raw:
            return None
        answer = self.ctx.sessions.utterance(row, uuid.UUID(raw))
        if answer is None or answer.speaker_kind is not SpeakerKind.STAKEHOLDER:
            return None
        return answer

    def _failed(
        self,
        node: str,
        code: str,
        *,
        role: AgentRole = AgentRole.STAKEHOLDER_INTERACTION,
        started: dt.datetime | None = None,
    ) -> dict[str, Any]:
        self.ctx.log.agent_run(
            node=node,
            role=role,
            status=AgentRunStatus.FAILED,
            started_at=started or utc_now(),
            error_code=code[:100],
        )
        self.ctx.log.node_failed(node, code[:100])
        return {"failure": code[:300], "current_node": node}

    def _llm(
        self,
        node: str,
        role: AgentRole,
        started: dt.datetime,
        result: StructuredResult[Any],
        input_refs: dict[str, Any],
        *,
        output_refs: dict[str, Any] | None = None,
        status: AgentRunStatus | None = None,
    ) -> Any:
        spec = self.ctx.gateway.prompts.get(result.meta.prompt_name)
        return self.ctx.log.agent_run(
            node=node,
            role=role,
            status=status or (AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED),
            started_at=started,
            meta=result.meta,
            prompt_role=spec.role,
            prompt_text=spec.text,
            input_refs={"session_id": str(self.ctx.session_id), **input_refs},
            output_refs={**(output_refs or {}), "output_sha256": result.output_sha256},
            error_code=str(result.error_code) if result.error_code else None,
        )

    def _deterministic(self, node: str, output_refs: dict[str, Any]) -> None:
        self.ctx.log.agent_run(
            node=node,
            role=AgentRole.COORDINATOR,
            status=AgentRunStatus.OK,
            started_at=utc_now(),
            input_refs={"session_id": str(self.ctx.session_id)},
            output_refs=output_refs,
        )
