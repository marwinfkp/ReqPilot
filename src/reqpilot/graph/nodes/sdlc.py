"""The nodes of ``sdlc_graph`` (architecture C.5; roadmap phase P9).

===========================  =================  ============  ================================
Node                         Kind               Role          Writes
===========================  =================  ============  ================================
``collect_factor_evidence``  **deterministic**  SDLC (#10)    nothing (approved facts)
``propose_factor_scores``    LLM (bounded)      SDLC (#10)    nothing (validated proposals)
``apply_rules_and_mcda``     **deterministic**  SDLC (#10)    run, factors, candidates, rules
``generate_explanation``     LLM + check        SDLC (#10)    explanation, discrepancies
``raise_g6``                 **deterministic**  Coordinator   four G6 tasks
===========================  =================  ============  ================================

"The LLM proposes; deterministic code disposes." The ranking is computed and
persisted **before** any explanation is requested (architecture L.5), so the
explanation can only describe it. The explanation is checked field by field
against the persisted ranking; a discrepancy triggers one regeneration and is
then stored and shown - never repaired, never used to change the ranking.

If the semantic layer is off (the offline stub), fails or is refused, the
deterministic steps still run: the derived profile is scored and persisted,
the explanation is recorded as not generated or failed, and G6 waits until an
explanation exists (an analyst retries it).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.agents.roles.sdlc import (
    CandidateView,
    FactorView,
    ProfileView,
    RankingView,
    SdlcSelectionRole,
)
from reqpilot.agents.validation.sdlc import (
    PROPOSABLE_FACTORS,
    explanation_claims,
    validate_factor_proposals,
)
from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    ExplanationStatus,
)
from reqpilot.domain.errors import EgressRefusedError, ReqPilotError, SdlcError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.sdlc import SdlcRun
from reqpilot.domain.policy import Actor
from reqpilot.domain.sdlc.consistency import Discrepancy, check_explanation
from reqpilot.domain.sdlc.factors import FactorId
from reqpilot.domain.sdlc.profile import OverrideRecord, ProposalDecision, ranking_hash
from reqpilot.domain.sdlc.scoring import ScoringResult, score_candidates
from reqpilot.graph.state import SDLCState
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import StructuredResult
from reqpilot.rules.risk import RiskRules
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.services.audit import AuditService
from reqpilot.services.elicitation.provenance import source_facts
from reqpilot.services.extraction import RunLog
from reqpilot.services.sdlc.evidence import CollectedEvidence, SdlcEvidenceService
from reqpilot.services.sdlc.service import Computed, ExplanationOutcome, SdlcService
from reqpilot.services.traceability.scope import ScopeService

ROLE = AgentRole.SDLC_SELECTION


@dataclass
class SdlcContext:
    """The transient run context: never checkpointed (architecture D.1)."""

    session: Session
    actor: Actor
    log: RunLog
    gateway: LLMGateway
    rules: SdlcRules
    risk_rules: RiskRules
    #: The human who started the run (or whose override triggered it).
    initiator: uuid.UUID
    overrides: dict[FactorId, OverrideRecord] = field(default_factory=dict)
    new_override: FactorId | None = None
    evidence: CollectedEvidence | None = None
    proposals: dict[FactorId, ProposalDecision] = field(default_factory=dict)
    computed: Computed | None = None

    @property
    def project_id(self) -> ProjectId:
        return self.log.project_id


def _error(node: str, message: str) -> list[dict[str, Any]]:
    return [{"node": node, "message": message, "attempt": 1}]


class SdlcNodes:
    def __init__(self, ctx: SdlcContext) -> None:
        self.ctx = ctx
        self.service = SdlcService(ctx.session, ctx.actor, ctx.rules)

    # ------------------------------------------------------------------
    def _fail(self, node: str, code: str, exc: ReqPilotError) -> dict[str, Any]:
        ctx = self.ctx
        ctx.log.agent_run(
            node=node,
            role=ROLE,
            status=AgentRunStatus.FAILED,
            started_at=utc_now(),
            error_code=code,
        )
        ctx.log.node_failed(node, code)
        return {"failed": True, "errors": _error(node, str(exc))}

    def _log_call(
        self,
        node: str,
        started: dt.datetime,
        result: StructuredResult[Any],
        input_refs: dict[str, Any],
        output_refs: dict[str, Any] | None = None,
    ) -> Any:
        spec = self.ctx.gateway.prompts.get(result.meta.prompt_name)
        return self.ctx.log.agent_run(
            node=node,
            role=ROLE,
            status=AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED,
            started_at=started,
            meta=result.meta,
            prompt_role=spec.role,
            prompt_text=spec.text,
            input_refs=input_refs,
            output_refs={**(output_refs or {}), "output_sha256": result.output_sha256},
            error_code=str(result.error_code) if result.error_code else None,
        )

    def _refused(self, node: str, started: dt.datetime, exc: EgressRefusedError) -> None:
        ctx = self.ctx
        ctx.log.agent_run(
            node=node,
            role=ROLE,
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
            payload={"node": node, "error_code": "egress_refused", "detail": str(exc)[:300]},
        )

    def _provenance(self, baseline_id: uuid.UUID) -> tuple[bool, bool]:
        """``(masked, synthetic)`` of the requirement versions the profile came from."""
        ctx = self.ctx
        scope = ScopeService(ctx.session, ctx.actor).baseline_scope(ctx.project_id, baseline_id)
        refs = [r for item in scope.items for r in (item.version.source_refs or [])]
        return source_facts(ctx.session, ctx.actor, ctx.project_id, refs)

    # ------------------------------------------------------------------
    # 1. collect_factor_evidence (deterministic)
    # ------------------------------------------------------------------
    def collect_factor_evidence(self, state: SDLCState) -> dict[str, Any]:
        node, ctx = "collect_factor_evidence", self.ctx
        ctx.log.node_started(node)
        try:
            evidence = SdlcEvidenceService(
                ctx.session, ctx.actor, ctx.rules, ctx.risk_rules
            ).collect(ctx.project_id, uuid.UUID(state["baseline_id"]))
        except ReqPilotError as exc:
            return self._fail(node, "inputs_not_approved", exc)
        ctx.evidence = evidence
        ctx.log.node_completed(
            node,
            requirements=evidence.facts.requirements.count,
            eligible_risks=evidence.summary.get("eligible_risks", 0),
            input_fingerprint=evidence.input_fingerprint,
        )
        return {"current_node": node}

    # ------------------------------------------------------------------
    # 2. propose_factor_scores (LLM, bounded; then deterministic validation)
    # ------------------------------------------------------------------
    def propose_factor_scores(self, state: SDLCState) -> dict[str, Any]:
        node, ctx = "propose_factor_scores", self.ctx
        ctx.log.node_started(node)
        assert ctx.evidence is not None
        if not state.get("semantic"):
            ctx.log.node_completed(node, calls=0, semantic=False)
            return {"current_node": node, "semantic_failures": 0}
        derived = self.service.derive(ctx.evidence)
        masked, synthetic = self._provenance(ctx.evidence.baseline_id)
        profile = ProfileView(
            factors=[
                FactorView(
                    factor=str(f),
                    score=d.score,
                    source=str(d.source),
                    weight=ctx.rules.config.weight(f),
                    rationale=d.rationale,
                    evidence_refs=d.evidence_refs,
                    evidence_state=str(d.evidence_state),
                )
                for f, d in derived.items()
            ],
            masked=masked,
            synthetic=synthetic,
        )
        supplied = profile.refs()
        started = utc_now()
        role = SdlcSelectionRole(ctx.gateway)
        try:
            result = role.propose_factors(
                profile,
                proposable=[str(f) for f in PROPOSABLE_FACTORS],
                max_deviation=ctx.rules.config.max_proposal_deviation,
            )
        except EgressRefusedError as exc:
            self._refused(node, started, exc)
            ctx.log.node_completed(node, calls=0, semantic_failures=1)
            return {"current_node": node, "semantic_failures": 1}
        input_refs = {"baseline_id": str(ctx.evidence.baseline_id), "supplied_refs": len(supplied)}
        if not result.ok or result.value is None:
            self._log_call(node, started, result, input_refs)
            ctx.log.node_completed(node, calls=1, semantic_failures=1)
            return {"current_node": node, "semantic_failures": 1}
        validation = validate_factor_proposals(
            result.value,
            {f: d.score for f, d in derived.items()},
            supplied,
            max_deviation=ctx.rules.config.max_proposal_deviation,
        )
        self._log_call(
            node,
            started,
            result,
            input_refs,
            {
                "accepted": validation.accepted,
                "rejected": validation.rejected,
                "unknown_factors": validation.unknown_factors,
            },
        )
        ctx.proposals = validation.decisions
        ctx.log.node_completed(
            node, calls=1, accepted=validation.accepted, rejected=validation.rejected
        )
        return {
            "current_node": node,
            "semantic_failures": 0,
            "proposals_accepted": validation.accepted,
            "proposals_rejected": validation.rejected,
        }

    # ------------------------------------------------------------------
    # 3. apply_rules_and_mcda (deterministic; the ranking is persisted here)
    # ------------------------------------------------------------------
    def apply_rules_and_mcda(self, state: SDLCState) -> dict[str, Any]:
        node, ctx = "apply_rules_and_mcda", self.ctx
        ctx.log.node_started(node)
        assert ctx.evidence is not None
        supersedes: SdlcRun | None = None
        if state.get("supersedes_run_id"):
            supersedes = self.service.get(
                ctx.project_id, uuid.UUID(str(state["supersedes_run_id"]))
            )
        try:
            computed = self.service.compute(ctx.evidence, ctx.proposals, ctx.overrides)
            run = self.service.record(
                ctx.project_id,
                computed,
                created_by=ctx.initiator,
                graph_run_id=ctx.log.run.id,
                supersedes=supersedes,
                new_override=ctx.new_override,
            )
        except SdlcError as exc:
            return self._fail(node, "scoring_refused", exc)
        ctx.computed = computed
        ctx.log.agent_run(
            node=node,
            role=ROLE,
            status=AgentRunStatus.OK,
            started_at=utc_now(),
            input_refs={"profile_hash": computed.profile_hash},
            output_refs={
                "sdlc_run_id": str(run.id),
                "ranking_hash": run.ranking_hash,
                "top_candidate": run.top_candidate,
            },
        )
        ctx.log.node_completed(node, sdlc_run_id=str(run.id), top_candidate=run.top_candidate)
        return {"current_node": node, "sdlc_run_id": str(run.id)}

    # ------------------------------------------------------------------
    # 4. generate_explanation (LLM, then the deterministic consistency check)
    # ------------------------------------------------------------------
    def _rebuild(self, run: SdlcRun) -> tuple[ScoringResult, ProfileView, RankingView]:
        """The persisted ranking, re-derived and checked against its own hash."""
        ctx = self.ctx
        config = ctx.rules.config
        if run.rules_sha256 != ctx.rules.content_sha256:
            raise SdlcError(
                "the SDLC ruleset changed since this run was scored; start a new run rather "
                "than explaining a ranking the current rules would not reproduce"
            )
        rows = self.service.factors(ctx.project_id, run.id)
        scores = {FactorId(r.factor_id): r.score for r in rows}
        result = score_candidates(scores, config)
        if ranking_hash(result, config.ruleset_ref, config.weights_version) != run.ranking_hash:
            raise SdlcError("the persisted ranking does not reproduce; refusing to explain it")
        masked, synthetic = self._provenance(run.baseline_id)
        profile = ProfileView(
            factors=[
                FactorView(
                    factor=r.factor_id,
                    score=r.score,
                    source=r.source,
                    weight=r.weight,
                    rationale=r.rationale
                    + (
                        f" Overridden by a human: {r.previous_score} -> {r.score}."
                        if r.is_overridden
                        else ""
                    ),
                    evidence_refs=tuple(r.evidence_refs or ()),
                    evidence_state=r.evidence_state,
                    derived_score=r.derived_score,
                )
                for r in rows
            ],
            masked=masked,
            synthetic=synthetic,
        )
        rules = self.service.rule_applications(ctx.project_id, run.id)
        ranking = RankingView(
            candidates=[
                CandidateView(
                    key=c.key,
                    label=c.label,
                    rank=c.rank,
                    score=c.score,
                    mcda_score=c.mcda_score,
                    rule_effects=tuple(
                        [f"vetoed_by {v}" for v in c.vetoed_by]
                        + [f"boosted_by {b}" for b in c.boosted_by]
                        + [f"required_by {q}" for q in c.required_by]
                    ),
                )
                for c in result.candidates
            ],
            rules=[f"{r.rule_id} ({r.effect}): {r.reason}" for r in rules],
            reversals=[str(r.get("text", "")) for r in run.reversal_conditions or []],
        )
        return result, profile, ranking

    def generate_explanation(self, state: SDLCState) -> dict[str, Any]:
        node, ctx = "generate_explanation", self.ctx
        ctx.log.node_started(node)
        run = self.service.get(ctx.project_id, uuid.UUID(str(state["sdlc_run_id"])))
        if run is None:  # pragma: no cover - recorded by the previous node
            return self._fail(node, "run_missing", SdlcError("the SDLC run is missing"))
        if not state.get("semantic"):
            ctx.log.node_completed(node, calls=0, semantic=False)
            return {
                "current_node": node,
                "explanation_status": str(ExplanationStatus.NOT_GENERATED),
            }
        try:
            result, profile, ranking = self._rebuild(run)
        except SdlcError as exc:
            return self._fail(node, "not_reproducible", exc)
        runner_up = result.runner_up
        supplied = set(profile.refs())
        allowed = [
            float(v)
            for r in self.service.factors(ctx.project_id, run.id)
            for k, v in (r.basis or {}).items()
            if k.endswith("_pct")
        ]
        role = SdlcSelectionRole(ctx.gateway)
        max_attempts = 1 + ctx.rules.config.consistency.max_regenerations
        attempts = calls = failures = 0
        best: tuple[Any, tuple[Discrepancy, ...], Any, Any] | None = None
        for _ in range(max_attempts):
            attempts += 1
            started = utc_now()
            try:
                call = role.explain(
                    profile,
                    ranking,
                    top=result.top.key,
                    runner_up=runner_up.key if runner_up is not None else result.top.key,
                )
            except EgressRefusedError as exc:
                self._refused(node, started, exc)
                failures += 1
                break
            calls += 1
            if not call.ok or call.value is None:
                self._log_call(node, started, call, {"sdlc_run_id": str(run.id)})
                failures += 1
                continue
            discrepancies = check_explanation(
                explanation_claims(call.value),
                result,
                supplied,
                ctx.rules.config,
                allowed_percentages=allowed,
            )
            agent_run = self._log_call(
                node,
                started,
                call,
                {"sdlc_run_id": str(run.id), "attempt": attempts},
                {"discrepancies": [d.code for d in discrepancies]},
            )
            best = (call.value, discrepancies, call.meta, agent_run)
            if not discrepancies:
                break

        if best is None:
            outcome = ExplanationOutcome(status=ExplanationStatus.FAILED, attempts=attempts)
        else:
            draft, discrepancies, meta, agent_run = best
            outcome = ExplanationOutcome(
                status=ExplanationStatus.DISCREPANCY
                if discrepancies
                else ExplanationStatus.GENERATED,
                narrative=draft.narrative,
                counter_arguments=tuple(c.model_dump(mode="json") for c in draft.counter_arguments),
                asserted_top_candidate=draft.asserted_top_candidate,
                asserted_scores=dict(draft.asserted_scores),
                discrepancies=discrepancies,
                attempts=attempts,
                model=f"{meta.provider}:{meta.model_id}",
                prompt=meta.prompt_ref,
                agent_run_id=agent_run.id,
            )
        self.service.record_explanation(ctx.project_id, run, outcome)
        ctx.log.node_completed(
            node,
            calls=calls,
            attempts=attempts,
            status=str(outcome.status),
            discrepancies=len(outcome.discrepancies),
        )
        return {
            "current_node": node,
            "explanation_status": str(outcome.status),
            "semantic_failures": failures if best is None else 0,
        }

    # ------------------------------------------------------------------
    # 5. raise_g6 (deterministic)
    # ------------------------------------------------------------------
    def raise_g6(self, state: SDLCState) -> dict[str, Any]:
        node, ctx = "raise_g6", self.ctx
        ctx.log.node_started(node)
        run = self.service.get(ctx.project_id, uuid.UUID(str(state["sdlc_run_id"])))
        assert run is not None
        tasks = self.service.raise_g6(ctx.project_id, run)
        ctx.log.node_completed(node, g6_tasks=len(tasks))
        return {"current_node": node, "g6_task_ids": [str(t.id) for t in tasks]}
