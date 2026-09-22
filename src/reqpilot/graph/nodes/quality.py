"""The P5 nodes of ``analysis_graph``: C.3 nodes 6-8.

=========================  ===================  =====================  ====================
Node                       Kind                 Role                   Writes
=========================  ===================  =====================  ====================
``quality_analysis``       rules + LLM          Validation (#12) /     quality findings
                                                Extraction (#3) supp.
``conflict_shortlist``     deterministic        Conflict (#6)          nothing (ids only)
``conflict_adjudicate``    rules + LLM          Conflict (#6)          conflicts
=========================  ===================  =====================  ====================

Each node is "the LLM proposes; deterministic code disposes": the rules record
what they can point at; the model's proposals are validated before the engine
records them, with the ruleset's severity; routing reads flags and counts only.
A semantic call that fails or is refused is recorded (an agent run, a review
item, a counter) and the rule results still stand; the run does not invent a
finding to fill the gap.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

from reqpilot.agents.contracts.quality import ConflictPairView, QualityReviewItem
from reqpilot.agents.roles.quality import ConflictDetectionRole, RequirementQualityRole
from reqpilot.agents.validation.quality import validate_conflict, validate_quality_findings
from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    ConflictClass,
    ConflictVerdict,
    FindingDetector,
    ReviewReason,
)
from reqpilot.domain.errors import (
    EgressRefusedError,
    EmbeddingUnavailableError,
    ReqPilotError,
)
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.quality import PairItem, judge_pair
from reqpilot.domain.quality.text import Span
from reqpilot.graph.state import AnalysisState
from reqpilot.services.audit import AuditService
from reqpilot.services.quality import ProposedConflict, QualityEngine, VersionView
from reqpilot.services.review import ReviewQueue

if TYPE_CHECKING:  # pragma: no cover
    from reqpilot.graph.nodes.analysis import AnalysisNodes


class QualityNodes:
    """C.3 nodes 6-8, sharing the analysis nodes' run context and recording helpers."""

    def __init__(self, base: AnalysisNodes) -> None:
        self.base = base
        self.ctx = base.ctx

    def _engine(self) -> QualityEngine:
        ctx = self.ctx
        if ctx.quality_rules is None:  # pragma: no cover - the runner always supplies them
            raise RuntimeError("a quality run needs the quality ruleset")
        return QualityEngine(ctx.session, ctx.actor, ctx.quality_rules)

    def _refused(self, node: str, role: AgentRole, started: Any, exc: Exception) -> None:
        """An egress refusal is a security event: recorded, audited, never retried."""
        ctx = self.ctx
        ctx.log.agent_run(
            node=node,
            role=role,
            status=AgentRunStatus.FAILED,
            started_at=started,
            error_code="egress_refused",
        )
        AuditService(ctx.session).append(
            event_type=AuditEventType.PERMISSION_DENIED,
            actor_kind=ctx.actor.kind,
            actor_ref=str(ctx.actor.actor_id),
            project_id=ctx.project_id,
            subject_type="graph_run",
            subject_id=str(ctx.log.run.id),
            graph_run_id=ctx.log.run.id,
            payload={"node": node, "error_code": "egress_refused", "detail": str(exc)[:200]},
        )

    def _malformed(self, node: str, agent_run: Any, error_code: str | None) -> None:
        ReviewQueue(self.ctx.session, self.ctx.actor).raise_item(
            project_id=self.ctx.project_id,
            reason=ReviewReason.MALFORMED_OUTPUT,
            subject_type="agent_run",
            subject_id=agent_run.id,
            graph_run_id=self.ctx.log.run.id,
            agent_run_id=agent_run.id,
            detail={"error_code": error_code, "node": node},
        )

    # ------------------------------------------------------------------
    # 6. quality_analysis (rules + LLM)
    # ------------------------------------------------------------------
    def quality_analysis(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "quality_analysis", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        rules = engine.rules
        try:
            pool = engine.current_versions(ctx.project_id)
            raw_scope = (
                state.get("quality_version_ids")
                if state.get("quality_mode")
                else state.get("requirement_version_ids")
            ) or []
            scope = (
                engine.views(ctx.project_id, [uuid.UUID(v) for v in raw_scope])
                if raw_scope
                else pool
            )
        except (ReqPilotError, ValueError) as exc:
            error = exc if isinstance(exc, ReqPilotError) else ReqPilotError(str(exc))
            return self.base._fail(node, AgentRole.VALIDATION, started, "invalid_scope", error)
        pool_by_key = {v.key: v for v in pool}
        for view in scope:
            pool_by_key.setdefault(view.key, view)
        ctx.quality_pool = pool_by_key
        ctx.quality_scope = [v.key for v in scope]

        # --- the rule half (#12 Validation: deterministic) -------------------
        glossary = engine.glossary_keys(ctx.project_id)
        recorded: list[str] = []
        by_type: Counter[str] = Counter()
        for view in scope:
            for detected in engine.rule_findings(ctx.project_id, view, glossary):
                finding = engine.record_detected(
                    ctx.project_id, view, detected, graph_run_id=ctx.log.run.id
                )
                if finding is not None:
                    recorded.append(str(finding.id))
                    by_type[str(finding.finding_type)] += 1
        scope_keys = frozenset(ctx.quality_scope)
        for view, detected in engine.duplicate_findings(list(pool_by_key.values()), scope_keys):
            related = pool_by_key.get(detected.related_key or "")
            finding = engine.record_detected(
                ctx.project_id, view, detected, graph_run_id=ctx.log.run.id, related=related
            )
            if finding is not None:
                recorded.append(str(finding.id))
                by_type[str(finding.finding_type)] += 1
        self.base._deterministic_run(
            node,
            AgentRole.VALIDATION,
            started,
            {"versions": len(scope), "rule_findings": len(recorded), "ruleset": rules.ruleset_ref},
            output_refs={"by_type": dict(by_type)},
        )

        # --- the semantic half (LLM, role #3 support) -------------------------
        failures = 0
        semantic_found = 0
        if state.get("semantic") and rules.semantic_quality_enabled and scope:
            groups: dict[tuple[bool, bool], list[VersionView]] = {}
            for view in scope:
                groups.setdefault((view.masked, view.synthetic), []).append(view)
            role = RequirementQualityRole(ctx.gateway)
            allowed = sorted(str(t) for t in rules.proposable_types)
            for (masked, synthetic), views in sorted(groups.items()):
                size = rules.semantic_batch_size
                for start in range(0, len(views), size):
                    batch = views[start : start + size]
                    keyed = {f"R{i}": v for i, v in enumerate(batch, start=1)}
                    call_started = utc_now()
                    try:
                        result = role.propose(
                            [QualityReviewItem(k, v.version.statement) for k, v in keyed.items()],
                            allowed_types=allowed,
                            masked=masked,
                            synthetic=synthetic,
                        )
                    except EgressRefusedError as exc:
                        self._refused(node, role.role, call_started, exc)
                        failures += 1
                        continue
                    input_refs = {"requirement_version_ids": {k: v.key for k, v in keyed.items()}}
                    if not result.ok or result.value is None:
                        agent_run = self.base._llm_run(
                            node, role.role, call_started, result, input_refs
                        )
                        self._malformed(node, agent_run, str(result.error_code))
                        failures += 1
                        continue
                    decision = validate_quality_findings(
                        result.value.findings,
                        statements={k: v.version.statement for k, v in keyed.items()},
                        rules=rules,
                    )
                    agent_run = self.base._llm_run(
                        node,
                        role.role,
                        call_started,
                        result,
                        input_refs,
                        output_refs={
                            "proposed": len(result.value.findings),
                            "accepted": len(decision.accepted),
                            "rejected": [reason for _, reason in decision.rejected][:20],
                        },
                        status_override=AgentRunStatus.PARTIAL if decision.rejected else None,
                    )
                    for accepted in decision.accepted:
                        view = keyed[accepted.key]
                        span = (
                            Span(accepted.start, accepted.end, accepted.evidence)
                            if accepted.start is not None and accepted.end is not None
                            else None
                        )
                        rationale = accepted.explanation
                        if accepted.missing:
                            rationale += f" Missing: {accepted.missing}"
                        finding = engine.record_finding(
                            ctx.project_id,
                            view,
                            finding_type=accepted.finding_type,
                            rationale=rationale,
                            span=span,
                            review_signal=accepted.review_signal,
                            detected_by=FindingDetector.AGENT,
                            rule_id=result.meta.prompt_ref,
                            graph_run_id=ctx.log.run.id,
                            agent_run_id=agent_run.id,
                            extra_evidence={"proposed_severity": accepted.proposed_severity},
                        )
                        if finding is not None:
                            recorded.append(str(finding.id))
                            semantic_found += 1
        ctx.log.node_completed(
            node,
            versions=len(scope),
            findings=len(recorded),
            semantic_findings=semantic_found,
            semantic_failures=failures,
        )
        return {
            "current_node": node,
            "quality_finding_ids": recorded,
            "semantic_failures": failures,
        }

    # ------------------------------------------------------------------
    # 7. conflict_shortlist (deterministic)
    # ------------------------------------------------------------------
    def conflict_shortlist(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "conflict_shortlist", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        pool = list(ctx.quality_pool.values())
        focus = frozenset(ctx.quality_scope) if state.get("quality_focus") else None
        embedder_note = ctx.embedder.model_id if ctx.embedder else "none"
        try:
            candidates = engine.candidates(pool, embedder=ctx.embedder, focus=focus)
        except EmbeddingUnavailableError:
            # Recorded, not silent: the shortlist ran on lexical and structural
            # signals only, and the agent run says so.
            embedder_note = "unavailable"
            candidates = engine.candidates(pool, embedder=None, focus=focus)
        pairs: list[dict] = []
        for candidate in candidates:
            a, b = ctx.quality_pool[candidate.a], ctx.quality_pool[candidate.b]
            if engine.pair_recorded(ctx.project_id, a, b):
                continue
            pairs.append({"a": candidate.a, "b": candidate.b, "score": candidate.score})
        engine.shortlisted(
            ctx.project_id, graph_run_id=ctx.log.run.id, pairs=len(pairs), compared=len(pool)
        )
        self.base._deterministic_run(
            node,
            AgentRole.CONFLICT_DETECTION,
            started,
            {"versions": len(pool), "pairs": len(pairs)},
            output_refs={"embedder": embedder_note},
        )
        ctx.log.node_completed(node, versions=len(pool), pairs=len(pairs))
        return {"current_node": node, "conflict_pairs": pairs}

    # ------------------------------------------------------------------
    # 8. conflict_adjudicate (rules + LLM, role #6)
    # ------------------------------------------------------------------
    def conflict_adjudicate(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "conflict_adjudicate", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        rules = engine.rules
        semantic = bool(state.get("semantic")) and rules.semantic_adjudication
        role = ConflictDetectionRole(ctx.gateway)
        recorded: list[str] = []
        outcomes: Counter[str] = Counter()
        failures = 0
        for pair in state.get("conflict_pairs", []):
            a, b = ctx.quality_pool[pair["a"]], ctx.quality_pool[pair["b"]]
            verdict = judge_pair(
                PairItem(a.key, a.version.statement, a.stakeholder),
                PairItem(b.key, b.version.statement, b.stakeholder),
                rules,
            )
            if verdict.verdict is ConflictVerdict.DUPLICATE:
                outcomes["duplicate"] += 1
                continue
            if verdict.definite:
                conflict = engine.record_conflict(
                    ctx.project_id,
                    ProposedConflict(
                        a,
                        b,
                        ConflictClass.DEFINITE,
                        verdict.kind,
                        verdict.rationale,
                        verdict.evidence_a,
                        verdict.evidence_b,
                        rules.conflict_signal_definite,
                        FindingDetector.RULE,
                        f"{rules.ruleset_ref}:{verdict.rule_id}",
                    ),
                    graph_run_id=ctx.log.run.id,
                )
                outcomes["definite_rule"] += 1
                if conflict is not None:
                    recorded.append(str(conflict.id))
                continue
            if not semantic:
                if verdict.signal:
                    conflict = engine.record_conflict(
                        ctx.project_id,
                        ProposedConflict(
                            a,
                            b,
                            ConflictClass.POTENTIAL,
                            verdict.kind,
                            f"{verdict.rationale}; no adjudicator ran, so a human must decide",
                            verdict.evidence_a,
                            verdict.evidence_b,
                            rules.conflict_signal_potential,
                            FindingDetector.RULE,
                            f"{rules.ruleset_ref}:{verdict.rule_id}",
                        ),
                        graph_run_id=ctx.log.run.id,
                    )
                    outcomes["potential_rule"] += 1
                    if conflict is not None:
                        recorded.append(str(conflict.id))
                else:
                    outcomes["no_signal"] += 1
                continue
            view = ConflictPairView(
                version_a_id=a.key,
                version_b_id=b.key,
                statement_a=a.version.statement,
                statement_b=b.version.statement,
                stakeholder_a=a.stakeholder,
                stakeholder_b=b.stakeholder,
                masked=a.masked and b.masked,
                synthetic=a.synthetic and b.synthetic,
            )
            call_started = utc_now()
            try:
                result = role.propose(view)
            except EgressRefusedError as exc:
                self._refused(node, role.role, call_started, exc)
                failures += 1
                continue
            input_refs = {"version_a_id": a.key, "version_b_id": b.key, "score": pair["score"]}
            if not result.ok or result.value is None:
                agent_run = self.base._llm_run(node, role.role, call_started, result, input_refs)
                self._malformed(node, agent_run, str(result.error_code))
                failures += 1
                continue
            decision = validate_conflict(result.value, pair=view, rules=rules)
            agent_run = self.base._llm_run(
                node,
                role.role,
                call_started,
                result,
                input_refs,
                output_refs={
                    "verdict": str(decision.verdict),
                    "accepted": decision.accepted,
                    "findings": list(decision.findings),
                },
                status_override=None if decision.accepted else AgentRunStatus.PARTIAL,
            )
            if not decision.accepted:
                outcomes["rejected"] += 1
                self._malformed(node, agent_run, "invalid_adjudication")
                continue
            outcomes[str(decision.verdict)] += 1
            if decision.conflict_class is None:
                continue
            conflict = engine.record_conflict(
                ctx.project_id,
                ProposedConflict(
                    a,
                    b,
                    decision.conflict_class,
                    decision.kind,
                    decision.explanation,
                    decision.evidence_a,
                    decision.evidence_b,
                    decision.review_signal,
                    FindingDetector.AGENT,
                    result.meta.prompt_ref,
                ),
                graph_run_id=ctx.log.run.id,
                agent_run_id=agent_run.id,
            )
            if conflict is not None:
                recorded.append(str(conflict.id))
        self.base._deterministic_run(
            node,
            AgentRole.CONFLICT_DETECTION,
            started,
            {"pairs": len(state.get("conflict_pairs", [])), "conflicts": len(recorded)},
            output_refs={"outcomes": dict(outcomes)},
        )
        ctx.log.node_completed(
            node, conflicts=len(recorded), semantic_failures=failures, **dict(outcomes)
        )
        return {
            "current_node": node,
            "conflict_pairs": [],  # the working set is consumed here (D.4)
            "conflict_ids": recorded,
            "has_open_conflicts": bool(recorded),
            "semantic_failures": failures,
        }
