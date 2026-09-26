"""The SDLC recommendation service (architecture C.5 nodes 2-6, L; ``FR-SDL-001``-``-008``).

Deterministic code owns every step that carries authority:

* **compute** - the derived profile, the validated proposals and the human
  overrides composed into the effective profile; MCDA; the rule pass; the
  ranking; the reversal conditions; the hashes. Pure over its inputs.
* **record** - one run, its 13 factors, its candidates and its rule
  applications, in one flush, with the audit events architecture O.2 names
  (``FACTOR_PROPOSED``, ``FACTOR_OVERRIDDEN``, ``RULES_APPLIED``,
  ``MCDA_COMPUTED``). Its trace edges (N.2 #20-#23) are derived from these rows
  by :meth:`TraceGraphSync.sdlc_edges` - a human action, like every trace link
  (policy rule 11). A run created by an
  override, or a new run for the project, supersedes the earlier live run (its
  open G6 tasks are cancelled; its rows stay exactly as they were).
* **record_explanation** - the LLM explanation *after* the ranking exists, with
  the consistency check's discrepancies stored beside it
  (``EXPLANATION_GENERATED`` / ``EXPLANATION_DISCREPANCY``). Never repaired.
* **raise_g6** - the four co-approval tasks (PM, Architect, Security Reviewer,
  Compliance Officer), one per role, sharing a ``task_group_id``, each bound to
  the recommendation hash.

The model never reaches any of this: its proposals arrive already validated
(:class:`ProposalDecision`), and its explanation's assertions are compared, not
used.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    ApprovalTaskStatus,
    AuditEventType,
    ExplanationStatus,
    Gate,
    ResourceType,
    Role,
    SdlcRunStatus,
)
from reqpilot.domain.errors import SdlcError
from reqpilot.domain.ids import ProjectId, new_task_group_id
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.sdlc import SdlcCandidate, SdlcFactor, SdlcRuleApplication, SdlcRun
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.domain.sdlc.consistency import Discrepancy
from reqpilot.domain.sdlc.derivation import DerivedFactor, derive_profile
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    RISK_DERIVED_FACTORS,
    FactorId,
    ProposalStatus,
    is_valid_score,
)
from reqpilot.domain.sdlc.profile import (
    EffectiveFactor,
    OverrideRecord,
    ProposalDecision,
    compose_profile,
    profile_hash,
    ranking_hash,
    recommendation_hash,
    reversal_payload,
    scores_of,
)
from reqpilot.domain.sdlc.scoring import (
    ReversalCondition,
    ScoringResult,
    reversal_conditions,
    score_candidates,
)
from reqpilot.repositories.approval import ApprovalTaskRepository
from reqpilot.repositories.sdlc import SdlcRunRepository
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.services.audit import AuditService
from reqpilot.services.sdlc.evidence import CollectedEvidence

SDLC_SUBJECT = ResourceType.SDLC_RUN.value

#: Runs that are still "live": a later run for the project supersedes them.
LIVE_STATUSES: frozenset[SdlcRunStatus] = frozenset(
    {SdlcRunStatus.RANKED, SdlcRunStatus.AWAITING_G6, SdlcRunStatus.REVISION_REQUESTED}
)

#: Runs a factor may be overridden on (every run that is not already replaced).
OVERRIDABLE_STATUSES: frozenset[SdlcRunStatus] = frozenset(
    {*LIVE_STATUSES, SdlcRunStatus.REJECTED, SdlcRunStatus.SELECTED}
)

#: The explanation statuses after which G6 may be raised (an explanation exists).
EXPLAINED_STATUSES: frozenset[ExplanationStatus] = frozenset(
    {ExplanationStatus.GENERATED, ExplanationStatus.DISCREPANCY}
)

MAX_REASON_CHARS = 2000


@dataclass(frozen=True)
class Computed:
    """One recommendation, computed and not yet persisted."""

    evidence: CollectedEvidence
    derived: dict[FactorId, DerivedFactor]
    profile: dict[FactorId, EffectiveFactor]
    result: ScoringResult
    reversals: tuple[ReversalCondition, ...]
    profile_hash: str
    ranking_hash: str


@dataclass(frozen=True)
class ExplanationOutcome:
    """What the explanation node produced, for the service to record as given."""

    status: ExplanationStatus
    narrative: str | None = None
    counter_arguments: tuple[dict[str, Any], ...] = ()
    asserted_top_candidate: str | None = None
    asserted_scores: dict[str, float] = field(default_factory=dict)
    discrepancies: tuple[Discrepancy, ...] = ()
    attempts: int = 0
    model: str | None = None
    prompt: str | None = None
    agent_run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class OverrideRequest:
    """A human's override of one factor on one run, checked and ready to apply."""

    run: SdlcRun
    record: OverrideRecord


