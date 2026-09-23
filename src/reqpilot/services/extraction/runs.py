"""Run records: the Coordinator's deterministic bookkeeping (architecture E #1, G.7).

Graph runs, agent runs, the prompt and model provenance of every generation, and
the run/node audit events. The audit trail - not the LangGraph checkpoint - is
the record of what happened (C.7), so every node execution leaves a row here.

**The pipeline actor.** A run is started by a human analyst. The pipeline then
acts as a *system* actor whose identity is the run itself and whose only role is
Analyst in that one project. It can record what a model proposed and move a
version along the transitions the architecture lets extraction and
classification make; policy rule 6 stops it from doing anything a human must
decide - submitting, approving, withdrawing, overriding, resolving, merging
(architecture A.1, E.1).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    ActorKind,
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    GraphRunStatus,
    ResourceType,
    Role,
)
from reqpilot.domain.errors import PromptRegistryError
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.extraction import ModelVersion, PromptTemplate
from reqpilot.domain.models.runs import AgentRun, GraphRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.provenance import ModelMeta
from reqpilot.repositories.extraction import RunRepository
from reqpilot.services.audit import AuditService

ANALYSIS_GRAPH = "analysis_graph"

#: Human actions whose success *triggers* a run, which the person may then start
#: without ``RUN_START`` (P4). Answering a clarification triggers its
#: re-analysis (``FR-CLR-003``; architecture C.3 ``route_after_clarification``):
#: the stakeholder who answers cannot start runs, but the answer must be
#: re-analysed. The run still acts as its restricted system actor.
TRIGGERING_ACTIONS: frozenset[Action] = frozenset({Action.CLARIFICATION_ANSWER})


def pipeline_actor(initiator: Actor, project_id: ProjectId, run_id: uuid.UUID) -> Actor:
    """The system actor a run acts as, derived from the analyst who started it.

    It holds exactly one role, in exactly one project, and is never human.
    """
    require(
        initiator,
        Action.RUN_START,
        ResourceRef(resource_type=ResourceType.GRAPH_RUN, project_id=project_id),
    )
    return Actor(
        actor_id=ActorId(run_id),
        kind=ActorKind.SYSTEM,
        roles_by_project={project_id: frozenset({Role.ANALYST})},
    )


def run_actor(run: GraphRun) -> Actor:
    """The system actor of an already-started run, to continue it (P4).

    An interview is one run that lasts many requests, and the person answering a
    question - a stakeholder - may not start runs. Continuing the run as the
    identity it was started with is what :func:`pipeline_actor` would return;
    callers use this only after authorising the human's own action (answering,
    resuming) under the policy.
    """
    project_id = ProjectId(run.project_id)
    return Actor(
        actor_id=ActorId(run.id),
        kind=ActorKind.SYSTEM,
        roles_by_project={project_id: frozenset({Role.ANALYST})},
    )


class RunRecorder:
    """Writes one graph run's records. Created per run."""

    def __init__(self, session: Session, initiator: Actor) -> None:
        self._session = session
        self._initiator = initiator
        self._audit = AuditService(session)

    def start(
        self,
        project_id: ProjectId,
        *,
        scope: dict[str, Any],
        graph_name: str = ANALYSIS_GRAPH,
        trigger: Action | None = None,
    ) -> tuple[GraphRun, Actor]:
        """Open a run as the initiating human, and return it with its pipeline actor.

        ``trigger`` names the human action that caused a triggered run (one of
        :data:`TRIGGERING_ACTIONS`); the initiator must hold it in the project.
        """
        if trigger is not None and trigger not in TRIGGERING_ACTIONS:
            raise ValueError(f"{trigger} does not trigger runs")
        run_id = uuid.uuid4()
        run = GraphRun(
            id=run_id,
            project_id=project_id,
            graph_name=graph_name,
            thread_id=str(run_id),
            status=GraphRunStatus.RUNNING,
            started_by=self._initiator.actor_id,
        )
        RunRepository(self._session, self._initiator).add_run(
            run, action=trigger or Action.RUN_START
        )
        self._audit.append(
            event_type=AuditEventType.RUN_STARTED,
            actor_kind=self._initiator.kind,
            actor_ref=str(self._initiator.actor_id),
            project_id=project_id,
            subject_type="graph_run",
            subject_id=str(run.id),
            graph_run_id=run.id,
            payload={
                "graph": graph_name,
                **scope,
                **({"triggered_by": str(trigger)} if trigger else {}),
            },
        )
        if trigger is not None:
            return run, run_actor(run)
        return run, pipeline_actor(self._initiator, project_id, run.id)


