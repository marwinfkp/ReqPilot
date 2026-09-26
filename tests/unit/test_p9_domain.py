"""P9 domain and agent-boundary units: derivation, precedence, consistency, validation, policy.

Pure functions only - no database, no model. The ruleset is the packaged,
frozen ``sdlc_rules.yaml``; each expected value is derived in the comment from
its bands.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError

from reqpilot.agents.contracts.sdlc import ExplanationDraft, FactorProposalOutput
from reqpilot.agents.validation.sdlc import (
    PROPOSABLE_FACTORS,
    explanation_claims,
    validate_factor_proposals,
)
from reqpilot.domain.enums import GATE_REQUIRED_ROLES, Action, ActorKind, Gate, ResourceType, Role
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.domain.sdlc.consistency import (
    CounterArgumentClaim,
    ExplanationClaims,
    check_explanation,
)
from reqpilot.domain.sdlc.derivation import derive_profile
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    RISK_DERIVED_FACTORS,
    EvidenceState,
    FactorId,
    FactorSource,
    ProposalStatus,
)
from reqpilot.domain.sdlc.facts import FactorFacts, RiskAggregate, Signal
from reqpilot.domain.sdlc.profile import (
    OverrideRecord,
    ProposalDecision,
    compose_profile,
    profile_hash,
    ranking_hash,
    recommendation_hash,
    scores_of,
)
from reqpilot.domain.sdlc.scoring import score_candidates
from reqpilot.rules.sdlc import packaged_sdlc_rules

pytestmark = pytest.mark.unit

F = FactorId
RULES = packaged_sdlc_rules()
CONFIG = RULES.config


def sig(count: int, kind: str = "requirement_version") -> Signal:
    return Signal(count, tuple(f"{kind}:{uuid.UUID(int=i + 1)}" for i in range(count)))


def facts(**overrides: object) -> FactorFacts:
    base: dict[str, object] = {
        "scope_ref": "baseline:00000000-0000-0000-0000-0000000000bb",
        "scope_label": "baseline B1",
        "requirements": sig(20),
        "stakeholders": sig(4, "stakeholder"),
    }
    base.update(overrides)
    return FactorFacts(**base)  # type: ignore[arg-type]


# --- derivation ---------------------------------------------------------------------------


def test_derivation_scores_every_factor_with_evidence_or_a_flag() -> None:
    profile = derive_profile(facts(), CONFIG)
    assert list(profile) == list(FACTOR_ORDER)
    for f, d in profile.items():
        assert 1 <= d.score <= 5, f
        if d.evidence_state is EvidenceState.NOT_RECORDED:
            assert d.score == 3 and not d.evidence_refs, f
        else:
            assert d.evidence_refs, f"{f} cites evidence or the scope it examined"


def test_absent_signals_cite_the_scope_and_unrecorded_ones_are_neutral() -> None:
    profile = derive_profile(facts(), CONFIG)
    # No legacy requirement in 20: band legacy_requirements -> 1, citing the baseline.
    legacy = profile[F.LEGACY_SYSTEM_DEPENDENCE]
    assert legacy.score == 1
    assert legacy.evidence_state is EvidenceState.ABSENT_IN_SCOPE
    assert legacy.evidence_refs == ("baseline:00000000-0000-0000-0000-0000000000bb",)
    # No schedule statement: budget/schedule is not recorded, neutral 3, flagged.
    budget = profile[F.BUDGET_AND_SCHEDULE_CONSTRAINTS]
    assert budget.evidence_state is EvidenceState.NOT_RECORDED and budget.score == 3


def test_stability_churn_and_change_ratio_by_hand() -> None:
    # 2 revised + 1 conflict + 3 change signals over 20 = 30% churn -> band min 26 -> 2.
    # change ratio = (3 + 2) / 20 = 25% -> band min 20 -> 4.
    profile = derive_profile(
        facts(revised_requirements=sig(2), conflicts=sig(1, "conflict"), change_signals=sig(3)),
        CONFIG,
    )
    assert profile[F.REQUIREMENT_STABILITY].score == 2
    assert profile[F.REQUIREMENT_STABILITY].basis["churn_pct"] == 30.0
    assert profile[F.EXPECTED_FREQUENCY_OF_CHANGE].score == 4


def test_risk_derived_factors_are_the_p7_aggregates() -> None:
    security = RiskAggregate(4, ("risk:" + str(uuid.UUID(int=9)),), {"high": 1})
    consequence = RiskAggregate(5, ("risk:" + str(uuid.UUID(int=10)),))
    profile = derive_profile(
        facts(security_risk=security, consequences_of_failure=consequence), CONFIG
    )
    assert profile[F.SECURITY_RISK].score == 4
    assert profile[F.SECURITY_RISK].source is FactorSource.RISK_AGGREGATE
    assert profile[F.CONSEQUENCES_OF_FAILURE].score == 5
    # Regulatory criticality is max(risk 1, 0 sources -> 1, 0 gaps -> 1) = 1 here, and
    # max(1, 3 sources -> 4, 1 gap -> 3) = 4 with sources and a gap.
    assert profile[F.REGULATORY_CRITICALITY].score == 1
    raised = derive_profile(
        facts(
            normative_sources=sig(3, "compliance_mapping"),
            open_compliance_gaps=sig(1, "compliance_gap"),
        ),
        CONFIG,
    )
    assert raised[F.REGULATORY_CRITICALITY].score == 4


def test_an_empty_scope_is_not_recorded_rather_than_invented() -> None:
    profile = derive_profile(facts(requirements=Signal(0), stakeholders=Signal(0)), CONFIG)
    for f in (F.REQUIREMENT_STABILITY, F.SYSTEM_SIZE, F.STAKEHOLDER_AVAILABILITY):
        assert profile[f].evidence_state is EvidenceState.NOT_RECORDED, f


# --- precedence: derived < accepted proposal < human override -----------------------------


def _override(f: FactorId, new: int, previous: int) -> OverrideRecord:
    return OverrideRecord(
        factor=f,
        new_score=new,
        previous_score=previous,
        reason="Reviewed with the architect (synthetic).",
        actor_id=uuid.uuid4(),
        role="project_manager",
        at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


def test_a_human_override_beats_an_accepted_proposal_which_beats_the_derivation() -> None:
    derived = derive_profile(facts(), CONFIG)
    f = F.NEED_FOR_FORMAL_VERIFICATION
    base = derived[f].score
    accepted = ProposalDecision(ProposalStatus.ACCEPTED, proposed_score=base + 1, rationale="r")
    rejected = ProposalDecision(ProposalStatus.REJECTED, proposed_score=5, rejection_reason="x")
    only_proposal = compose_profile(derived, {f: accepted}, {})
    assert only_proposal[f].score == base + 1
    assert only_proposal[f].source is FactorSource.MODEL_PROPOSAL
    assert compose_profile(derived, {f: rejected}, {})[f].score == base
    both = compose_profile(derived, {f: accepted}, {f: _override(f, 5, base + 1)})
    assert both[f].score == 5 and both[f].source is FactorSource.HUMAN_OVERRIDE
    assert both[f].derived.score == base, "the derived value is kept beside the override"


def test_an_override_needs_a_reason_and_a_valid_score() -> None:
    with pytest.raises(ValueError):
        OverrideRecord(F.SECURITY_RISK, 6, 3, "r", uuid.uuid4(), "analyst", dt.datetime.now(dt.UTC))
    with pytest.raises(ValueError):
        OverrideRecord(
            F.SECURITY_RISK, 4, 3, "   ", uuid.uuid4(), "analyst", dt.datetime.now(dt.UTC)
        )


def test_hashes_are_deterministic_and_sensitive() -> None:
    derived = derive_profile(facts(), CONFIG)
    scores = scores_of(compose_profile(derived, {}, {}))
    result = score_candidates(scores, CONFIG)
    assert profile_hash(scores) == profile_hash(dict(scores))
    first = ranking_hash(result, CONFIG.ruleset_ref, CONFIG.weights_version)
    assert first == ranking_hash(
        score_candidates(scores, CONFIG), CONFIG.ruleset_ref, CONFIG.weights_version
    )
    changed = dict(scores)
    changed[F.SECURITY_RISK] = 5 if scores[F.SECURITY_RISK] != 5 else 1
    assert profile_hash(changed) != profile_hash(scores)
    kwargs = {
        "run_id": uuid.UUID(int=1),
        "project_id": uuid.UUID(int=2),
        "ranking_hash_value": first,
        "top_candidate": result.top.key,
        "explanation_status": "generated",
        "narrative": "n",
        "counter_arguments": [],
        "discrepancies": [],
    }
    assert recommendation_hash(**kwargs) == recommendation_hash(**kwargs)  # type: ignore[arg-type]
    assert recommendation_hash(**{**kwargs, "narrative": "m"}) != recommendation_hash(**kwargs)  # type: ignore[arg-type]


# --- proposal validation (role #10, deterministic) ----------------------------------------


def test_proposal_validation_accepts_only_bounded_cited_non_risk_proposals() -> None:
    derived = dict.fromkeys(FACTOR_ORDER, 3)
    supplied = {"requirement_version:a", "baseline:b"}
    output = FactorProposalOutput.model_validate(
        {
            "proposals": [
                {"factor": "need_for_formal_verification", "proposed_score": 4,
                 "rationale": "dual control", "evidence_refs": ["requirement_version:a"]},
                {"factor": "security_risk", "proposed_score": 4, "rationale": "r",
                 "evidence_refs": ["baseline:b"]},
                {"factor": "system_size", "proposed_score": 5, "rationale": "r",
                 "evidence_refs": ["baseline:b"]},
                {"factor": "legacy_system_dependence", "proposed_score": "four",
                 "rationale": "r", "evidence_refs": ["baseline:b"]},
                {"factor": "stakeholder_availability", "proposed_score": 2, "rationale": "r",
                 "evidence_refs": ["risk:invented"]},
                {"factor": "need_for_continuous_delivery", "proposed_score": 2,
                 "rationale": "", "evidence_refs": ["baseline:b"]},
                {"factor": "expected_frequency_of_change", "proposed_score": 2,
                 "rationale": "r", "evidence_refs": []},
                {"factor": "budget_and_schedule_constraints", "proposed_score": 2,
                 "rationale": "r", "evidence_refs": ["baseline:b"]},
                {"factor": "budget_and_schedule_constraints", "proposed_score": 4,
                 "rationale": "r", "evidence_refs": ["baseline:b"]},
                {"factor": "choose_waterfall", "proposed_score": 5, "rationale": "r",
                 "evidence_refs": ["baseline:b"]},
            ]
        }
    )  # fmt: skip
    result = validate_factor_proposals(output, derived, supplied, max_deviation=1)
    d = result.decisions
    assert d[F.NEED_FOR_FORMAL_VERIFICATION].status is ProposalStatus.ACCEPTED
    assert "RISK_DERIVED" in (d[F.SECURITY_RISK].rejection_reason or "")
    assert "OUT_OF_BOUNDS" in (d[F.SYSTEM_SIZE].rejection_reason or "")
    assert "INVALID_SCORE" in (d[F.LEGACY_SYSTEM_DEPENDENCE].rejection_reason or "")
    assert "UNSUPPLIED_EVIDENCE" in (d[F.STAKEHOLDER_AVAILABILITY].rejection_reason or "")
    assert "NO_RATIONALE" in (d[F.NEED_FOR_CONTINUOUS_DELIVERY].rejection_reason or "")
    assert "NO_EVIDENCE" in (d[F.EXPECTED_FREQUENCY_OF_CHANGE].rejection_reason or "")
    assert "DUPLICATE" in (d[F.BUDGET_AND_SCHEDULE_CONSTRAINTS].rejection_reason or "")
    assert d[F.REQUIREMENT_STABILITY].status is ProposalStatus.MISSING
    assert result.unknown_factors == 1
    assert result.accepted == 1
    assert set(PROPOSABLE_FACTORS) | RISK_DERIVED_FACTORS == set(FACTOR_ORDER)


def test_the_contracts_have_nowhere_to_put_a_ranking_or_a_selection() -> None:
    with pytest.raises(ValidationError):
        FactorProposalOutput.model_validate({"proposals": [], "ranking": ["agile"]})
    good = {
        "narrative": "n [factor:security_risk]",
        "asserted_top_candidate": "agile",
        "asserted_scores": {"agile": 70.0},
    }
    ExplanationDraft.model_validate(good)
    for extra in ("selected_candidate", "approve", "g6_decision", "weights"):
        with pytest.raises(ValidationError):
            ExplanationDraft.model_validate({**good, extra: "agile"})


# --- the explanation consistency check ------------------------------------------------------


def _result():  # type: ignore[no-untyped-def]
    derived = derive_profile(facts(), CONFIG)
    return score_candidates(scores_of(compose_profile(derived, {}, {})), CONFIG)


def _claims(result, **overrides):  # type: ignore[no-untyped-def]
    top, runner = result.top, result.runner_up
    values = {
        "narrative": f"{top.key} first [factor:security_risk] [ref:baseline:x]",
        "counter_arguments": (
            CounterArgumentClaim(runner.key, "lower score", "if change rose", ("security_risk",)),
        ),
        "asserted_top_candidate": top.key,
        "asserted_scores": {top.key: top.score, runner.key: runner.score},
    }
    values.update(overrides)
    return ExplanationClaims(**values)  # type: ignore[arg-type]


def codes(result, claims, supplied=("baseline:x",)) -> set[str]:  # type: ignore[no-untyped-def]
    return {d.code for d in check_explanation(claims, result, supplied, CONFIG)}


def test_a_consistent_explanation_has_no_discrepancy() -> None:
    result = _result()
    assert codes(result, _claims(result)) == set()


def test_every_disagreement_is_named() -> None:
    result = _result()
    top, runner = result.top, result.runner_up
    assert "TOP_MISMATCH" in codes(result, _claims(result, asserted_top_candidate=runner.key))
    assert "SCORE_MISMATCH" in codes(
        result, _claims(result, asserted_scores={top.key: top.score + 5})
    )
    assert "TOP_SCORE_MISSING" in codes(result, _claims(result, asserted_scores={}))
    assert "UNKNOWN_CANDIDATE" in codes(
        result, _claims(result, asserted_scores={top.key: top.score, "kanban": 50.0})
    )
    assert "RUNNER_UP_NOT_ADDRESSED" in codes(result, _claims(result, counter_arguments=()))
    assert "REVERSAL_MISSING" in codes(
        result,
        _claims(result, counter_arguments=(CounterArgumentClaim(runner.key, "lower", ""),)),
    )
    assert "COUNTER_ARGUMENT_ON_TOP" in codes(
        result,
        _claims(
            result,
            counter_arguments=(
                CounterArgumentClaim(runner.key, "lower", "if"),
                CounterArgumentClaim(top.key, "odd", "if"),
            ),
        ),
    )
    assert "NO_FACTOR_CITED" in codes(
        result,
        _claims(
            result,
            narrative="plain",
            counter_arguments=(CounterArgumentClaim(runner.key, "lower", "if"),),
        ),
    )
    assert "UNKNOWN_FACTOR_CITED" in codes(result, _claims(result, narrative="[factor:vibes]"))
    assert "UNSUPPLIED_EVIDENCE_CITED" in codes(
        result, _claims(result, narrative="x [factor:security_risk] [ref:risk:fabricated]")
    )
    assert "MODEL_SELECTION_CLAIM" in codes(
        result,
        _claims(result, narrative="The AI selected this [factor:security_risk] [ref:baseline:x]"),
    )
    assert "UNSUPPORTED_NUMBER" in codes(
        result, _claims(result, narrative="97.3% sure [factor:security_risk] [ref:baseline:x]")
    )


def test_the_draft_becomes_claims_with_normalised_keys() -> None:
    draft = ExplanationDraft.model_validate(
        {
            "narrative": "n",
            "counter_arguments": [
                {"candidate": " Agile ", "why_not_selected": "w", "reversal_condition": "r"}
            ],
            "asserted_top_candidate": "V_Model",
            "asserted_scores": {"V_Model": 70.5},
        }
    )
    claims = explanation_claims(draft)
    assert claims.asserted_top_candidate == "v_model"
    assert claims.asserted_scores == {"v_model": 70.5}
    assert claims.counter_arguments[0].candidate == "agile"


# --- policy (rule 12) and G6 roles --------------------------------------------------------


def test_g6_requires_the_four_named_roles() -> None:
    assert GATE_REQUIRED_ROLES[Gate.G6_SDLC_SELECTION] == frozenset(
        {Role.PROJECT_MANAGER, Role.ARCHITECT, Role.SECURITY_REVIEWER, Role.COMPLIANCE_OFFICER}
    )


def _actor(role: Role, kind: ActorKind = ActorKind.HUMAN) -> tuple[Actor, ResourceRef]:
    pid = uuid.uuid4()
    actor = Actor(actor_id=uuid.uuid4(), kind=kind, roles_by_project={pid: frozenset({role})})  # type: ignore[arg-type]
    return actor, ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=pid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("action", "allowed"),
    [
        (Action.SDLC_RUN_START, {Role.ANALYST}),
        (Action.SDLC_FACTOR_OVERRIDE, {Role.ANALYST, Role.PROJECT_MANAGER}),
        (Action.SDLC_EXPLAIN, {Role.ANALYST}),
        (
            Action.SDLC_READ,
            {
                Role.ANALYST,
                Role.COMPLIANCE_OFFICER,
                Role.SECURITY_REVIEWER,
                Role.PROJECT_MANAGER,
                Role.ARCHITECT,
                Role.AUDITOR,
            },
        ),
    ],
)
def test_sdlc_action_grants(action: Action, allowed: set[Role]) -> None:
    for role in Role:
        actor, ref = _actor(role)
        assert can(actor, action, ref).allowed is (role in allowed), (action, role)


@pytest.mark.parametrize(
    "action", [Action.SDLC_RUN_START, Action.SDLC_FACTOR_OVERRIDE, Action.SDLC_EXPLAIN]
)
def test_no_agent_or_system_actor_starts_overrides_or_explains(action: Action) -> None:
    for kind in (ActorKind.SYSTEM, ActorKind.AGENT_ROLE):
        actor, ref = _actor(Role.ANALYST, kind)
        assert not can(actor, action, ref).allowed


def test_the_architect_cannot_start_runs_or_override() -> None:
    actor, ref = _actor(Role.ARCHITECT)
    assert can(actor, Action.SDLC_READ, ref).allowed
    for action in (Action.SDLC_RUN_START, Action.SDLC_FACTOR_OVERRIDE, Action.RUN_START):
        assert not can(actor, action, ref).allowed
