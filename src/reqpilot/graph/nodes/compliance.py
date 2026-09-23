"""The P6 nodes of ``analysis_graph``: C.3 nodes 12-17 and 20.

==========================  ===================  ==========================  ======================
Node                        Kind                 Role                        Writes
==========================  ===================  ==========================  ======================
``compliance_retrieve``     deterministic (RAG)  Compliance (#7)             evidence rows
``compliance_map``          LLM (grounded)       Compliance (#7)             nothing (proposals)
``compliance_validate``     deterministic        Compliance (#7) / #12       mappings, drops
``compliance_gaps``         rules                Compliance (#7)             gaps
``security_privacy_derive`` LLM + catalogue      Security & Privacy (#8)     nothing (proposals)
``security_privacy_eval``   **deterministic**    Security & Privacy (#8)     findings + risk_level
``gate_fanout``             deterministic        Coordinator (#1)            G2 / G3 tasks
==========================  ===================  ==========================  ======================

"The LLM proposes; deterministic code disposes." A model sees one requirement
and the evidence the node retrieved for it through the project's allowlist; its
typed proposal waits in the transient run context (never in a checkpoint) until
the validating node judges it. Gaps, the authoritative risk level and whether a
gate fires are computed by code from persisted values. Routing reads flags and
counts only. A semantic call that fails or is refused is recorded and the
deterministic parts still run: gaps are still computed, and every indicated
security/privacy family still yields a catalogue finding - so removing the model
never removes a gate.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reqpilot.agents.contracts.compliance import ComplianceMappingOutput, SecurityPrivacyOutput
from reqpilot.agents.roles.compliance import (
    ComplianceRole,
    ControlView,
    EvidenceView,
    RequirementView,
    SecurityPrivacyRole,
)
from reqpilot.agents.validation.compliance import (
    baseline_proposal,
    validate_compliance_mappings,
    validate_security_proposals,
)
from reqpilot.domain.compliance.claims import AcceptedSecurityProposal
from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    ReviewReason,
    SecurityPrivacyCategory,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import EgressRefusedError, ReqPilotError
from reqpilot.domain.models.base import utc_now
from reqpilot.graph.state import AnalysisState
from reqpilot.llm.types import StructuredResult
from reqpilot.retrieval.contracts import RetrievalOutcome
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance import AnalysisView, ComplianceEngine
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.review import ReviewQueue

if TYPE_CHECKING:  # pragma: no cover
    from reqpilot.graph.nodes.analysis import AnalysisNodes


@dataclass(frozen=True)
class PendingCall:
    """A model's proposal awaiting validation (transient tier only)."""

    result: StructuredResult[Any]
    agent_run_id: uuid.UUID
    supplied: tuple[uuid.UUID, ...]


