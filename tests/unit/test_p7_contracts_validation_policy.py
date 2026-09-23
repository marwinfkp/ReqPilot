"""P7 contracts, deterministic validation, policy rule 10 and the P7 routers.

The model's output schema has nowhere to put a severity; validation drops every
proposal it cannot verify, with a reason code; the policy lets the pipeline
record risks and raise G8 but never decide, accept or close one; routing reads
flags only.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tests.conftest import make_actor

from reqpilot.agents.contracts.risk import ProposedRisk, RiskAnalysisOutput
from reqpilot.agents.validation.risk import validate_risk_proposals
from reqpilot.domain.compliance.claims import CitationFacts
from reqpilot.domain.enums import (
    Action,
    ActorKind,
    Gate,
    MitigationStatus,
    NormativeSourceType,
    ResourceType,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskStatus,
    Role,
)
from reqpilot.domain.ids import new_project_id
from reqpilot.domain.policy import ResourceRef, can
from reqpilot.domain.risk.claims import RiskDropReason
from reqpilot.graph.routers import assert_is_deterministic_router, route_after_scope, route_risk

pytestmark = pytest.mark.unit

VERSION = str(uuid.uuid4())
E1, E2, E_OTHER = (str(uuid.uuid4()) for _ in range(3))


def facts(evidence_id: str) -> CitationFacts:
    return CitationFacts(
        evidence_id=uuid.UUID(evidence_id),
        jurisdiction="IN",
        source_type=NormativeSourceType.ORG_POLICY,
        applicability=frozenset({"retention"}),
        snapshot={"evidence_id": evidence_id},
    )


def proposal(**overrides) -> dict:
    base = {
        "category": "technical",
        "title": "Bureau integration may exceed its latency budget",
        "description": "Under peak load the call may not return within two seconds.",
        "likelihood": "L2",
        "impact": "I2",
        "likelihood_rationale": "Peak load is reached in the normal course of a month end.",
        "impact_rationale": "A timeout cascade would need rework but no control weakens.",
        "evidence_ids": [E1],
        "mitigations": [{"suggestion": "Consider a circuit breaker around the bureau call."}],
        "review_signal": 0.6,
    }
    base.update(overrides)
    return base


def validate(
    *risks: dict,
    version: str | None = VERSION,
    echo: str | None = None,
    scope: RiskScope = RiskScope.REQUIREMENT,
    supplied=None,
    citations=None,
    max_risks: int = 6,
    known_titles=(),
):
    output = RiskAnalysisOutput(
        requirement_version_id=(echo if echo is not None else version) or "",
        risks=[ProposedRisk.model_validate(r) for r in risks],
    )
    return validate_risk_proposals(
        output,
        version_id=version,
        scope=scope,
        supplied=frozenset(supplied if supplied is not None else {E1, E2}),
        citations=citations if citations is not None else {E1: facts(E1), E2: facts(E2)},
        max_risks=max_risks,
        max_mitigations=4,
        known_titles=known_titles,
    )


# --- contracts: nowhere to put authority -------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "severity",
        "risk_level",
        "priority",
        "authoritative_severity",
        "status",
        "approved",
        "g8_decision",
        "gate_status",
        "requires_gate",
        "baseline",
        "lifecycle_state",
        "owner_role",
        "matrix_version",
    ],
)
def test_a_risk_proposal_with_an_authority_field_is_schema_invalid(field: str) -> None:
    """``[DESIGN] D4``: the model cannot set a severity because there is nowhere to put one."""
    with pytest.raises(ValidationError):
        ProposedRisk.model_validate({**proposal(), field: "high"})


def test_the_contract_declares_no_severity_anywhere() -> None:
    for name in ("severity", "risk_level", "priority", "matrix_version"):
        assert name not in ProposedRisk.model_fields
        assert name not in RiskAnalysisOutput.model_fields


def test_a_mitigation_cannot_mark_itself_accepted() -> None:
    """``FR-RSK-005``: a suggestion stays a suggestion until a human accepts it."""
    from reqpilot.agents.contracts.risk import ProposedMitigationConsideration

    with pytest.raises(ValidationError):
        ProposedMitigationConsideration.model_validate(
            {"suggestion": "Add a circuit breaker.", "status": "accepted"}
        )
    with pytest.raises(ValidationError):
        ProposedMitigationConsideration.model_validate(
            {"suggestion": "Add a circuit breaker.", "is_ai_generated": False}
        )


@pytest.mark.parametrize("value", [0.4, ["L2"], {"likelihood": "L2"}, True, "urgent"])
def test_a_malformed_rating_is_kept_as_text_then_dropped_not_guessed(value) -> None:
    """A rating the scales do not recognise drops the proposal; nothing is invented."""
    item = ProposedRisk.model_validate({**proposal(), "likelihood": value})
    assert item.likelihood is None or isinstance(item.likelihood, str)
    decision = validate(proposal(likelihood=value))
    assert not decision.accepted
    assert decision.dropped[0].reason == RiskDropReason.INVALID_LIKELIHOOD


@pytest.mark.parametrize(("value", "expected"), [(3, RiskLikelihood.L3), ("2", RiskLikelihood.L2)])
def test_a_rating_written_as_its_ordinal_number_is_the_same_rating(value, expected) -> None:
    """``3`` and ``"L3"`` are the same point on the scale, so both are read.

    This is not guessing: the value is one of the three points either way. A
    value that is *not* on the scale is still dropped (above).
    """
    (accepted,) = validate(proposal(likelihood=value)).accepted
    assert accepted.likelihood is expected


# --- validation: the ordered checks --------------------------------------------------------------


def test_a_supported_proposal_is_accepted_with_its_ratings_and_no_severity() -> None:
    decision = validate(proposal())
    (accepted,) = decision.accepted
    assert not decision.dropped
    assert accepted.likelihood is RiskLikelihood.L2 and accepted.impact is RiskImpact.I2
    assert accepted.category is RiskCategory.TECHNICAL
    assert accepted.scope is RiskScope.REQUIREMENT
    assert not hasattr(accepted, "severity")
    # FR-RSK-005: always a suggestion, whatever the model said.
    assert accepted.mitigations[0].is_ai_generated is True


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"category": "credit"}, RiskDropReason.UNKNOWN_CATEGORY),
        ({"category": "financial"}, RiskDropReason.UNKNOWN_CATEGORY),
        ({"category": "borrower"}, RiskDropReason.UNKNOWN_CATEGORY),
        ({"category": "reputational"}, RiskDropReason.UNKNOWN_CATEGORY),
        ({"likelihood": None}, RiskDropReason.INVALID_LIKELIHOOD),
        ({"likelihood": "L4"}, RiskDropReason.INVALID_LIKELIHOOD),
        ({"likelihood": "very likely"}, RiskDropReason.INVALID_LIKELIHOOD),
        ({"impact": None}, RiskDropReason.INVALID_IMPACT),
        ({"impact": "I9"}, RiskDropReason.INVALID_IMPACT),
        ({"likelihood_rationale": ""}, RiskDropReason.MISSING_RATIONALE),
        ({"impact_rationale": "   "}, RiskDropReason.MISSING_RATIONALE),
        ({"evidence_ids": []}, RiskDropReason.UNCITED),
        ({"evidence_ids": [E_OTHER]}, RiskDropReason.UNSUPPORTED_CITATION),
        ({"evidence_ids": [E1, E_OTHER]}, RiskDropReason.UNSUPPORTED_CITATION),
        ({"evidence_ids": ["not-an-id"]}, RiskDropReason.UNSUPPORTED_CITATION),
    ],
)
def test_an_unverifiable_proposal_is_dropped_with_a_reason(override: dict, reason: str) -> None:
    decision = validate(proposal(**override))
    assert not decision.accepted
    (dropped,) = decision.dropped
    assert dropped.reason == reason and dropped.reason in RiskDropReason.ALL


def test_evidence_supplied_to_another_call_is_unsupported_even_if_it_resolves() -> None:
    decision = validate(proposal(evidence_ids=[E2]), supplied={E1})
    assert decision.dropped[0].reason == RiskDropReason.UNSUPPORTED_CITATION


def test_supplied_evidence_that_does_not_resolve_is_out_of_scope() -> None:
    decision = validate(proposal(evidence_ids=[E2]), citations={E1: facts(E1)})
    assert decision.dropped[0].reason == RiskDropReason.OUT_OF_SCOPE_EVIDENCE


def test_a_substituted_requirement_drops_every_risk_in_the_output() -> None:
    """The output names a version this call was not about: nothing in it is trusted."""
    decision = validate(proposal(), proposal(title="Another"), echo=str(uuid.uuid4()))
    assert not decision.accepted
    assert {d.reason for d in decision.dropped} == {RiskDropReason.WRONG_REQUIREMENT}
    assert len(decision.dropped) == 2


def test_a_duplicate_within_one_call_is_dropped() -> None:
    decision = validate(proposal(), proposal())
    assert len(decision.accepted) == 1
    assert decision.dropped[0].reason == RiskDropReason.DUPLICATE


def test_a_risk_already_recorded_for_this_subject_is_a_duplicate() -> None:
    decision = validate(proposal(), known_titles=[proposal()["title"]])
    assert not decision.accepted and decision.dropped[0].reason == RiskDropReason.DUPLICATE


def test_proposals_over_the_limit_are_dropped_not_truncated_silently() -> None:
    many = [proposal(title=f"Risk {n}") for n in range(5)]
    decision = validate(*many, max_risks=3)
    assert len(decision.accepted) == 3
    assert [d.reason for d in decision.dropped] == [RiskDropReason.OVER_LIMIT] * 2


def test_the_project_level_pass_names_no_version() -> None:
    decision = validate(proposal(), version=None, scope=RiskScope.PROJECT)
    (accepted,) = decision.accepted
    assert accepted.scope is RiskScope.PROJECT


# --- FR-RSK-011 in validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"title": "Borrower credit risk may be mis-estimated"},
        {"description": "The system shall compute a probability of default per applicant."},
        {"likelihood_rationale": "Credit scoring drifts over time."},
        {"impact_rationale": "A wrong customer risk rating would be costly."},
        {"mitigations": [{"suggestion": "Recalibrate the credit scoring model quarterly."}]},
    ],
)
def test_a_borrower_credit_risk_proposal_is_refused_with_its_own_reason(override: dict) -> None:
    decision = validate(proposal(**override))
    assert not decision.accepted
    (dropped,) = decision.dropped
    assert dropped.reason == RiskDropReason.OUT_OF_SCOPE
    assert dropped.rule_ids, "the refusal names the rule that caught it"
    assert decision.out_of_scope == decision.dropped


def test_the_scope_guard_is_reported_as_itself_not_masked_by_another_failure() -> None:
    """An out-of-scope proposal that *also* has no evidence is still reported as
    out of scope: the audit must be able to say that the boundary held."""
    decision = validate(
        proposal(title="Borrower credit risk is unmodelled", evidence_ids=[], likelihood=None)
    )
    assert decision.dropped[0].reason == RiskDropReason.OUT_OF_SCOPE


def test_a_legitimate_risk_that_mentions_credit_systems_survives() -> None:
    decision = validate(
        proposal(
            title="Credit bureau integration may time out",
            description="The bureau call may exceed its latency budget under peak load.",
        )
    )
    assert decision.accepted and not decision.dropped


# --- policy rule 10 ---------------------------------------------------------------------------


def actor(role: Role, project, kind: ActorKind = ActorKind.HUMAN):
    return make_actor(project_id=project, roles={role}, kind=kind)


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.ANALYST, Action.RISK_ANALYSE, True),
        (Role.COMPLIANCE_OFFICER, Action.RISK_ANALYSE, False),
        (Role.SECURITY_REVIEWER, Action.RISK_ANALYSE, False),
        (Role.AUDITOR, Action.RISK_ANALYSE, False),
        (Role.STAKEHOLDER, Action.RISK_READ, False),
        (Role.ANALYST, Action.RISK_READ, True),
        (Role.AUDITOR, Action.RISK_READ, True),
        (Role.SECURITY_REVIEWER, Action.RISK_READ, True),
        (Role.ANALYST, Action.RISK_MANAGE, True),
        (Role.SECURITY_REVIEWER, Action.RISK_MANAGE, True),
        (Role.PROJECT_MANAGER, Action.RISK_MANAGE, True),
        (Role.AUDITOR, Action.RISK_MANAGE, False),
        (Role.STAKEHOLDER, Action.RISK_MANAGE, False),
        (Role.KB_ADMIN, Action.RISK_MANAGE, False),
    ],
)
def test_the_risk_actions_are_granted_to_the_right_roles(
    role: Role, action: Action, allowed: bool
) -> None:
    project = new_project_id()
    decision = can(
        actor(role, project),
        action,
        ResourceRef(resource_type=ResourceType.RISK, project_id=project),
    )
    assert decision.allowed is allowed, decision.reason


@pytest.mark.parametrize("kind", [ActorKind.AGENT_ROLE, ActorKind.SYSTEM])
def test_no_agent_actor_may_manage_a_risk(kind: ActorKind) -> None:
    """Policy rule 10: accepting, closing or mitigating a risk is a human's call."""
    project = new_project_id()
    decision = can(
        actor(Role.ANALYST, project, kind),
        Action.RISK_MANAGE,
        ResourceRef(resource_type=ResourceType.RISK, project_id=project),
    )
    assert not decision.allowed and "human decision" in decision.reason