class RunLog:
    """What a pipeline actor records during a run: nodes, agent runs, the end."""

    def __init__(self, session: Session, actor: Actor, run: GraphRun) -> None:
        self._session = session
        self._actor = actor
        self._run = run
        self._repo = RunRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def run(self) -> GraphRun:
        return self._run

    @property
    def project_id(self) -> ProjectId:
        return ProjectId(self._run.project_id)

    def _event(
        self,
        event_type: AuditEventType,
        *,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any],
        agent_run_id: uuid.UUID | None = None,
    ) -> None:
        self._audit.append(
            event_type=event_type,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=self.project_id,
            subject_type=subject_type,
            subject_id=subject_id,
            graph_run_id=self._run.id,
            agent_run_id=agent_run_id,
            payload=payload,
        )

    # -- nodes -------------------------------------------------------------
    def node_started(self, node: str) -> None:
        self._event(
            AuditEventType.NODE_STARTED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"node": node},
        )

    def node_completed(self, node: str, **counts: Any) -> None:
        self._event(
            AuditEventType.NODE_COMPLETED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"node": node, **counts},
        )

    def node_failed(self, node: str, error_code: str) -> None:
        self._run.failure_count += 1
        self._repo.update_run(self._run)
        self._event(
            AuditEventType.NODE_FAILED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"node": node, "error_code": error_code},
        )

    # -- agent runs ----------------------------------------------------------
    def agent_run(
        self,
        *,
        node: str,
        role: AgentRole,
        status: AgentRunStatus,
        started_at: dt.datetime,
        meta: ModelMeta | None = None,
        prompt_role: AgentRole | None = None,
        prompt_text: str | None = None,
        input_refs: dict[str, Any] | None = None,
        output_refs: dict[str, Any] | None = None,
        error_code: str | None = None,
        review_signal: float | None = None,
        evidence_ids: list[str] | None = None,
    ) -> AgentRun:
        """Record one role invocation, with its prompt and model provenance.

        ``evidence_ids`` (P6): the evidence supplied to the invocation - the run's
        citable set for that call (architecture G.7 ``agent_run.evidence_ids``).
        """
        prompt_id: str | None = None
        model_id: str | None = None
        output_refs = dict(output_refs or {})
        if meta is not None:
            if prompt_text is None or prompt_role is None:
                raise ValueError("an LLM invocation records the prompt text it used")
            prompt_id = str(self._prompt_template(meta, prompt_role, prompt_text).id)
            model_id = str(self._model_version(meta).id)
            if meta.response_ids:
                # The provider's identifiers - references to its calls, not content.
                output_refs["provider_response_ids"] = list(meta.response_ids)
        agent_run = AgentRun(
            graph_run_id=self._run.id,
            node=node,
            role=role,
            prompt_template_id=prompt_id,
            model_version_id=model_id,
            input_refs=input_refs or {},
            output_refs=output_refs,
            evidence_ids=list(evidence_ids or []),
            tokens_in=meta.tokens_in if meta else None,
            tokens_out=meta.tokens_out if meta else None,
            latency_ms=meta.latency_ms if meta else None,
            status=status,
            started_at=started_at,
            attempts=meta.attempts if meta else None,
            error_code=error_code,
            review_signal=review_signal,
            cost_estimate=meta.cost_estimate if meta else None,
            finished_at=utc_now(),
        )
        return self._repo.add_agent_run(self.project_id, agent_run)

    def _prompt_template(self, meta: ModelMeta, role: AgentRole, text: str) -> PromptTemplate:
        existing = self._repo.prompt_template(
            self.project_id, name=meta.prompt_name, version=meta.prompt_version
        )
        if existing is not None:
            if existing.template_sha256 != meta.prompt_sha256:
                # The registry's lock makes this unreachable in one checkout; it
                # guards a database shared across checkouts with different text.
                raise PromptRegistryError(
                    f"{meta.prompt_ref} was recorded with different text; bump its version"
                )
            return existing
        return self._repo.add_prompt_template(
            self.project_id,
            PromptTemplate(
                name=meta.prompt_name,
                role=role,
                version=meta.prompt_version,
                contract_version=meta.contract_version,
                template_sha256=meta.prompt_sha256,
                template_text=text,
            ),
        )

    def _model_version(self, meta: ModelMeta) -> ModelVersion:
        existing = self._repo.model_version(
            self.project_id,
            provider=meta.provider,
            model_id=meta.model_id,
            params_hash=meta.params_hash,
        )
        if existing is not None:
            return existing
        return self._repo.add_model_version(
            self.project_id,
            ModelVersion(
                provider=meta.provider,
                model_id=meta.model_id,
                params_hash=meta.params_hash,
                params=dict(meta.params),
                is_model=meta.is_model,
            ),
        )

    # -- the end -------------------------------------------------------------
    # -- interrupt and resume (P4: elicitation_graph) -----------------------
    def suspend(self, **payload: Any) -> None:
        """The graph is waiting for a person (architecture C.7 interrupt)."""
        if self._run.status is GraphRunStatus.SUSPENDED:
            return
        self._run.status = GraphRunStatus.SUSPENDED
        self._repo.update_run(self._run)
        self._event(
            AuditEventType.RUN_SUSPENDED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"status": str(GraphRunStatus.SUSPENDED), **payload},
        )

    def resume(self, **payload: Any) -> None:
        if self._run.status is GraphRunStatus.RUNNING:
            return
        self._run.status = GraphRunStatus.RUNNING
        self._repo.update_run(self._run)
        self._event(
            AuditEventType.RUN_RESUMED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload=payload,
        )

    def stall(self, reason_code: str) -> None:
        """A step failed safely; the run waits for an analyst to retry it."""
        self._run.status = GraphRunStatus.STALLED
        self._repo.update_run(self._run)
        self._event(
            AuditEventType.RUN_SUSPENDED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"status": str(GraphRunStatus.STALLED), "reason_code": reason_code[:100]},
        )

    def finish(self, status: GraphRunStatus, **summary: Any) -> GraphRun:
        if status not in (GraphRunStatus.COMPLETED, GraphRunStatus.FAILED):
            raise ValueError("a run finishes as completed or failed")
        self._run.status = status
        self._run.finished_at = utc_now()
        self._repo.update_run(self._run)
        self._event(
            AuditEventType.RUN_COMPLETED
            if status is GraphRunStatus.COMPLETED
            else AuditEventType.RUN_FAILED,
            subject_type="graph_run",
            subject_id=str(self._run.id),
            payload={"status": str(status), **summary},
        )
        return self._run