class ComplianceNodes:
    """C.3 nodes 12-17 and 20, sharing the analysis nodes' run context and helpers."""

    def __init__(self, base: AnalysisNodes) -> None:
        self.base = base
        self.ctx = base.ctx

    def _engine(self) -> ComplianceEngine:
        ctx = self.ctx
        if ctx.compliance_rules is None or ctx.security_rules is None:  # pragma: no cover
            raise RuntimeError("a compliance run needs the compliance and security rulesets")
        return ComplianceEngine(ctx.session, ctx.actor, ctx.compliance_rules, ctx.security_rules)

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

    def _requirement(self, view: AnalysisView) -> RequirementView:
        return RequirementView(
            version_id=view.key,
            statement=view.version.statement,
            masked=view.masked,
            synthetic=view.synthetic,
        )

    def _evidence_views(self, supplied: tuple[uuid.UUID, ...]) -> list[EvidenceView]:
        """The supplied evidence as the model sees it - re-read and re-verified by id."""
        service = EvidenceService(self.ctx.session, self.ctx.actor)
        views = []
        for evidence_id in supplied:
            citation = service.describe(self.ctx.project_id, evidence_id)
            views.append(
                EvidenceView(
                    evidence_id=str(citation.evidence_id),
                    source_title=citation.source_title,
                    source_type=str(citation.source_type),
                    binding=SOURCE_TYPE_BINDING[citation.source_type],
                    issuing_body=citation.issuing_body,
                    jurisdiction=citation.jurisdiction,
                    source_version=citation.source_version,
                    effective_date=(
                        citation.effective_date.isoformat() if citation.effective_date else None
                    ),
                    clause_ref=citation.clause_ref,
                    quote=citation.quote,
                )
            )
        return views

    # ------------------------------------------------------------------
    # 12. compliance_retrieve (deterministic, RAG)
    # ------------------------------------------------------------------
    def compliance_retrieve(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "compliance_retrieve", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        try:
            raw = state.get("compliance_version_ids") or []
            views = (
                engine.views(ctx.project_id, [uuid.UUID(v) for v in raw])
                if raw
                else engine.current_views(ctx.project_id)
            )
            if ctx.retriever is None:
                raise ReqPilotError("a compliance run needs the allowlisted retrieval service")
        except (ReqPilotError, ValueError) as exc:
            error = exc if isinstance(exc, ReqPilotError) else ReqPilotError(str(exc))
            return self.base._fail(node, AgentRole.COMPLIANCE, started, "invalid_scope", error)
        ctx.compliance_pool = {v.key: v for v in views}
        evidence_ids: list[str] = []
        unavailable: list[str] = []
        try:
            for view in views:
                found = engine.retrieve_evidence(
                    ctx.project_id, view, ctx.retriever, graph_run_id=ctx.log.run.id
                )
                ctx.version_evidence[view.key] = found
                if found.outcome is RetrievalOutcome.EMPTY:
                    unavailable.append(view.key)
                evidence_ids.extend(str(e) for e in found.evidence_ids)
        except ReqPilotError as exc:
            return self.base._fail(node, AgentRole.COMPLIANCE, started, "retrieval_failed", exc)
        except RuntimeError as exc:
            # e.g. hybrid retrieval without PostgreSQL + pgvector (ADR-003/004): an
            # infrastructure refusal, recorded; nothing is answered without evidence.
            return self.base._fail(
                node, AgentRole.COMPLIANCE, started, "retrieval_failed", ReqPilotError(str(exc))
            )
        ctx.log.agent_run(
            node=node,
            role=AgentRole.COMPLIANCE,
            status=AgentRunStatus.OK,
            started_at=started,
            input_refs={"requirement_version_ids": [v.key for v in views]},
            output_refs={
                "versions": len(views),
                "evidence": len(evidence_ids),
                "evidence_unavailable": len(unavailable),
                "checklist": engine.rules.ruleset_ref,
            },
            evidence_ids=evidence_ids,
        )
        ctx.log.node_completed(
            node, versions=len(views), evidence=len(evidence_ids), unavailable=len(unavailable)
        )
        return {
            "current_node": node,
            "evidence_ids": evidence_ids,
            "evidence_unavailable_ids": unavailable,
        }

    # ------------------------------------------------------------------
    # 13. compliance_map (LLM, role #7)
    # ------------------------------------------------------------------
    def compliance_map(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "compliance_map", self.ctx
        ctx.log.node_started(node)
        engine = self._engine()
        failures = 0
        calls = 0
        if state.get("semantic"):
            role = ComplianceRole(ctx.gateway)
            project = engine.project(ctx.project_id)
            jurisdictions = [str(j).upper() for j in project.jurisdiction_scope or ()]
            for key, view in ctx.compliance_pool.items():
                found = ctx.version_evidence.get(key)
                if found is None or found.outcome is not RetrievalOutcome.SUCCESS:
                    continue  # FR-RAG-005: nothing to ground on -> no model call
                indicated = {c.key for c in engine.indicated_controls(ctx.project_id, view)}
                controls = [
                    ControlView(c.key, c.title, c.obligation_kind.value, c.key in indicated)
                    for c in engine.controls(ctx.project_id)
                ]
                if not controls:
                    continue
                supplied = tuple(found.evidence_ids)
                call_started = utc_now()
                try:
                    result = role.propose(
                        self._requirement(view),
                        controls=controls,
                        evidence=self._evidence_views(supplied),
                        jurisdictions=jurisdictions,
                    )
                except EgressRefusedError as exc:
                    self._refused(node, role.role, call_started, exc)
                    failures += 1
                    continue
                calls += 1
                agent_run = ctx.log.agent_run(
                    node=node,
                    role=role.role,
                    status=AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED,
                    started_at=call_started,
                    meta=result.meta,
                    prompt_role=ctx.gateway.prompts.get(result.meta.prompt_name).role,
                    prompt_text=ctx.gateway.prompts.get(result.meta.prompt_name).text,
                    input_refs={"requirement_version_id": key},
                    output_refs={"output_sha256": result.output_sha256},
                    error_code=str(result.error_code) if result.error_code else None,
                    evidence_ids=[str(e) for e in supplied],
                )
                if not result.ok:
                    self._malformed(node, agent_run, str(result.error_code))
                    failures += 1
                    continue
                ctx.compliance_results[key] = PendingCall(result, agent_run.id, supplied)
        ctx.log.node_completed(node, calls=calls, semantic_failures=failures)
        return {"current_node": node, "semantic_failures": failures}

    # ------------------------------------------------------------------
    # 14. compliance_validate (deterministic)
    # ------------------------------------------------------------------
    def compliance_validate(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "compliance_validate", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        project = engine.project(ctx.project_id)
        jurisdictions = frozenset(str(j).upper() for j in project.jurisdiction_scope or ())
        controls = {c.key: c for c in engine.controls(ctx.project_id)}
        allowed = engine.run_evidence_ids(ctx.project_id, ctx.log.run.id)
        recorded: list[str] = []
        dropped_total = 0
        reasons: Counter[str] = Counter()
        high_impact = False
        for key, pending in ctx.compliance_results.items():
            view = ctx.compliance_pool[key]
            output = pending.result.value
            assert isinstance(output, ComplianceMappingOutput)
            AuditService(ctx.session).append(
                event_type=AuditEventType.COMPLIANCE_PROPOSED,
                actor_kind=ctx.actor.kind,
                actor_ref=str(ctx.actor.actor_id),
                project_id=ctx.project_id,
                subject_type="requirement_version",
                subject_id=key,
                subject_version=str(view.version.version_no),
                graph_run_id=ctx.log.run.id,
                agent_run_id=pending.agent_run_id,
                payload={
                    "proposed": len(output.mappings),
                    "controls": [m.control_key[:64] for m in output.mappings][:20],
                    "prompt_ref": pending.result.meta.prompt_ref,
                    "model": pending.result.meta.model_id,
                },
            )
            cited = {str(e).strip().lower() for m in output.mappings for e in m.evidence_ids}
            citations = engine.citation_facts(
                ctx.project_id,
                [e for e in pending.supplied if str(e) in cited],
                allowed=allowed,
            )
            decision = validate_compliance_mappings(
                output,
                version_id=key,
                controls=controls,
                supplied=frozenset(str(e) for e in pending.supplied),
                citations=citations,
                project_jurisdictions=jurisdictions,
                high_impact_source_types=engine.rules.high_impact_source_types,
                max_mappings=engine.rules.max_mappings_per_requirement,
            )
            for accepted in decision.accepted:
                mapping = engine.record_mapping(
                    ctx.project_id,
                    view,
                    accepted,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=pending.agent_run_id,
                )
                if mapping is not None:
                    recorded.append(str(mapping.id))
                    high_impact = high_impact or mapping.is_high_impact
            for dropped in decision.dropped:
                engine.record_drop(
                    ctx.project_id,
                    view,
                    dropped,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=pending.agent_run_id,
                )
                reasons[dropped.reason] += 1
            engine.raise_drop_review(
                ctx.project_id,
                view,
                decision.dropped,
                graph_run_id=ctx.log.run.id,
                agent_run_id=pending.agent_run_id,
            )
            dropped_total += len(decision.dropped)
        ctx.compliance_results.clear()  # the working set is consumed here (D.4)
        self.base._deterministic_run(
            node,
            AgentRole.COMPLIANCE,
            started,
            {"mappings": len(recorded), "dropped": dropped_total},
            output_refs={"drop_reasons": dict(reasons)},
        )
        ctx.log.node_completed(node, mappings=len(recorded), dropped=dropped_total)
        return {
            "current_node": node,
            "compliance_mapping_ids": recorded,
            "claims_dropped": dropped_total,
            "has_high_impact_interpretation": high_impact,
        }

    # ------------------------------------------------------------------
    # 15. compliance_gaps (rules)
    # ------------------------------------------------------------------
    def compliance_gaps(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "compliance_gaps", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        gaps = engine.record_gaps(ctx.project_id, graph_run_id=ctx.log.run.id)
        checklists = [f"{c.domain} x {c.jurisdiction}" for c in engine.checklists(ctx.project_id)]
        self.base._deterministic_run(
            node,
            AgentRole.COMPLIANCE,
            started,
            {"gaps": len(gaps), "checklists": len(checklists)},
            output_refs={"checklist": engine.rules.ruleset_ref, "applied_to": checklists},
        )
        ctx.log.node_completed(node, gaps=len(gaps), checklists=len(checklists))
        return {"current_node": node, "compliance_gap_ids": [str(g.id) for g in gaps]}

    # ------------------------------------------------------------------
    # 16. security_privacy_derive (LLM + catalogue, role #8)
    # ------------------------------------------------------------------
    def security_privacy_derive(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "security_privacy_derive", self.ctx
        ctx.log.node_started(node)
        engine = self._engine()
        rules = engine.security_rules
        failures = 0
        calls = 0
        for key, view in ctx.compliance_pool.items():
            indicated = engine.indicated_families(ctx.project_id, view)
            ctx.indicated_families[key] = indicated
            if not state.get("semantic"):
                continue
            found = ctx.version_evidence.get(key)
            supplied = tuple(found.evidence_ids) if found is not None else ()
            evidence = self._evidence_views(supplied)
            role = SecurityPrivacyRole(ctx.gateway)
            for category in (SecurityPrivacyCategory.SECURITY, SecurityPrivacyCategory.PRIVACY):
                families = [f.family.value for f in rules.families_of(category)]
                hinted = [f.value for f in indicated if f.value in families]
                call_started = utc_now()
                try:
                    result = role.propose(
                        self._requirement(view),
                        category=category.value,
                        families=families,
                        indicated=hinted,
                        evidence=evidence,
                    )
                except EgressRefusedError as exc:
                    self._refused(node, role.role, call_started, exc)
                    failures += 1
                    continue
                calls += 1
                spec = ctx.gateway.prompts.get(result.meta.prompt_name)
                agent_run = ctx.log.agent_run(
                    node=node,
                    role=role.role,
                    status=AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED,
                    started_at=call_started,
                    meta=result.meta,
                    prompt_role=spec.role,
                    prompt_text=spec.text,
                    input_refs={"requirement_version_id": key, "category": category.value},
                    output_refs={"output_sha256": result.output_sha256},
                    error_code=str(result.error_code) if result.error_code else None,
                    evidence_ids=[str(e) for e in supplied],
                )
                if not result.ok:
                    self._malformed(node, agent_run, str(result.error_code))
                    failures += 1
                    continue
                ctx.security_results[(key, category.value)] = PendingCall(
                    result, agent_run.id, supplied
                )
        ctx.log.node_completed(node, calls=calls, semantic_failures=failures)
        return {"current_node": node, "semantic_failures": failures}

    # ------------------------------------------------------------------
    # 17. security_privacy_evaluate (DETERMINISTIC - I.7 authority)
    # ------------------------------------------------------------------
    def security_privacy_evaluate(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "security_privacy_evaluate", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        engine = self._engine()
        rules = engine.security_rules
        allowed = engine.run_evidence_ids(ctx.project_id, ctx.log.run.id)
        recorded: list[str] = []
        dropped_total = 0
        by_detector: Counter[str] = Counter()
        high = False
        for key, view in ctx.compliance_pool.items():
            indicated = ctx.indicated_families.get(key, {})
            proposals: list[AcceptedSecurityProposal] = []
            agent_runs: dict[str, uuid.UUID] = {}
            for category in (SecurityPrivacyCategory.SECURITY, SecurityPrivacyCategory.PRIVACY):
                pending = ctx.security_results.get((key, category.value))
                if pending is None:
                    continue
                output = pending.result.value
                assert isinstance(output, SecurityPrivacyOutput)
                cited = {str(e).strip().lower() for f in output.findings for e in f.evidence_ids}
                citations = engine.citation_facts(
                    ctx.project_id,
                    [e for e in pending.supplied if str(e) in cited],
                    allowed=allowed,
                )
                decision = validate_security_proposals(
                    output,
                    version_id=key,
                    category=category,
                    rules=rules,
                    supplied=frozenset(str(e) for e in pending.supplied),
                    citations=citations,
                    indicated=indicated,
                )
                for accepted in decision.accepted:
                    proposals.append(accepted)
                    agent_runs[accepted.family.value] = pending.agent_run_id
                for dropped in decision.dropped:
                    engine.record_drop(
                        ctx.project_id,
                        view,
                        dropped,
                        graph_run_id=ctx.log.run.id,
                        agent_run_id=pending.agent_run_id,
                        event=AuditEventType.SECURITY_FINDING_DROPPED,
                    )
                engine.raise_drop_review(
                    ctx.project_id,
                    view,
                    decision.dropped,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=pending.agent_run_id,
                )
                dropped_total += len(decision.dropped)
            # An indicated family no validated proposal covered still yields a
            # finding: omitting it - or having no model - cannot suppress G3.
            covered = {p.family for p in proposals}
            for family, signal in indicated.items():
                if family not in covered:
                    proposals.append(baseline_proposal(family, rules, signal_finding_id=signal))
            for proposal in proposals:
                finding = engine.record_finding(
                    ctx.project_id,
                    view,
                    proposal,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=agent_runs.get(proposal.family.value),
                )
                if finding is None:
                    continue
                recorded.append(str(finding.id))
                by_detector[str(finding.detected_by)] += 1
                # The routing flag is read back from the persisted, authoritative
                # column - never from the proposal.
                high = high or finding.risk_level is SecurityRiskLevel.HIGH
        ctx.security_results.clear()  # consumed here (D.4)
        self.base._deterministic_run(
            node,
            AgentRole.SECURITY_PRIVACY,
            started,
            {"findings": len(recorded), "dropped": dropped_total},
            output_refs={"by_detector": dict(by_detector), "risk_rules": rules.ruleset_ref},
        )
        ctx.log.node_completed(node, findings=len(recorded), dropped=dropped_total)
        return {
            "current_node": node,
            "security_finding_ids": recorded,
            "claims_dropped": dropped_total,
            "has_high_security_risk": high,
        }

    # ------------------------------------------------------------------
    # 20. gate_fanout (deterministic, Coordinator) - G2 and G3 only in P6
    # ------------------------------------------------------------------
    def gate_fanout(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "gate_fanout", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        tasks = self._engine().raise_pending_gates(ctx.project_id, graph_run_id=ctx.log.run.id)
        refs = [{"gate": str(t.gate), "task_id": str(t.id), "blocking": t.blocking} for t in tasks]
        gates = Counter(str(t.gate) for t in tasks)
        self.base._deterministic_run(
            node,
            AgentRole.COORDINATOR,
            started,
            {"tasks": len(tasks)},
            output_refs={"by_gate": dict(gates)},
        )
        ctx.log.node_completed(node, tasks=len(tasks), **{f"gate_{k}": v for k, v in gates.items()})
        return {"current_node": node, "pending_gate_tasks": refs}
