"""The interview entry point: start, answer, pause, resume (architecture C.4, C.7; P4).

One interview is **one** ``elicitation_graph`` run and **one** LangGraph thread,
interrupted at every question and resumed by the next answer - never a new run
per answer. Between requests the thread lives in the checkpointer (PostgreSQL in
a durable deployment, C.7); everything it refers to lives in the database.

The order of operations on an answer is what keeps the two consistent:

1. authorise the person and the session (policy + "only their own session");
2. check that the thread is suspended at ``await_answer`` for exactly the
   question the database says is pending;
3. **persist the answer** (append-only utterance), then
4. resume the thread with a server-built value carrying only the answer's id.

If step 2 finds the checkpoint missing or out of step with the database (a
crash between a checkpoint write and a commit, say), the thread is restarted from
``load_session``, which re-derives its position from the database: a stale
checkpoint degrades to a safe restart, never to a duplicated or lost turn.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import (
    Action,
    DataSensitivity,
    GraphRunStatus,
    InterviewSessionKind,
    InterviewSessionStatus,
)
from reqpilot.domain.errors import ElicitationError
from reqpilot.domain.ids import GraphRunId, ProjectId
from reqpilot.domain.models.elicitation import InterviewSession, Utterance
from reqpilot.domain.models.runs import GraphRun
from reqpilot.domain.policy import Actor
from reqpilot.graph.builder import checkpointer_scope, run_config
from reqpilot.graph.graphs.elicitation import GRAPH_NAME, build_elicitation_graph
from reqpilot.graph.nodes.elicitation import ElicitationContext, ElicitationNodes
from reqpilot.llm.accounting import UsageLedger
from reqpilot.llm.gateway import LLMGateway
from reqpilot.repositories.elicitation import InterviewSessionRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.elicitation import CoverageView, InterviewSessionService
from reqpilot.services.extraction import RunLog, RunRecorder, run_actor


@dataclass(frozen=True)
class InterviewTurn:
    """Where an interview stands after a request."""

    session: InterviewSession
    question: Utterance | None
    coverage: CoverageView
    #: Model calls this request made, and their tokens (diagnostics only).
    provider_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def complete(self) -> bool:
        return self.session.status is InterviewSessionStatus.COMPLETED

    @property
    def stalled(self) -> bool:
        return self.session.status is InterviewSessionStatus.STALLED


class ElicitationRunner:
    def __init__(
        self,
        session: Session,
        gateway: LLMGateway,
        rules: ElicitationRules,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._rules = rules
        self._settings = settings or get_settings()

    # -- people ------------------------------------------------------------
    def start(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        stakeholder_id: uuid.UUID,
        sensitivity: DataSensitivity,
        template_id: str | None = None,
    ) -> InterviewTurn:
        """Create the session, start its run, and ask the first question."""
        service = InterviewSessionService(self._session, actor, self._rules)
        row = service.create(
            project_id=project_id,
            stakeholder_id=stakeholder_id,
            template_id=template_id,
            sensitivity=sensitivity,
        )
        run, pipeline = RunRecorder(self._session, actor).start(
            project_id,
            scope={"mode": "interview", "session_id": str(row.id), "template": row.template_id},
            graph_name=GRAPH_NAME,
        )
        row.graph_run_id = run.id
        InterviewSessionRepository(self._session, actor).save(row, action=Action.SESSION_CREATE)
        return self._invoke(row, run, pipeline, resume_value=None)

    def answer(
        self, *, actor: Actor, project_id: ProjectId, session_id: uuid.UUID, text: str
    ) -> InterviewTurn:
        """Persist the answer, then resume the interview's own thread."""
        service = InterviewSessionService(self._session, actor, self._rules)
        row = service.require(project_id, session_id)
        run = self._run(row)
        in_step = self._suspended_at_pending_question(run, row)
        answer = service.record_answer(row, text)  # refuses a paused/complete session
        resume = (
            {
                "answer_utterance_id": str(answer.id),
                "question_utterance_id": str(answer.replies_to_id),
            }
            if in_step
            else None
        )
        return self._invoke(row, run, run_actor(run), resume_value=resume)

    def pause(self, *, actor: Actor, project_id: ProjectId, session_id: uuid.UUID) -> InterviewTurn:
        service = InterviewSessionService(self._session, actor, self._rules)
        row = service.pause(service.require(project_id, session_id))
        return self._turn(actor, row)

    def resume(
        self, *, actor: Actor, project_id: ProjectId, session_id: uuid.UUID
    ) -> InterviewTurn:
        """Resume a paused session, or retry a stalled one (``FR-ELI-005``).

        A paused session whose thread still waits at its pending question is
        simply reopened: no new question is generated, so none is duplicated.
        Anything else continues from ``load_session``.
        """
        service = InterviewSessionService(self._session, actor, self._rules)
        row = service.require(project_id, session_id)
        run = self._run(row)
        in_step = row.pending_question_id is not None and self._suspended_at_pending_question(
            run, row
        )
        service.reactivate(row)
        if in_step:
            return self._turn(actor, row)
        return self._invoke(row, run, run_actor(run), resume_value=None)

    def thread_position(self, row: InterviewSession) -> tuple[tuple[str, ...], dict[str, Any]]:
        """The checkpointed thread's next node(s) and state values (ids only)."""
        run = self._run(row)
        with checkpointer_scope(self._settings, shared_memory=True) as checkpointer:
            snapshot = self._graph(run, row, checkpointer, UsageLedger()).get_state(
                run_config(GraphRunId(run.id))
            )
        return tuple(snapshot.next), dict(snapshot.values)

    def turn(self, *, actor: Actor, project_id: ProjectId, session_id: uuid.UUID) -> InterviewTurn:
        service = InterviewSessionService(self._session, actor, self._rules)
        return self._turn(actor, service.require(project_id, session_id))

    # -- internals -----------------------------------------------------------
    def _run(self, row: InterviewSession) -> GraphRun:
        if row.kind is not InterviewSessionKind.INTERVIEW or row.graph_run_id is None:
            raise ElicitationError("this session has no interview run")
        run = self._session.get(GraphRun, row.graph_run_id)
        if run is None or run.project_id != row.project_id:  # pragma: no cover - a foreign key
            raise ElicitationError("the session's run is missing")
        return run

    def _graph(
        self, run: GraphRun, row: InterviewSession, checkpointer: Any, ledger: UsageLedger
    ) -> Any:
        pipeline = run_actor(run)
        context = ElicitationContext(
            session=self._session,
            actor=pipeline,
            log=RunLog(self._session, pipeline, run),
            gateway=self._gateway.with_usage(ledger),
            rules=self._rules,
            session_id=row.id,
        )
        return build_elicitation_graph(ElicitationNodes(context), checkpointer=checkpointer)

    def _suspended_at_pending_question(self, run: GraphRun, row: InterviewSession) -> bool:
        """Is the thread interrupted at ``await_answer`` for the question on record?"""
        if row.pending_question_id is None:
            return False
        with checkpointer_scope(self._settings, shared_memory=True) as checkpointer:
            graph = self._graph(run, row, checkpointer, UsageLedger())
            snapshot = graph.get_state(run_config(GraphRunId(run.id)))
        return tuple(snapshot.next) == ("await_answer",) and snapshot.values.get(
            "pending_question_id"
        ) == str(row.pending_question_id)

    def _invoke(
        self,
        row: InterviewSession,
        run: GraphRun,
        pipeline: Actor,
        *,
        resume_value: dict[str, str] | None,
    ) -> InterviewTurn:
        log = RunLog(self._session, pipeline, run)
        if run.status in (GraphRunStatus.SUSPENDED, GraphRunStatus.STALLED):
            log.resume(session_id=str(row.id))
        ledger = UsageLedger()
        config = run_config(GraphRunId(run.id))
        with checkpointer_scope(self._settings, shared_memory=True) as checkpointer:
            graph = self._graph(run, row, checkpointer, ledger)
            if resume_value is not None:
                graph.invoke(Command(resume=resume_value), config=config)
            else:
                graph.invoke(
                    {
                        "run_id": str(run.id),
                        "project_id": str(row.project_id),
                        "actor_id": str(pipeline.actor_id),
                        "session_id": str(row.id),
                    },
                    config=config,
                )
            snapshot = graph.get_state(config)
        self._session.refresh(row)
        if tuple(snapshot.next) == ("await_answer",):
            log.suspend(session_id=str(row.id))
        return self._turn(pipeline, row, ledger)

    def _turn(
        self, actor: Actor, row: InterviewSession, ledger: UsageLedger | None = None
    ) -> InterviewTurn:
        service = InterviewSessionService(self._session, actor, self._rules)
        question = (
            service.utterance(row, row.pending_question_id) if row.pending_question_id else None
        )
        return InterviewTurn(
            session=row,
            question=question,
            coverage=service.coverage(row),
            provider_calls=ledger.calls if ledger else 0,
            tokens_in=ledger.tokens_in if ledger else 0,
            tokens_out=ledger.tokens_out if ledger else 0,
        )