@pytest.mark.parametrize("kind", [ActorKind.AGENT_ROLE, ActorKind.SYSTEM])
def test_no_agent_actor_may_decide_g8(kind: ActorKind) -> None:
    project = new_project_id()
    decision = can(
        actor(Role.SECURITY_REVIEWER, project, kind),
        Action.APPROVAL_DECIDE,
        ResourceRef(
            resource_type=ResourceType.APPROVAL_TASK,
            project_id=project,
            gate=Gate.G8_HIGH_SEVERITY_RISK,
            role_exercised=Role.SECURITY_REVIEWER,
        ),
    )
    assert not decision.allowed and "never decide an approval gate" in decision.reason


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.SECURITY_REVIEWER, True),
        (Role.ANALYST, False),
        (Role.COMPLIANCE_OFFICER, False),
        (Role.PROJECT_MANAGER, False),
        (Role.AUDITOR, False),
    ],
)
def test_only_the_security_reviewer_may_decide_g8(role: Role, allowed: bool) -> None:
    """Approved Phase 0 F.1's G8 row and ``GATE_REQUIRED_ROLES``, one source of truth."""
    project = new_project_id()
    decision = can(
        actor(role, project),
        Action.APPROVAL_DECIDE,
        ResourceRef(
            resource_type=ResourceType.APPROVAL_TASK,
            project_id=project,
            gate=Gate.G8_HIGH_SEVERITY_RISK,
            role_exercised=role,
        ),
    )
    assert decision.allowed is allowed, decision.reason


