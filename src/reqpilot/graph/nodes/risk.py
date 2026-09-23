"""The P7 nodes of ``analysis_graph``: C.3 nodes 18-19 and the G8 part of 20.

==========================  ===================  ==================  ==================
Node                        Kind                 Role                Writes
==========================  ===================  ==================  ==================
``risk_identify``           LLM (grounded)       Risk Analysis (#9)  nothing (proposals)
``risk_compute_severity``   **deterministic**    Risk Analysis (#9)  risk rows + severity
==========================  ===================  ==================  ==================

"The LLM proposes; deterministic code disposes." A model sees one requirement,
the findings earlier phases recorded for it, and the evidence this project's
allowlisted retrieval produced. Its typed proposal waits in the transient run
context - never in a checkpoint - until ``risk_compute_severity`` judges it.

That node is where the phase's authority lives. It validates each proposal
(including the ``FR-RSK-011`` scope guard), looks the severity up in the
versioned 3x3 matrix, records the risk with it, and sets the routing flag from
the **persisted** column. A model cannot reach the severity: there is no field
for one in the contract, no field in the accepted value object, and no parameter
on the engine method that writes the row.

If the semantic layer is off, fails or is refused, the node still runs and
records nothing - no invented ratings, no default severity. Risk is a rated
judgement, and a fabricated one would be worse than its absence; the run's
counters show that no risks were identified. The G2/G3 escalations P6 owns are
unaffected, because they come from their own persisted values.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

from reqpilot.agents.contracts.risk import RiskAnalysisOutput
from reqpilot.agents.roles.risk import SetSummaryView, SignalView
from reqpilot.agents.validation.risk import validate_risk_proposals
from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    RiskScope,
    RiskSeverity,
)
from reqpilot.domain.errors import EgressRefusedError, ReqPilotError
from reqpilot.domain.models.base import utc_now
from reqpilot.graph.nodes.compliance import PendingCall
from reqpilot.graph.state import AnalysisState
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance.engine import AnalysisView
from reqpilot.services.risk import RiskEngine
from reqpilot.services.risk.gates import RISK_SUBJECT

if TYPE_CHECKING:  # pragma: no cover
    from reqpilot.graph.nodes.analysis import AnalysisNodes

#: The key the project-level pass uses in the run context's result map.
PROJECT_KEY = "__project__"


class RiskNodes:
    """C.3 nodes 18-19, sharing the analysis nodes' run context and helpers."""

    def __init__(self, base: AnalysisNodes) -> None:
        self.base = base
        self.ctx = base.ctx

    def _engine(self) -> RiskEngine:
        ctx = self.ctx
        if ctx.risk_rules is None:  # pragma: no cover - the runner supplies them
            raise RuntimeError("a risk run needs the risk ruleset")
        return RiskEngine(ctx.session, ctx.actor, ctx.risk_rules, self.base.compliance._engine())

    def _signal_views(self, signals: Any) -> list[SignalView]:
        return [SignalView(kind=s.kind, key=s.key, detail=s.detail) for s in signals]

    # ------------------------------------------------------------------
    # 18. risk_identify (LLM, role #9)
    # ------------------------------------------------------------------
    def risk_identify(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "risk_identify", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        try:
            raw = state.get("risk_version_ids") or state.get("compliance_version_ids") or []
            views = engine.views(ctx.project_id, [uuid.UUID(v) for v in raw])
        except (ReqPilotError, ValueError) as exc:
            error = exc if isinstance(exc, ReqPilotError) else ReqPilotError(str(exc))
            return self.base._fail(node, AgentRole.RISK_ANALYSIS, started, "invalid_scope", error)
        ctx.risk_pool = {v.key: v for v in views}

        AuditService(ctx.session).append(
            event_type=AuditEventType.RISK_ANALYSIS_STARTED,
            actor_kind=ctx.actor.kind,
            actor_ref=str(ctx.actor.actor_id),
            project_id=ctx.project_id,
            subject_type="graph_run",
            subject_id=str(ctx.log.run.id),
            graph_run_id=ctx.log.run.id,
            payload={
                "versions": len(views),
                "rules_version": engine.rules.ruleset_ref,
                "matrix_version": engine.rules.matrix.version,
                "semantic": bool(state.get("semantic")),
            },
        )

        # The deterministic half runs whatever happens next: which categories
        # each version is examined for, and which persisted P5/P6 findings
        # inform it. Both are recorded before any model is called.
        for key, view in ctx.risk_pool.items():
            ctx.risk_indicated[key] = engine.indicated_categories(view)
            ctx.risk_signals[key] = engine.signals_for(ctx.project_id, view)

        if not state.get("semantic"):
            ctx.log.node_completed(node, calls=0, versions=len(views), semantic=False)
            return {"current_node": node, "semantic_failures": 0}

        from reqpilot.agents.roles.risk import RiskAnalysisRole

        role = RiskAnalysisRole(ctx.gateway)
        categories = [c.value for c in engine.rules.owner_roles]
        calls = 0
        failures = 0

        for key, view in ctx.risk_pool.items():
            supplied = self._evidence_for(view)
            if not supplied:
                # FR-RSK-006: every risk needs evidence, so a requirement with
                # none is not sent to a model at all - a proposal made without
                # evidence could only be dropped, and asking for one invites a
                # fabricated citation.
                continue
            call_started = utc_now()
            try:
                result = role.propose(
                    self.base.compliance._requirement(view),
                    categories=categories,
                    indicated=[str(c) for c in ctx.risk_indicated.get(key, ())],
                    signals=self._signal_views(ctx.risk_signals.get(key, ())),
                    evidence=self.base.compliance._evidence_views(supplied),
                )
            except EgressRefusedError as exc:
                self.base.compliance._refused(node, role.role, call_started, exc)
                failures += 1
                continue
            calls += 1
            agent_run = self._log_call(node, role.role, call_started, result, key, supplied)
            if not result.ok:
                self.base.compliance._malformed(node, agent_run, str(result.error_code))
                failures += 1
                continue
            ctx.risk_results[key] = PendingCall(result, agent_run.id, supplied)

        # FR-RSK-001's second half: risks arising from the requirement set as a
        # whole. One call, with a deterministic summary of the set.
        project_call = self._identify_project_risks(node, engine, role, categories)
        calls += project_call[0]
        failures += project_call[1]

        ctx.log.node_completed(node, calls=calls, semantic_failures=failures)
        return {"current_node": node, "semantic_failures": failures}

    def _identify_project_risks(
        self, node: str, engine: RiskEngine, role: Any, categories: list[str]
    ) -> tuple[int, int]:
        """The project-level pass. Returns ``(calls, failures)``."""
        ctx = self.ctx
        views = list(ctx.risk_pool.values())
        if not views:
            return 0, 0
        supplied = tuple(dict.fromkeys(e for view in views for e in self._evidence_for(view)))[:8]
        if not supplied:
            return 0, 0
        signals = list(engine.project_signals(ctx.project_id))
        for key in ctx.risk_pool:
            signals.extend(ctx.risk_signals.get(key, ()))
        summary = SetSummaryView(
            requirement_count=len(views),
            category_counts=dict(Counter(c for v in views for c in v.categories)),
            lines=[f"{v.human_id}: {' '.join(v.version.statement.split())[:200]}" for v in views],
        )
        call_started = utc_now()
        try:
            result = role.propose_project_risks(
                summary,
                categories=categories,
                signals=self._signal_views(signals[:40]),
                evidence=self.base.compliance._evidence_views(supplied),
                masked=all(v.masked for v in views),
                synthetic=all(v.synthetic for v in views),
            )
        except EgressRefusedError as exc:
            self.base.compliance._refused(node, role.role, call_started, exc)
            return 0, 1
        agent_run = self._log_call(node, role.role, call_started, result, PROJECT_KEY, supplied)
        if not result.ok:
            self.base.compliance._malformed(node, agent_run, str(result.error_code))
            return 1, 1
        ctx.risk_results[PROJECT_KEY] = PendingCall(result, agent_run.id, supplied)
        return 1, 0

    def _evidence_for(self, view: AnalysisView) -> tuple[uuid.UUID, ...]:
        """The evidence recorded for this version in this run (P2/P6 path, reused)."""
        found = self.ctx.version_evidence.get(view.key)
        return tuple(found.evidence_ids) if found is not None else ()

    def _log_call(
        self,
        node: str,
        role: AgentRole,
        started: Any,
        result: Any,
        key: str,
        supplied: tuple[uuid.UUID, ...],
    ) -> Any:
        ctx = self.ctx
        spec = ctx.gateway.prompts.get(result.meta.prompt_name)
        return ctx.log.agent_run(
            node=node,
            role=role,
            status=AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED,
            started_at=started,
            meta=result.meta,
            prompt_role=spec.role,
            prompt_text=spec.text,
            input_refs={"requirement_version_id": key},
            output_refs={"output_sha256": result.output_sha256},
            error_code=str(result.error_code) if result.error_code else None,
            evidence_ids=[str(e) for e in supplied],
        )

    # ------------------------------------------------------------------
    # 19. risk_compute_severity (DETERMINISTIC - the I.3 authority)
    # ------------------------------------------------------------------
    def risk_compute_severity(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "risk_compute_severity", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        compliance = self.base.compliance._engine()
        allowed = compliance.run_evidence_ids(ctx.project_id, ctx.log.run.id)
        recorded: list[str] = []
        dropped_total = 0
        out_of_scope_total = 0
        reasons: Counter[str] = Counter()
        by_severity: Counter[str] = Counter()
        high = False

        for key, pending in ctx.risk_results.items():
            project_level = key == PROJECT_KEY
            view = None if project_level else ctx.risk_pool[key]
            output = pending.result.value
            assert isinstance(output, RiskAnalysisOutput)

            AuditService(ctx.session).append(
                event_type=AuditEventType.RISK_PROPOSED,
                actor_kind=ctx.actor.kind,
                actor_ref=str(ctx.actor.actor_id),
                project_id=ctx.project_id,
                subject_type="requirement_version" if view else "project",
                subject_id=view.key if view else str(ctx.project_id),
                subject_version=str(view.version.version_no) if view else None,
                graph_run_id=ctx.log.run.id,
                agent_run_id=pending.agent_run_id,
                payload={
                    "proposed": len(output.risks),
                    "categories": sorted({r.category[:40] for r in output.risks}),
                    "prompt_ref": pending.result.meta.prompt_ref,
                    "model": pending.result.meta.model_id,
                },
            )

            cited = {str(e).strip().lower() for r in output.risks for e in r.evidence_ids}
            citations = compliance.citation_facts(
                ctx.project_id,
                [e for e in pending.supplied if str(e) in cited],
                allowed=allowed,
            )
            decision = validate_risk_proposals(
                output,
                version_id=None if project_level else key,
                scope=RiskScope.PROJECT if project_level else RiskScope.REQUIREMENT,
                supplied=frozenset(str(e) for e in pending.supplied),
                citations=citations,
                max_risks=(
                    engine.rules.max_project_risks_per_run
                    if project_level
                    else engine.rules.max_risks_per_requirement
                ),
                max_mitigations=engine.rules.max_mitigations_per_risk,
            )
            for accepted in decision.accepted:
                risk = engine.record_risk(
                    ctx.project_id,
                    accepted,
                    view=view,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=pending.agent_run_id,
                )
                if risk is None:
                    continue
                recorded.append(str(risk.id))
                by_severity[str(risk.severity)] += 1
                # The routing flag is read back from the persisted, authoritative
                # column - never from the proposal it came from.
                high = high or risk.severity is RiskSeverity.HIGH
            for drop in decision.dropped:
                engine.record_drop(
                    ctx.project_id,
                    drop,
                    view=view,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=pending.agent_run_id,
                )
                reasons[drop.reason] += 1
            engine.raise_drop_review(
                ctx.project_id,
                decision.dropped,
                view=view,
                graph_run_id=ctx.log.run.id,
                agent_run_id=pending.agent_run_id,
            )
            dropped_total += len(decision.dropped)
            out_of_scope_total += len(decision.out_of_scope)

        ctx.risk_results.clear()  # the working set is consumed here (D.4)
        self.base._deterministic_run(
            node,
            AgentRole.RISK_ANALYSIS,
            started,
            {"risks": len(recorded), "dropped": dropped_total},
            output_refs={
                "drop_reasons": dict(reasons),
                "by_severity": dict(by_severity),
                "scope_guard_refusals": out_of_scope_total,
                "matrix_version": engine.rules.matrix.version,
                "rules_version": engine.rules.ruleset_ref,
            },
        )
        ctx.log.node_completed(
            node,
            risks=len(recorded),
            dropped=dropped_total,
            out_of_scope=out_of_scope_total,
            high=by_severity.get(str(RiskSeverity.HIGH), 0),
        )
        return {
            "current_node": node,
            "risk_ids": recorded,
            "claims_dropped": dropped_total,
            "risks_out_of_scope": out_of_scope_total,
            "has_high_severity_risk": high,
        }

    # ------------------------------------------------------------------
    # 20 (G8 half). Called by ``gate_fanout`` after the P6 gates.
    # ------------------------------------------------------------------
    def raise_risk_gates(self) -> list[Any]:
        """Raise G8 from persisted HIGH severities. Reads columns, not proposals."""
        return self._engine().raise_pending_gates(
            self.ctx.project_id, graph_run_id=self.ctx.log.run.id
        )


__all__ = ["PROJECT_KEY", "RISK_SUBJECT", "RiskNodes"]
