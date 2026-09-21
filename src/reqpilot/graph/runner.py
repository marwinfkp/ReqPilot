"""The Coordinator's run entry point (architecture E #1, C.1: ``GraphRunner``).

Starts one ``analysis_graph`` run in **batch mode**: the analyst supplies sources
that already exist, the run extracts and classifies, and it ends. There is no
interview loop and no resume from a human answer - that is the elicitation
graph, roadmap phase P4.

The run executes synchronously within the caller's transaction, one node after
another. Each node records what it did as it goes; the final status is decided
by whether any node recorded an error. A bug - an exception no node expected -
is not caught: the request fails and its transaction rolls back, which is the
architecture's "fail loudly" (T).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import GraphRunStatus
from reqpilot.domain.errors import ReqPilotError
from reqpilot.domain.ids import GraphRunId, ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.domain.requirement_ids import normalise_domain
from reqpilot.graph.builder import build_checkpointer, run_config
from reqpilot.graph.graphs.analysis import build_analysis_graph
from reqpilot.graph.nodes.analysis import AnalysisNodes, RunContext
from reqpilot.graph.state import AnalysisState
from reqpilot.llm.accounting import UsageLedger
from reqpilot.llm.gateway import LLMGateway
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.services.extraction import RunLog, RunRecorder

#: Architecture D3: a run processes a bounded batch.
MAX_SCOPE_ITEMS = 50


@dataclass(frozen=True)
class RunSummary:
    run_id: uuid.UUID
    status: GraphRunStatus
    requirement_version_ids: tuple[uuid.UUID, ...]
    classified_version_ids: tuple[uuid.UUID, ...]
    review_item_ids: tuple[uuid.UUID, ...]
    accepted: int
    merged: int
    rejected: int
    errors: tuple[str, ...]
    provider_calls: int
    tokens_in: int
    tokens_out: int
    cost_estimate: float | None


class AnalysisRunner:
    def __init__(
        self,
        session: Session,
        gateway: LLMGateway,
        rules: ExtractionRules,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._rules = rules
        self._settings = settings or get_settings()

    def extract(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        source_ids: Sequence[uuid.UUID],
        domain: str,
    ) -> RunSummary:
        """Extract requirements from ``source_ids`` and classify them."""
        if not source_ids:
            raise ReqPilotError("an extraction run needs at least one source")
        if len(source_ids) > MAX_SCOPE_ITEMS:
            raise ReqPilotError(f"a run takes at most {MAX_SCOPE_ITEMS} sources")
        domain = normalise_domain(domain)
        return self._run(
            actor,
            project_id,
            {
                "domain": domain,
                "scope_source_ids": [str(s) for s in dict.fromkeys(source_ids)],
                "scope_version_ids": [],
            },
            scope={"mode": "extract", "sources": len(set(source_ids)), "domain": domain},
        )

    def classify(
        self, *, actor: Actor, project_id: ProjectId, version_ids: Sequence[uuid.UUID]
    ) -> RunSummary:
        """Classify ``EXTRACTED`` versions - after a failure, or after a merge."""
        if not version_ids:
            raise ReqPilotError("a classification run needs at least one version")
        if len(version_ids) > MAX_SCOPE_ITEMS:
            raise ReqPilotError(f"a run takes at most {MAX_SCOPE_ITEMS} versions")
        return self._run(
            actor,
            project_id,
            {
                "domain": "",
                "scope_source_ids": [],
                "scope_version_ids": [str(v) for v in dict.fromkeys(version_ids)],
            },
            scope={"mode": "classify", "versions": len(set(version_ids))},
        )

    def _run(
        self,
        actor: Actor,
        project_id: ProjectId,
        initial: dict[str, Any],
        *,
        scope: dict[str, Any],
    ) -> RunSummary:
        run, pipeline = RunRecorder(self._session, actor).start(project_id, scope=scope)
        log = RunLog(self._session, pipeline, run)
        ledger = UsageLedger()
        context = RunContext(
            session=self._session,
            actor=pipeline,
            log=log,
            gateway=self._gateway.with_usage(ledger),
            rules=self._rules,
        )
        graph = build_analysis_graph(
            AnalysisNodes(context), checkpointer=build_checkpointer(self._settings)
        )
        state: AnalysisState = {
            "run_id": str(run.id),
            "project_id": str(project_id),
            "actor_id": str(actor.actor_id),
            "errors": [],
            **initial,  # type: ignore[typeddict-item]
        }
        final = graph.invoke(state, config=run_config(GraphRunId(run.id)))

        errors = tuple(e["message"] for e in final.get("errors", []))
        status = GraphRunStatus.FAILED if errors else GraphRunStatus.COMPLETED
        log.finish(
            status,
            accepted=final.get("accepted", 0),
            merged=final.get("merged", 0),
            rejected=final.get("rejected", 0),
            classified=len(final.get("classified_version_ids", [])),
            review_items=len(final.get("review_item_ids", [])),
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            cost_estimate=ledger.cost_estimate,
        )
        return RunSummary(
            run_id=run.id,
            status=status,
            requirement_version_ids=tuple(
                uuid.UUID(v) for v in final.get("requirement_version_ids", [])
            ),
            classified_version_ids=tuple(
                uuid.UUID(v) for v in final.get("classified_version_ids", [])
            ),
            review_item_ids=tuple(uuid.UUID(v) for v in final.get("review_item_ids", [])),
            accepted=final.get("accepted", 0),
            merged=final.get("merged", 0),
            rejected=final.get("rejected", 0),
            errors=errors,
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            cost_estimate=ledger.cost_estimate,
        )