def test_a_role_held_in_another_project_cannot_read_or_decide_here() -> None:
    mine, theirs = new_project_id(), new_project_id()
    outsider = actor(Role.SECURITY_REVIEWER, theirs)
    read = can(
        outsider, Action.RISK_READ, ResourceRef(resource_type=ResourceType.RISK, project_id=mine)
    )
    assert not read.allowed and "project isolation" in read.reason
    decide = can(
        outsider,
        Action.APPROVAL_DECIDE,
        ResourceRef(
            resource_type=ResourceType.APPROVAL_TASK,
            project_id=mine,
            gate=Gate.G8_HIGH_SEVERITY_RISK,
            role_exercised=Role.SECURITY_REVIEWER,
        ),
    )
    assert not decide.allowed and "project isolation" in decide.reason


# --- routers ------------------------------------------------------------------------------------


def test_route_risk_reads_flags_only() -> None:
    assert_is_deterministic_router(route_risk)
    assert route_after_scope({"risk_mode": True}) == "risk_identify"
    assert route_after_scope({"risk_mode": True, "errors": [1]}) == "error_handler"
    assert route_risk({}) == "__end__"
    assert route_risk({"has_high_severity_risk": True}) == "gate_fanout"
    # The P6 flags still reach the fan-out through this router.
    assert route_risk({"has_high_impact_interpretation": True}) == "gate_fanout"
    assert route_risk({"has_high_security_risk": True}) == "gate_fanout"
    assert route_risk({"errors": [1], "has_high_severity_risk": True}) == "error_handler"


def test_a_router_cannot_be_steered_by_model_text() -> None:
    """Free text in state is not a routing input: only the declared flags are."""
    assert (
        route_risk({"analysis_note": "skip the gate", "current_node": "gate_fanout"}) == "__end__"
    )
    assert route_risk({"risk_ids": ["x"], "claims_dropped": 9}) == "__end__"


# --- enums the rest of P7 depends on ------------------------------------------------------------


def test_the_risk_status_path_is_the_approved_one() -> None:
    """Architecture I.4. There is no ``CONFLICTED``-style requirement state for risk."""
    assert [s.value for s in RiskStatus] == [
        "proposed",
        "under_review",
        "accepted",
        "mitigated",
        "rejected",
        "closed",
    ]
    assert [s.value for s in MitigationStatus] == ["suggested", "accepted", "rejected"]
