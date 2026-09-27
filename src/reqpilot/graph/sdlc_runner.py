"""The entry point of ``sdlc_graph`` (architecture C.5; roadmap phase P9).

Three operations, each one graph run executed synchronously within the caller's
transaction (the ``AnalysisRunner`` pattern):

* :meth:`SdlcRunner.start` - an analyst asks for a recommendation from one
  approved baseline;
* :meth:`SdlcRunner.override` - a human overrides one factor on a run with a
  recorded reason (``FR-SDL-003``). The override triggers a **full recompute**
  (architecture L.6): a new run, with every earlier override carried forward,
  that supersedes the run it was made on. The run acts as its system actor, so a
  Project Manager can override without being able to start runs;
* :meth:`SdlcRunner.explain` - an analyst retries the explanation of a ranked
  run whose explanation failed or was never generated.
* :meth:`SdlcRunner.generate_workflow` (P10) - an analyst or the Project Manager
  asks for the project workflow of a run that passed G6 (``FR-WFL-001``). The
  graph's ``workflow`` mode verifies G6 from the persisted records, derives,
  validates and stores the workflow, and renders it once. The run acts as its
  system actor, like a factor override's recompute.

A bug - an exception no node expected - is not caught: the request fails and its
transaction rolls back (architecture T, "fail loudly").
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import (
    Action,
    ExplanationStatus,
    GraphRunStatus,
    ResourceType,
    Role,
    SdlcRunStatus,
)
from reqpilot.domain.errors import SdlcError
from reqpilot.domain.ids import GraphRunId, ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.domain.sdlc.factors import FactorId
from reqpilot.domain.sdlc.profile import OverrideRecord
from reqpilot.graph.builder import build_checkpointer, checkpointer_scope, run_config
from reqpilot.graph.graphs.sdlc import GRAPH_NAME, build_sdlc_graph
from reqpilot.graph.nodes.sdlc import SdlcContext, SdlcNodes
from reqpilot.graph.state import SDLCState
from reqpilot.llm.accounting import UsageLedger
from reqpilot.llm.gateway import LLMGateway
from reqpilot.rules.risk import RiskRules, packaged_risk_rules
from reqpilot.rules.sdlc import SdlcRules, packaged_sdlc_rules
from reqpilot.services.extraction import RunLog, RunRecorder
from reqpilot.services.sdlc.service import SdlcService
from reqpilot.services.traceability.sync import TraceGraphSync


@dataclass(frozen=True)
class SdlcRunSummary:
    graph_run_id: uuid.UUID
    status: GraphRunStatus
    sdlc_run_id: uuid.UUID | None
    explanation_status: str | None
    g6_task_ids: tuple[uuid.UUID, ...]
    proposals_accepted: int
    proposals_rejected: int
    semantic_failures: int
    errors: tuple[str, ...]
    provider_calls: int
    tokens_in: int
    tokens_out: int
    # P10 (workflow mode); defaulted so every P9 construction is unchanged.
    workflow_id: uuid.UUID | None = None
    workflow_status: str | None = None
    workflow_reused: bool = False
    workflow_findings: tuple[dict[str, str], ...] = ()


class SdlcRunner:
    def __init__(
        self,
        session: Session,
        gateway: LLMGateway,
        *,
        settings: Settings | None = None,
        sdlc_rules: SdlcRules | None = None,
        risk_rules: RiskRules | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._settings = settings or get_settings()
        self._rules = sdlc_rules or packaged_sdlc_rules()
        self._risk_rules = risk_rules or packaged_risk_rules()

    def semantic_default(self) -> bool:
        """The LLM layer runs unless the gateway is the offline stub."""
        return self._gateway.provider_name != "stub"

    # ------------------------------------------------------------------
    def start(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        baseline_id: uuid.UUID,
        semantic: bool | None = None,
    ) -> SdlcRunSummary:
        require(
            actor,
            Action.SDLC_RUN_START,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        )
        use_semantic = self.semantic_default() if semantic is None else semantic
        return self._run(
            actor,
            project_id,
            {"mode": "start", "baseline_id": str(baseline_id), "semantic": use_semantic},
            scope={"mode": "sdlc", "baseline_id": str(baseline_id), "semantic": use_semantic},
        )

    def override(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        run_id: uuid.UUID,
        factor: str,
        new_score: int,
        reason: str,
        role: Role,
        semantic: bool | None = None,
    ) -> SdlcRunSummary:
        service = SdlcService(self._session, actor, self._rules)
        request = service.prepare_override(
            actor, project_id, run_id, factor, new_score, reason, role
        )
        overrides: dict[FactorId, OverrideRecord] = service.carried_overrides(
            project_id, request.run
        )
        overrides[request.record.factor] = request.record
        use_semantic = self.semantic_default() if semantic is None else semantic
        return self._run(
            actor,
            project_id,
            {
                "mode": "override",
                "baseline_id": str(request.run.baseline_id),
                "supersedes_run_id": str(request.run.id),
                "semantic": use_semantic,
            },
            scope={
                "mode": "sdlc_override",
                "sdlc_run_id": str(request.run.id),
                "factor": str(request.record.factor),
                "semantic": use_semantic,
            },
            trigger=Action.SDLC_FACTOR_OVERRIDE,
            overrides=overrides,
            new_override=request.record.factor,
        )

    def explain(self, *, actor: Actor, project_id: ProjectId, run_id: uuid.UUID) -> SdlcRunSummary:
        require(
            actor,
            Action.SDLC_EXPLAIN,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        )
        run = SdlcService(self._session, actor, self._rules).get(project_id, run_id)
        if run is None:
            raise SdlcError("SDLC run not found in this project")
        if run.status is not SdlcRunStatus.RANKED or run.explanation_status not in (
            ExplanationStatus.NOT_GENERATED,
            ExplanationStatus.FAILED,
        ):
            raise SdlcError(
                "only a ranked run without an explanation can have its explanation retried "
                f"(this run is {run.status}, explanation {run.explanation_status})"
            )
        return self._run(
            actor,
            project_id,
            {
                "mode": "explain",
                "baseline_id": str(run.baseline_id),
                "sdlc_run_id": str(run.id),
                "semantic": True,
            },
            scope={"mode": "sdlc_explain", "sdlc_run_id": str(run.id)},
        )

    def generate_workflow(
        self, *, actor: Actor, project_id: ProjectId, run_id: uuid.UUID
    ) -> SdlcRunSummary:
        """P10: the project workflow of a G6-selected run. Idempotent per approved inputs."""
        require(
            actor,
            Action.WORKFLOW_GENERATE,
            ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=project_id),
        )
        return self._run(
            actor,
            project_id,
            {"mode": "workflow", "sdlc_run_id": str(run_id), "semantic": False},
            scope={"mode": "workflow", "sdlc_run_id": str(run_id)},
            trigger=Action.WORKFLOW_GENERATE,
        )

    # ------------------------------------------------------------------
    def _run(
        self,
        actor: Actor,
        project_id: ProjectId,
        initial: dict[str, Any],
        *,
        scope: dict[str, Any],
        trigger: Action | None = None,
        overrides: dict[FactorId, OverrideRecord] | None = None,
        new_override: FactorId | None = None,
    ) -> SdlcRunSummary:
        run, pipeline = RunRecorder(self._session, actor).start(
            project_id,
            scope={**scope, "ruleset": self._rules.ruleset_ref},
            graph_name=GRAPH_NAME,
            trigger=trigger,
        )
        log = RunLog(self._session, pipeline, run)
        ledger = UsageLedger()
        context = SdlcContext(
            session=self._session,
            actor=pipeline,
            log=log,
            gateway=self._gateway.with_usage(ledger),
            rules=self._rules,
            risk_rules=self._risk_rules,
            initiator=actor.actor_id,
            overrides=dict(overrides or {}),
            new_override=new_override,
        )
        state: SDLCState = {
            "run_id": str(run.id),
            "project_id": str(project_id),
            "actor_id": str(actor.actor_id),
            "errors": [],
            "failed": False,
            "semantic_failures": 0,
            **initial,  # type: ignore[typeddict-item]
        }
        saver_scope = (
            nullcontext(build_checkpointer(self._settings))
            if self._settings.checkpoint_backend == "memory"
            else checkpointer_scope(self._settings)
        )
        with saver_scope as checkpointer:
            graph = build_sdlc_graph(SdlcNodes(context), checkpointer=checkpointer)
            final = graph.invoke(state, config=run_config(GraphRunId(run.id)))

        errors = tuple(e["message"] for e in final.get("errors", []))
        status = GraphRunStatus.FAILED if errors else GraphRunStatus.COMPLETED
        sdlc_run_id = final.get("sdlc_run_id")
        workflow_id = final.get("workflow_id")
        if initial.get("mode") == "workflow":
            trace_links = self._record_workflow_trace(actor, project_id, workflow_id)
        else:
            trace_links = self._record_trace(actor, project_id, sdlc_run_id)
        log.finish(
            status,
            sdlc_run_id=sdlc_run_id,
            explanation_status=final.get("explanation_status"),
            g6_tasks=len(final.get("g6_task_ids", [])),
            proposals_accepted=final.get("proposals_accepted", 0),
            proposals_rejected=final.get("proposals_rejected", 0),
            semantic_failures=final.get("semantic_failures", 0),
            trace_links=trace_links,
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            cost_estimate=ledger.cost_estimate,
        )
        return SdlcRunSummary(
            graph_run_id=run.id,
            status=status,
            sdlc_run_id=uuid.UUID(sdlc_run_id) if sdlc_run_id else None,
            explanation_status=final.get("explanation_status"),
            g6_task_ids=tuple(uuid.UUID(t) for t in final.get("g6_task_ids", [])),
            proposals_accepted=final.get("proposals_accepted", 0),
            proposals_rejected=final.get("proposals_rejected", 0),
            semantic_failures=final.get("semantic_failures", 0),
            errors=errors,
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            workflow_id=uuid.UUID(workflow_id) if workflow_id else None,
            workflow_status=final.get("workflow_status"),
            workflow_reused=bool(final.get("workflow_reused", False)),
            workflow_findings=tuple(final.get("workflow_findings", [])),
        )

    def _record_workflow_trace(
        self, human: Actor, project_id: ProjectId, workflow_id: str | None
    ) -> int:
        """N.2 #24-#26 for the new workflow, recorded as the human who asked for it.

        As for a run's edges: an analyst holds ``TRACE_SYNC`` and the edges exist at
        once; a Project Manager's request gets them at the next trace sync, derived
        from the same persisted provenance (:meth:`TraceGraphSync.workflow_edges`).
        """
        if workflow_id is None:
            return 0
        ref = ResourceRef(resource_type=ResourceType.TRACEABILITY_LINK, project_id=project_id)
        if not can(human, Action.TRACE_SYNC, ref):
            return 0
        sync = TraceGraphSync(self._session, human)
        return sync.record(project_id, sync.workflow_edges(project_id, [uuid.UUID(workflow_id)]))

    def _record_trace(self, human: Actor, project_id: ProjectId, sdlc_run_id: str | None) -> int:
        """The new run's trace edges, recorded as the human who caused the run.

        A trace link is asserted by a person, never by the pipeline (policy rule
        11). An analyst who starts a run holds ``TRACE_SYNC``, so its edges exist
        at once; a run a Project Manager's override caused gets them at the next
        trace sync, derived from the same rows (:meth:`TraceGraphSync.sdlc_edges`).
        """
        if sdlc_run_id is None:
            return 0
        ref = ResourceRef(resource_type=ResourceType.TRACEABILITY_LINK, project_id=project_id)
        if not can(human, Action.TRACE_SYNC, ref):
            return 0
        sync = TraceGraphSync(self._session, human)
        return sync.record(project_id, sync.sdlc_edges(project_id, [uuid.UUID(sdlc_run_id)]))
