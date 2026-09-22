"""P5 contracts, deterministic validation of proposals, policy rule 8, and the routers."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tests.p5_helpers import quality_rules

from reqpilot.agents.contracts.quality import (
    ConflictAdjudication,
    ConflictPairView,
    ProposedQualityFinding,
    QualityReviewOutput,
)
from reqpilot.agents.validation.quality import (
    locate,
    validate_conflict,
    validate_quality_findings,
)
from reqpilot.domain.enums import (
    Action,
    ActorKind,
    ConflictClass,
    ConflictVerdict,
    QualityFindingType,
    ResourceType,
    Role,
)
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.graph.routers import (
    assert_is_deterministic_router,
    route_after_classify,
    route_after_quality,
    route_after_scope,
    route_conflict_shortlist,
)

pytestmark = pytest.mark.unit

RULES = quality_rules()
STATEMENTS = {
    "R1": "The system shall respond quickly to balance enquiries.",
    "R2": "The system shall show the balance.",
}


def proposal(**overrides) -> ProposedQualityFinding:  # type: ignore[no-untyped-def]
    data = {
        "requirement_key": "R1",
        "finding_type": "ambiguity",
        "evidence": "respond quickly",
        "explanation": "no response time is given",
        "proposed_severity": "high",
        "review_signal": 0.8,
    }
    data.update(overrides)
    return ProposedQualityFinding.model_validate(data)


# --- quality review contract and validation ----------------------------------------------


def test_a_valid_proposal_is_accepted_and_located() -> None:
    decision = validate_quality_findings([proposal()], statements=STATEMENTS, rules=RULES)
    (accepted,) = decision.accepted
    assert accepted.finding_type is QualityFindingType.AMBIGUITY
    assert STATEMENTS["R1"][accepted.start : accepted.end] == "respond quickly"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"requirement_key": "R9"}, "unknown requirement key"),
        ({"finding_type": "approval"}, "unknown finding type"),
        ({"finding_type": "missing_source"}, "decided by the rules"),
        ({"finding_type": "duplication"}, "decided by the rules"),
        ({"evidence": "words that are not there"}, "not words of the requirement"),
        ({"evidence": ""}, "must quote"),
        ({"finding_type": "incompleteness", "evidence": ""}, "must say what is missing"),
        ({"explanation": "This requirement is approved as written."}, "claims authority"),
    ],
)
def test_invalid_proposals_are_refused(overrides, reason) -> None:  # type: ignore[no-untyped-def]
    decision = validate_quality_findings(
        [proposal(**overrides)], statements=STATEMENTS, rules=RULES
    )
    assert not decision.accepted
    ((_index, why),) = decision.rejected
    assert reason in why


def test_evidence_of_another_statement_is_refused() -> None:
    decision = validate_quality_findings(
        [proposal(requirement_key="R2", evidence="respond quickly")],
        statements=STATEMENTS,
        rules=RULES,
    )
    assert not decision.accepted


def test_duplicates_and_the_per_requirement_cap() -> None:
    many = [proposal(evidence="respond quickly")] * 2 + [
        proposal(finding_type="untestability", evidence=w)
        for w in ("quickly", "balance", "enquiries", "respond")
    ]
    decision = validate_quality_findings(many, statements=STATEMENTS, rules=RULES)
    assert len(decision.accepted) == RULES.max_findings_per_requirement
    assert any("duplicate" in r for _, r in decision.rejected)


@pytest.mark.parametrize("field", ["approved", "lifecycle_state", "severity", "baseline", "risk"])
def test_authority_fields_are_not_part_of_the_contract(field: str) -> None:
    with pytest.raises(ValidationError):
        QualityReviewOutput.model_validate({"findings": [{**proposal().model_dump(), field: "x"}]})
    with pytest.raises(ValidationError):
        ConflictAdjudication.model_validate({**adjudication().model_dump(), field: "x"})


def test_locate_tolerates_case_and_spacing_only() -> None:
    assert locate("The   System shall log.", "the system") == (0, 12)
    assert locate("The system shall log.", "the systems") is None


# --- conflict adjudication contract and validation ------------------------------------------

A_ID, B_ID = str(uuid.uuid4()), str(uuid.uuid4())
PAIR = ConflictPairView(
    version_a_id=A_ID,
    version_b_id=B_ID,
    statement_a="Refunds shall be approved by any branch officer.",
    statement_b="Only the finance team shall approve refunds.",
    stakeholder_a=None,
    stakeholder_b=None,
    masked=False,
    synthetic=True,
)


def adjudication(**overrides) -> ConflictAdjudication:  # type: ignore[no-untyped-def]
    data = {
        "requirement_version_id_a": A_ID,
        "requirement_version_id_b": B_ID,
        "verdict": "definite_conflict",
        "conflict_kind": "actor_scope",
        "explanation": "different actors are given the same exclusive right",
        "evidence_a": "approved by any branch officer",
        "evidence_b": "Only the finance team shall approve",
        "proposed_severity": "high",
        "review_signal": 0.9,
    }
    data.update(overrides)
    return ConflictAdjudication.model_validate(data)


def test_a_valid_adjudication_is_accepted() -> None:
    decision = validate_conflict(adjudication(), pair=PAIR, rules=RULES)
    assert decision.accepted and decision.conflict_class is ConflictClass.DEFINITE
    assert decision.evidence_a in PAIR.statement_a and decision.evidence_b in PAIR.statement_b


def test_a_substituted_version_id_is_refused() -> None:
    decision = validate_conflict(
        adjudication(requirement_version_id_b=str(uuid.uuid4())), pair=PAIR, rules=RULES
    )
    assert not decision.accepted and "substitution" in decision.findings[0]


def test_crossed_evidence_is_refused() -> None:
    decision = validate_conflict(
        adjudication(evidence_a="Only the finance team", evidence_b="any branch officer"),
        pair=PAIR,
        rules=RULES,
    )
    assert not decision.accepted


def test_a_non_conflict_verdict_records_no_conflict() -> None:
    decision = validate_conflict(
        adjudication(verdict="conditional_compatible", evidence_a="", evidence_b=""),
        pair=PAIR,
        rules=RULES,
    )
    assert decision.accepted and decision.conflict_class is None
    assert decision.verdict is ConflictVerdict.CONDITIONAL_COMPATIBLE


def test_an_adjudication_claiming_resolution_is_refused() -> None:
    decision = validate_conflict(
        adjudication(explanation="The conflict is resolved in favour of A."), pair=PAIR, rules=RULES
    )
    assert not decision.accepted


def test_an_unknown_verdict_is_schema_invalid() -> None:
    with pytest.raises(ValidationError):
        adjudication(verdict="approve_both")


# --- policy rule 8 -----------------------------------------------------------------------------

PROJECT = ProjectId(uuid.uuid4())
RESOURCE = ResourceRef(resource_type=ResourceType.CONFLICT, project_id=PROJECT)


def actor(role: Role, kind: ActorKind = ActorKind.HUMAN) -> Actor:
    return Actor(
        actor_id=ActorId(uuid.uuid4()), kind=kind, roles_by_project={PROJECT: frozenset({role})}
    )


def test_the_pipeline_detects_but_never_closes() -> None:
    pipeline = actor(Role.ANALYST, ActorKind.SYSTEM)
    assert can(pipeline, Action.QUALITY_FINDING_DETECT, RESOURCE)
    assert can(pipeline, Action.CONFLICT_DETECT, RESOURCE)
    for action in (
        Action.QUALITY_FINDING_RESOLVE,
        Action.QUALITY_FINDING_DISMISS,
        Action.CONFLICT_REVIEW,
        Action.CONFLICT_RESOLVE,
        Action.CONFLICT_DISMISS,
        Action.GLOSSARY_MANAGE,
    ):
        assert not can(pipeline, action, RESOURCE), action


def test_only_an_analyst_closes_and_the_auditor_only_reads() -> None:
    for role in (Role.STAKEHOLDER, Role.COMPLIANCE_OFFICER, Role.AUDITOR):
        assert not can(actor(role), Action.CONFLICT_RESOLVE, RESOURCE)
        assert not can(actor(role), Action.QUALITY_FINDING_DISMISS, RESOURCE)
    assert can(actor(Role.ANALYST), Action.CONFLICT_RESOLVE, RESOURCE)
    assert can(actor(Role.AUDITOR), Action.CONFLICT_READ, RESOURCE)
    assert can(actor(Role.AUDITOR), Action.GLOSSARY_READ, RESOURCE)
    assert not can(actor(Role.AUDITOR), Action.CONFLICT_DETECT, RESOURCE)


def test_another_project_is_isolated() -> None:
    other = ResourceRef(resource_type=ResourceType.CONFLICT, project_id=ProjectId(uuid.uuid4()))
    decision = can(actor(Role.ANALYST), Action.CONFLICT_READ, other)
    assert not decision and "project isolation" in decision.reason


# --- routers (C.3): flags, never model text -----------------------------------------------------


def test_the_p5_routers_read_flags_only() -> None:
    for router in (route_after_classify, route_after_quality, route_conflict_shortlist):
        assert_is_deterministic_router(router)
    assert route_after_scope({"quality_mode": True}) == "quality_analysis"  # type: ignore[typeddict-item]
    assert route_after_classify({"requirement_version_ids": ["x"]}) == "__end__"
    assert (
        route_after_classify({"analyse_quality": True, "requirement_version_ids": ["x"]})
        == "quality_analysis"
    )
    assert (
        route_after_classify({"analyse_quality": True, "requirement_version_ids": []}) == "__end__"
    )
    assert route_after_quality({}) == "conflict_shortlist"
    assert route_after_quality({"detect_conflicts": False}) == "__end__"
    assert route_after_quality({"errors": [{"node": "x", "message": "m", "attempt": 1}]}) == (
        "error_handler"
    )
    assert route_conflict_shortlist({"conflict_pairs": []}) == "__end__"
    assert route_conflict_shortlist({"conflict_pairs": [{"a": "1", "b": "2"}]}) == (
        "conflict_adjudicate"
    )