def ordered_factors(rows: list[SdlcFactor]) -> list[SdlcFactor]:
    position = {str(f): i for i, f in enumerate(FACTOR_ORDER)}
    return sorted(rows, key=lambda r: position.get(r.factor_id, 99))


def run_recommendation_hash(run: SdlcRun) -> str:
    return recommendation_hash(
        run_id=run.id,
        project_id=run.project_id,
        ranking_hash_value=run.ranking_hash,
        top_candidate=run.top_candidate,
        explanation_status=str(run.explanation_status),
        narrative=run.explanation_narrative,
        counter_arguments=run.explanation_counter_arguments or [],
        discrepancies=run.explanation_discrepancies or [],
    )


class SdlcService:
    def __init__(self, session: Session, actor: Actor, rules: SdlcRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._runs = SdlcRunRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def rules(self) -> SdlcRules:
        return self._rules

    # ------------------------------------------------------------------
    # compute (pure over its inputs)
    # ------------------------------------------------------------------
    def derive(self, evidence: CollectedEvidence) -> dict[FactorId, DerivedFactor]:
        return derive_profile(evidence.facts, self._rules.config)

    def compute(
        self,
        evidence: CollectedEvidence,
        proposals: dict[FactorId, ProposalDecision],
        overrides: dict[FactorId, OverrideRecord],
    ) -> Computed:
        config = self._rules.config
        derived = self.derive(evidence)
        for f, decision in proposals.items():
            # Defence in depth: validation already refuses these.
            if decision.status is ProposalStatus.ACCEPTED and (
                f in RISK_DERIVED_FACTORS
                or not is_valid_score(decision.proposed_score)
                or abs(int(decision.proposed_score or 0) - derived[f].score)
                > config.max_proposal_deviation
            ):
                raise SdlcError(f"an accepted proposal for {f} is outside what validation allows")
        profile = compose_profile(derived, proposals, overrides)
        scores = scores_of(profile)
        result = score_candidates(scores, config)
        return Computed(
            evidence=evidence,
            derived=derived,
            profile=profile,
            result=result,
            reversals=reversal_conditions(scores, config, result),
            profile_hash=profile_hash(scores),
            ranking_hash=ranking_hash(result, config.ruleset_ref, config.weights_version),
        )

    # ------------------------------------------------------------------
    # overrides (FR-SDL-003)
    # ------------------------------------------------------------------
    def prepare_override(
        self,
        human: Actor,
        project_id: ProjectId,
        run_id: uuid.UUID,
        factor: str,
        new_score: int,
        reason: str,
        role: Role,
    ) -> OverrideRequest:
        """Check a human's override request. Nothing is written here."""
        require(
            human,
            Action.SDLC_FACTOR_OVERRIDE,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        )
        if role not in human.roles_in(project_id):
            raise SdlcError(f"the override is made as {role}, which the actor does not hold here")
        single_role = Actor(
            actor_id=human.actor_id,
            kind=human.kind,
            roles_by_project={project_id: frozenset({role})},
        )
        if not can(
            single_role,
            Action.SDLC_FACTOR_OVERRIDE,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        ):
            raise SdlcError(f"{role} may not override an SDLC factor")
        try:
            fid = FactorId(factor)
        except ValueError as exc:
            raise SdlcError(f"{factor!r} is not one of the 13 SDLC factors") from exc
        if not is_valid_score(new_score):
            raise SdlcError("a factor score is an integer from 1 to 5")
        cleaned = " ".join((reason or "").split())
        if not cleaned:
            raise SdlcError("an override needs a reason (FR-SDL-003)")
        if len(cleaned) > MAX_REASON_CHARS:
            raise SdlcError("the reason is too long")
        run = self._runs.get(project_id, run_id)
        if run is None:
            raise SdlcError("SDLC run not found in this project")
        if run.status not in OVERRIDABLE_STATUSES:
            raise SdlcError(
                f"this run is {run.status}; override a factor on the run that replaced it"
            )
        current = next(
            (r for r in self._runs.factors(project_id, run.id) if r.factor_id == str(fid)), None
        )
        if current is None:  # pragma: no cover - every run has all 13
            raise SdlcError("the run has no such factor")
        if current.score == new_score:
            raise SdlcError(f"{fid} is already {new_score}; an override must change the score")
        return OverrideRequest(
            run=run,
            record=OverrideRecord(
                factor=fid,
                new_score=new_score,
                previous_score=current.score,
                reason=cleaned,
                actor_id=human.actor_id,
                role=str(role),
                at=utc_now(),
            ),
        )

    def carried_overrides(
        self, project_id: ProjectId, run: SdlcRun
    ) -> dict[FactorId, OverrideRecord]:
        """The overrides already on a run: an override persists into its successors."""
        out: dict[FactorId, OverrideRecord] = {}
        for row in self._runs.factors(project_id, run.id):
            if not row.is_overridden:
                continue
            assert row.previous_score is not None and row.overridden_by is not None
            assert row.override_reason is not None and row.overridden_at is not None
            out[FactorId(row.factor_id)] = OverrideRecord(
                factor=FactorId(row.factor_id),
                new_score=row.score,
                previous_score=row.previous_score,
                reason=row.override_reason,
                actor_id=row.overridden_by,
                role=str(row.override_role),
                at=row.overridden_at,
            )
        return out

    # ------------------------------------------------------------------
    # record
    # ------------------------------------------------------------------
    def record(
        self,
        project_id: ProjectId,
        computed: Computed,
        *,
        created_by: uuid.UUID,
        graph_run_id: uuid.UUID | None,
        supersedes: SdlcRun | None = None,
        new_override: FactorId | None = None,
    ) -> SdlcRun:
        config = self._rules.config
        result = computed.result
        runner_up = result.runner_up
        run = SdlcRun(
            id=uuid.uuid4(),
            project_id=project_id,
            baseline_id=computed.evidence.baseline_id,
            graph_run_id=graph_run_id,
            supersedes_run_id=supersedes.id if supersedes is not None else None,
            status=SdlcRunStatus.RANKED,
            ruleset_version=config.version,
            ruleset_ref=config.ruleset_ref,
            rules_sha256=self._rules.content_sha256,
            weights_version=config.weights_version,
            input_fingerprint=computed.evidence.input_fingerprint,
            profile_hash=computed.profile_hash,
            ranking_hash=computed.ranking_hash,
            top_candidate=result.top.key,
            runner_up_candidate=runner_up.key if runner_up is not None else None,
            reversal_conditions=reversal_payload(computed.reversals),
            facts_summary=dict(computed.evidence.summary),
            explanation_status=ExplanationStatus.NOT_GENERATED,
            explanation_counter_arguments=[],
            asserted_scores={},
            explanation_discrepancies=[],
            explanation_attempts=0,
            created_by=created_by,
        )
        factors = [self._factor_row(project_id, computed.profile[f]) for f in FACTOR_ORDER]
        candidates = [
            SdlcCandidate(
                project_id=project_id,
                candidate_key=c.key,
                label=c.label,
                raw_score=c.raw,
                max_raw=c.max_raw,
                mcda_score=c.mcda_score,
                normalised_score=c.score,
                rank=c.rank,
                vetoed_by=list(c.vetoed_by),
                boosted_by=list(c.boosted_by),
                required_by=list(c.required_by),
                contributions=dict(c.contributions),
            )
            for c in result.candidates
        ]
        rules = [
            SdlcRuleApplication(
                project_id=project_id,
                rule_id=r.rule_id,
                effect=str(r.effect),
                affected_candidate=r.affected_candidate,
                changed_ranking=r.changed_ranking,
                reason=r.reason,
                trigger_values={str(k): int(v) for k, v in r.trigger_values.items()},
            )
            for r in result.rules
        ]
        self._runs.add(run, factors, candidates, rules)
        self._audit_recorded(project_id, run, computed, factors, new_override)
        self._supersede_earlier(project_id, run, supersedes)
        return run

    def _factor_row(self, project_id: ProjectId, item: EffectiveFactor) -> SdlcFactor:
        base, proposal, override = item.derived, item.proposal, item.override
        return SdlcFactor(
            project_id=project_id,
            factor_id=str(item.factor),
            score=item.score,
            source=str(item.source),
            weight=self._rules.config.weight(item.factor),
            derived_score=base.score,
            rationale=base.rationale,
            evidence_refs=list(base.evidence_refs),
            evidence_state=str(base.evidence_state),
            basis={str(k): v for k, v in base.basis.items()},
            proposal_status=str(proposal.status),
            proposed_score=proposal.proposed_score,
            proposal_rationale=proposal.rationale,
            proposal_evidence_refs=list(proposal.evidence_refs),
            proposal_rejection_reason=proposal.rejection_reason,
            is_overridden=override is not None,
            previous_score=override.previous_score if override else None,
            override_reason=override.reason if override else None,
            overridden_by=override.actor_id if override else None,
            override_role=override.role if override else None,
            overridden_at=override.at if override else None,
        )

    def _event(
        self,
        event_type: AuditEventType,
        project_id: ProjectId,
        run: SdlcRun,
        payload: dict[str, Any],
    ) -> None:
        self._audit.append(
            event_type=event_type,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=SDLC_SUBJECT,
            subject_id=str(run.id),
            subject_version=run.ruleset_ref,
            graph_run_id=run.graph_run_id,
            payload={"sdlc_run_id": str(run.id), **payload},
        )

    def _audit_recorded(
        self,
        project_id: ProjectId,
        run: SdlcRun,
        computed: Computed,
        factors: list[SdlcFactor],
        new_override: FactorId | None,
    ) -> None:
        proposals = {
            row.factor_id: row.proposal_status
            for row in factors
            if row.proposal_status != str(ProposalStatus.NOT_REQUESTED)
        }
        self._event(
            AuditEventType.FACTOR_PROPOSED,
            project_id,
            run,
            {
                "baseline_id": str(run.baseline_id),
                "input_fingerprint": run.input_fingerprint,
                "profile_hash": run.profile_hash,
                "factors": {
                    row.factor_id: {"score": row.score, "source": row.source} for row in factors
                },
                "model_proposals": proposals,
                "not_recorded": sorted(
                    row.factor_id for row in factors if row.evidence_state == "not_recorded"
                ),
            },
        )
        for row in factors:
            if not row.is_overridden:
                continue
            self._event(
                AuditEventType.FACTOR_OVERRIDDEN,
                project_id,
                run,
                {
                    "factor": row.factor_id,
                    "previous_score": row.previous_score,
                    "new_score": row.score,
                    # The reason is stored on the factor row; the audit event
                    # carries its digest (references only, never content).
                    "reason_sha256": hashlib.sha256(
                        (row.override_reason or "").encode("utf-8")
                    ).hexdigest(),
                    "overridden_by": str(row.overridden_by),
                    "role": row.override_role,
                    "overridden_at": row.overridden_at.isoformat() if row.overridden_at else None,
                    "supersedes_run_id": str(run.supersedes_run_id)
                    if run.supersedes_run_id
                    else None,
                    "carried_forward": row.factor_id
                    != (str(new_override) if new_override else None),
                },
            )
        result = computed.result
        self._event(
            AuditEventType.RULES_APPLIED,
            project_id,
            run,
            {
                "ruleset_ref": run.ruleset_ref,
                "triggered": [
                    {
                        "rule_id": r.rule_id,
                        "effect": str(r.effect),
                        "affected_candidate": r.affected_candidate,
                        "changed_ranking": r.changed_ranking,
                    }
                    for r in result.rules
                ],
            },
        )
        self._event(
            AuditEventType.MCDA_COMPUTED,
            project_id,
            run,
            {
                "weights_version": run.weights_version,
                "rules_sha256": run.rules_sha256,
                "ranking_hash": run.ranking_hash,
                "ranking": [
                    {
                        "candidate": c.key,
                        "rank": c.rank,
                        "score": c.score,
                        "mcda_score": c.mcda_score,
                    }
                    for c in result.candidates
                ],
                "top_candidate": run.top_candidate,
                "runner_up_candidate": run.runner_up_candidate,
                "reversal_conditions": len(run.reversal_conditions),
            },
        )

    def _supersede_earlier(
        self, project_id: ProjectId, run: SdlcRun, supersedes: SdlcRun | None
    ) -> None:
        """A new run replaces every live run; an override also replaces its source run."""
        replaced = [
            r
            for r in self._runs.list_for_project(project_id)
            if r.id != run.id
            and (
                r.status in LIVE_STATUSES
                or (
                    supersedes is not None
                    and r.id == supersedes.id
                    and r.status is SdlcRunStatus.REJECTED
                )
            )
        ]
        for earlier in replaced:
            self.supersede(project_id, earlier, by=run)

    def supersede(self, project_id: ProjectId, earlier: SdlcRun, *, by: SdlcRun) -> None:
        cancelled = self._cancel_open_g6(project_id, earlier)
        previous = earlier.status
        earlier.status = SdlcRunStatus.SUPERSEDED
        self._runs.save_lifecycle(earlier)
        self._event(
            AuditEventType.SDLC_RUN_SUPERSEDED,
            project_id,
            earlier,
            {
                "previous_status": str(previous),
                "superseded_by": str(by.id),
                "g6_tasks_cancelled": cancelled,
            },
        )

    def _cancel_open_g6(self, project_id: ProjectId, run: SdlcRun) -> int:
        if run.g6_task_group_id is None:
            return 0
        count = 0
        for task in ApprovalTaskRepository(self._session, self._actor).list_in_group(
            project_id, run.g6_task_group_id
        ):
            if task.status is ApprovalTaskStatus.OPEN:
                task.status = ApprovalTaskStatus.CANCELLED
                count += 1
        self._session.flush()
        return count

    # ------------------------------------------------------------------
    # the explanation (architecture L.5)
    # ------------------------------------------------------------------
    def record_explanation(
        self, project_id: ProjectId, run: SdlcRun, outcome: ExplanationOutcome
    ) -> SdlcRun:
        require(
            self._actor,
            Action.SDLC_RECORD,
            ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=project_id),
        )
        if run.status is not SdlcRunStatus.RANKED:
            raise SdlcError(
                f"an explanation is recorded for a ranked run; this one is {run.status}"
            )
        if run.explanation_status in EXPLAINED_STATUSES:
            raise SdlcError("this run's explanation is already recorded; it is written once")
        run.explanation_attempts = (run.explanation_attempts or 0) + outcome.attempts
        run.explanation_status = outcome.status
        if outcome.status in EXPLAINED_STATUSES:
            run.explanation_narrative = outcome.narrative
            run.explanation_counter_arguments = [dict(c) for c in outcome.counter_arguments]
            run.asserted_top_candidate = outcome.asserted_top_candidate
            run.asserted_scores = {str(k): float(v) for k, v in outcome.asserted_scores.items()}
            run.explanation_discrepancies = [d.as_dict() for d in outcome.discrepancies]
            run.explanation_model = outcome.model
            run.explanation_prompt = outcome.prompt
            run.explanation_agent_run_id = outcome.agent_run_id
            run.explained_at = utc_now()
        self._runs.save_lifecycle(run)
        if outcome.status is ExplanationStatus.GENERATED:
            self._event(
                AuditEventType.EXPLANATION_GENERATED,
                project_id,
                run,
                {
                    "attempts": run.explanation_attempts,
                    "model": outcome.model,
                    "prompt_ref": outcome.prompt,
                    "consistent": True,
                    "asserted_top_candidate": outcome.asserted_top_candidate,
                },
            )
        elif outcome.status is ExplanationStatus.DISCREPANCY:
            self._event(
                AuditEventType.EXPLANATION_DISCREPANCY,
                project_id,
                run,
                {
                    "attempts": run.explanation_attempts,
                    "model": outcome.model,
                    "prompt_ref": outcome.prompt,
                    "discrepancies": [d.as_dict() for d in outcome.discrepancies],
                    "computed_top_candidate": run.top_candidate,
                    "asserted_top_candidate": outcome.asserted_top_candidate,
                    "ranking_unchanged": True,
                },
            )
        else:
            self._event(
                AuditEventType.EXPLANATION_GENERATED,
                project_id,
                run,
                {
                    "attempts": run.explanation_attempts,
                    "status": str(outcome.status),
                    "consistent": None,
                    "note": "no explanation was stored; the ranking stands and G6 waits for one",
                },
            )
        return run

    # ------------------------------------------------------------------
    # G6 (FR-SDL-008; architecture M.3)
    # ------------------------------------------------------------------
    def raise_g6(self, project_id: ProjectId, run: SdlcRun) -> list[ApprovalTask]:
        from reqpilot.services.approval.service import ApprovalService, tasks_required_for

        if run.status is not SdlcRunStatus.RANKED:
            raise SdlcError(f"G6 is raised for a ranked run; this one is {run.status}")
        if run.explanation_status not in EXPLAINED_STATUSES:
            raise SdlcError(
                "G6 is raised once the ranking has its explanation; retry the explanation first"
            )
        run.recommendation_hash = run_recommendation_hash(run)
        group = new_task_group_id()
        approvals = ApprovalService(self._session, self._actor)
        tasks = [
            approvals.create_task(
                project_id=project_id,
                gate=Gate.G6_SDLC_SELECTION,
                subject_type=SDLC_SUBJECT,
                subject_id=run.id,
                subject_version=f"sdlc {str(run.id)[:8]} ({run.top_candidate})",
                subject_version_hash=run.recommendation_hash,
                required_role=role,
                task_group_id=group,
                _action=Action.GATE_TASK_RAISE,
            )
            for role in tasks_required_for(Gate.G6_SDLC_SELECTION)
        ]
        run.g6_task_group_id = group
        run.status = SdlcRunStatus.AWAITING_G6
        self._runs.save_lifecycle(run)
        return tasks

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get(self, project_id: ProjectId, run_id: uuid.UUID) -> SdlcRun | None:
        return self._runs.get(project_id, run_id)

    def list_runs(self, project_id: ProjectId) -> list[SdlcRun]:
        return self._runs.list_for_project(project_id)

    def factors(self, project_id: ProjectId, run_id: uuid.UUID) -> list[SdlcFactor]:
        return ordered_factors(self._runs.factors(project_id, run_id))

    def candidates(self, project_id: ProjectId, run_id: uuid.UUID) -> list[SdlcCandidate]:
        return self._runs.candidates(project_id, run_id)

    def rule_applications(
        self, project_id: ProjectId, run_id: uuid.UUID
    ) -> list[SdlcRuleApplication]:
        return self._runs.rule_applications(project_id, run_id)

    def g6_tasks(self, project_id: ProjectId, run: SdlcRun) -> list[ApprovalTask]:
        if run.g6_task_group_id is None:
            return []
        return ApprovalTaskRepository(self._session, self._actor).list_in_group(
            project_id, run.g6_task_group_id
        )
