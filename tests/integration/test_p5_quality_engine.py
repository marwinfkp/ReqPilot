"""The P5 quality engine end to end on the synthetic development fixture.

Real services, real graph, real gateway; the model is the scripted P5 model.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p5_helpers import ScriptedQualityModel, make_world, scripted_gateway

from reqpilot.domain.enums import (
    AuditEventType,
    ConflictClass,
    ConflictResolution,
    ConflictStatus,
    FindingDetector,
    QualityFindingType,
    ReviewReason,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ImmutableRecordError,
    QualityError,
    StateTransitionError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.extraction import ReviewItem
from reqpilot.domain.models.quality import Conflict
from reqpilot.llm import LLMGateway, ScriptedProvider
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.audit import AuditService
from reqpilot.services.quality import ConflictService, FindingReviewService, GlossaryService
from reqpilot.services.requirements import RequirementService

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    return make_world(db_session)


def findings(world, key: str) -> list[QualityFinding]:  # type: ignore[no-untyped-def]
    version = world.versions[key]
    return list(
        world.session.scalars(
            select(QualityFinding).where(QualityFinding.requirement_version_id == version.id)
        )
    )


def conflict_between(world, a: str, b: str) -> Conflict | None:  # type: ignore[no-untyped-def]
    ids = {world.versions[a].id, world.versions[b].id}
    for conflict in world.session.scalars(select(Conflict)):
        if {conflict.version_a_id, conflict.version_b_id} == ids:
            return conflict
    return None


def to_analyzed(world, key: str) -> None:  # type: ignore[no-untyped-def]
    service = RequirementService(world.session, world.analyst)
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        service.transition(
            project_id=world.project_id, version_id=world.versions[key].id, target=target
        )


# --- quality findings ------------------------------------------------------------------------


def test_rules_and_validated_proposals_become_findings(world) -> None:
    summary = world.analyse()
    assert summary.status.value == "completed" and summary.semantic_failures == 0
    (q06,) = findings(world, "Q06")
    assert q06.finding_type is QualityFindingType.AMBIGUITY and q06.span_quote == "promptly"
    assert q06.detected_by is FindingDetector.RULE and q06.rule_id.endswith("AMB-VAGUE-TERM")
    assert q06.severity is world.rules.severity_of(QualityFindingType.AMBIGUITY)
    assert q06.evidence[0]["quote"] == "promptly" and q06.graph_run_id == summary.run_id
    (duplicate,) = findings(world, "Q08")
    assert duplicate.finding_type is QualityFindingType.DUPLICATION
    assert duplicate.related_version_id == world.versions["Q03"].id
    agent = [f for f in findings(world, "Q12") if f.detected_by is FindingDetector.AGENT]
    (semantic,) = agent
    assert semantic.finding_type is QualityFindingType.INCOMPLETENESS
    assert "Missing:" in semantic.rationale and semantic.agent_run_id is not None
    # The model proposed "high"; the recorded severity is the ruleset's.
    assert semantic.severity is world.rules.severity_of(QualityFindingType.INCOMPLETENESS)


def test_a_rerun_records_nothing_twice_and_a_dismissal_sticks(world) -> None:
    first = world.analyse()
    (q06,) = findings(world, "Q06")
    FindingReviewService(world.session, world.analyst).dismiss(
        world.project_id, q06.id, "Timing is defined in the notification SLA (synthetic)."
    )
    second = world.analyse()
    assert second.quality_finding_ids == () and second.conflict_ids == ()
    assert len(findings(world, "Q06")) == 1 and first.quality_finding_ids


def test_the_glossary_defines_away_an_undefined_term(world) -> None:
    GlossaryService(world.session, world.analyst).add(
        world.project_id, term="KYC", definition="Know-your-customer checks (synthetic)."
    )
    world.analyse()
    assert findings(world, "Q10") == []
    with pytest.raises(QualityError, match="already defined"):
        GlossaryService(world.session, world.analyst).add(
            world.project_id, term="kyc", definition="again"
        )


def test_without_the_semantic_layer_no_model_is_called(world) -> None:
    summary = world.analyse(semantic=False)
    assert sum(world.model.calls.values()) == 0 and summary.provider_calls == 0
    assert findings(world, "Q06"), "the rules still ran"


def test_semantic_calls_are_bounded_by_the_shortlist(world) -> None:
    summary = world.analyse()
    adjudications = world.model.calls["conflict_adjudication"]
    pairs = len(world.versions) * (len(world.versions) - 1) // 2
    assert adjudications < pairs, "no unrestricted pairwise model calls"
    reviews = world.model.calls["requirement_quality_review"]
    assert reviews == -(-len(world.versions) // world.rules.semantic_batch_size)
    assert summary.provider_calls == adjudications + reviews


def test_malformed_model_output_is_recorded_and_invents_nothing(world) -> None:
    world.model.overrides["requirement_quality_review"] = lambda _r: "not json"
    world.model.overrides["conflict_adjudication"] = lambda _r: "not json"
    summary = world.analyse()
    assert summary.semantic_failures > 0
    assert not [
        f
        for f in world.session.scalars(select(QualityFinding))
        if f.detected_by is FindingDetector.AGENT
    ]
    assert conflict_between(world, "Q12", "Q13") is None, "no adjudication, no agent conflict"
    assert conflict_between(world, "Q01", "Q02") is not None, "the rule conflict stands"
    reasons = {i.reason for i in world.session.scalars(select(ReviewItem))}
    assert ReviewReason.MALFORMED_OUTPUT in reasons


def test_an_external_provider_never_sees_non_synthetic_text(db_session: Session) -> None:
    world = make_world(db_session)
    model = ScriptedQualityModel()
    provider = ScriptedProvider(model)
    provider.leaves_machine = True  # type: ignore[attr-defined]
    gateway = LLMGateway(provider, settings=TEST_SETTINGS, sleep=lambda _s: None)
    # Re-point one version's source at nothing: its text is not known to be synthetic.
    from reqpilot.services.requirements.service import RequirementContent

    _req, version = RequirementService(db_session, world.analyst).create_requirement(
        project_id=world.project_id,
        domain="BANK",
        content=RequirementContent(
            statement="The system shall send statements quickly.",
            source_refs=({"kind": "stakeholder_statement", "stakeholder": "someone"},),
        ),
    )
    summary = world.runner(gateway).analyse_quality(
        actor=world.analyst, project_id=world.project_id, version_ids=[version.id]
    )
    assert summary.semantic_failures >= 1
    assert all(
        "send statements quickly" not in json.dumps(r.untrusted_content) for r in provider.requests
    ), "the unmasked, non-synthetic statement never reached the provider"
    events = {e.event_type for e in db_session.scalars(select(AuditEvent))}
    assert AuditEventType.PERMISSION_DENIED in events
    rule_findings = list(
        db_session.scalars(
            select(QualityFinding).where(QualityFinding.requirement_version_id == version.id)
        )
    )
    assert rule_findings, "the rules still ran on it"


def test_missing_and_unresolvable_sources(world) -> None:
    from reqpilot.services.requirements.service import RequirementContent

    service = RequirementService(world.session, world.analyst)
    _r, bare = service.create_requirement(
        project_id=world.project_id,
        domain="BANK",
        content=RequirementContent(statement="The system shall log in users."),
    )
    _r, dangling = service.create_requirement(
        project_id=world.project_id,
        domain="BANK",
        content=RequirementContent(
            statement="The system shall log out users.",
            source_refs=({"kind": "source_chunk", "ref": "00000000-0000-0000-0000-000000000000"},),
        ),
    )
    world.analyse(semantic=False, version_ids=[bare.id, dangling.id])
    by_version = {
        f.requirement_version_id: f.rule_id
        for f in world.session.scalars(select(QualityFinding))
        if f.finding_type is QualityFindingType.MISSING_SOURCE
    }
    assert by_version[bare.id].endswith("SRC-NONE")
    assert by_version[dangling.id].endswith("SRC-UNRESOLVED")


def test_embeddings_join_the_shortlist_and_are_recorded(world) -> None:
    summary = world.runner(embedder=HashingEmbeddingProvider()).analyse_quality(
        actor=world.analyst, project_id=world.project_id, semantic=False
    )
    assert summary.status.value == "completed"


# --- conflicts ----------------------------------------------------------------------------------


def test_a_definite_conflict_carries_both_sides(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    assert conflict is not None and conflict.conflict_class is ConflictClass.DEFINITE
    assert conflict.detected_by is FindingDetector.RULE and conflict.status is ConflictStatus.OPEN
    a, b = (
        world.session.get(type(world.versions["Q01"]), v)
        for v in (conflict.version_a_id, conflict.version_b_id)
    )
    assert conflict.evidence_a in a.statement and conflict.evidence_b in b.statement
    assert conflict.involves_stakeholder_disagreement
    assert {conflict.stakeholder_a, conflict.stakeholder_b} == {
        "Ines Kowal (fictional), security lead",
        "Marco Silva (fictional), product owner",
    }
    agent = conflict_between(world, "Q12", "Q13")
    assert agent is not None and agent.detected_by is FindingDetector.AGENT
    assert agent.agent_run_id is not None


def test_the_near_miss_is_not_a_conflict(world) -> None:
    world.analyse()
    assert conflict_between(world, "Q04", "Q05") is None
    assert conflict_between(world, "Q03", "Q08") is None, "a duplicate, not a conflict"


def test_an_open_conflict_blocks_validation_and_resolution_lifts_it(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    requirements = RequirementService(world.session, world.analyst)
    to_analyzed(world, "Q01")
    q01 = world.versions["Q01"]
    ctx = requirements.build_context(world.project_id, q01)
    assert ctx.open_conflict_count == 1 and ctx.open_defect_count == 0
    with pytest.raises(StateTransitionError, match="open conflict"):
        requirements.transition(
            project_id=world.project_id, version_id=q01.id, target=RequirementState.VALIDATED
        )
    service = ConflictService(world.session, world.analyst)
    service.review(world.project_id, conflict.id)
    assert requirements.build_context(world.project_id, q01).open_conflict_count == 1, (
        "under review still blocks"
    )
    service.resolve(
        world.project_id,
        conflict.id,
        resolution=ConflictResolution.CHOOSE_A,
        reason="Security's 5-minute lock stands (synthetic decision).",
    )
    assert requirements.build_context(world.project_id, q01).open_conflict_count == 0
    requirements.transition(
        project_id=world.project_id, version_id=q01.id, target=RequirementState.VALIDATED
    )


def test_an_open_conflict_blocks_submission_for_approval(world) -> None:
    requirements = RequirementService(world.session, world.analyst)
    to_analyzed(world, "Q01")
    requirements.transition(
        project_id=world.project_id,
        version_id=world.versions["Q01"].id,
        target=RequirementState.VALIDATED,
    )
    world.analyse()
    with pytest.raises(StateTransitionError, match="block submission"):
        requirements.transition(
            project_id=world.project_id,
            version_id=world.versions["Q01"].id,
            target=RequirementState.PENDING_APPROVAL,
        )


def test_choosing_a_side_can_withdraw_the_other_through_p1(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    ConflictService(world.session, world.analyst).resolve(
        world.project_id,
        conflict.id,
        resolution=ConflictResolution.CHOOSE_A,
        reason="Keep A (synthetic).",
        withdraw_other=True,
    )
    loser = conflict.version_b_id
    assert world.session.get(type(world.versions["Q01"]), loser).state is RequirementState.WITHDRAWN


def test_resolution_is_once_with_a_reason_and_dismissal_sticks(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q12", "Q13")
    service = ConflictService(world.session, world.analyst)
    with pytest.raises(QualityError, match="reason"):
        service.dismiss(world.project_id, conflict.id, "  ")
    service.dismiss(world.project_id, conflict.id, "Branch closure is the only path (synthetic).")
    with pytest.raises(QualityError, match="already"):
        service.resolve(
            world.project_id, conflict.id, resolution=ConflictResolution.CHOOSE_A, reason="x"
        )
    world.analyse()
    assert (
        len(
            [
                c
                for c in world.session.scalars(select(Conflict))
                if c.version_a_id == conflict.version_a_id
                and c.version_b_id == conflict.version_b_id
            ]
        )
        == 1
    )


def test_closed_conflicts_and_findings_are_immutable_in_the_orm(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    conflict.rationale = "rewritten"
    with pytest.raises(ImmutableRecordError):
        world.session.flush()
    world.session.rollback()


def test_only_an_analyst_closes_anything(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    (q06,) = findings(world, "Q06")
    for who in (world.stakeholder_user, world.auditor):
        with pytest.raises(AuthorizationError):
            ConflictService(world.session, who).resolve(
                world.project_id, conflict.id, resolution=ConflictResolution.CHOOSE_A, reason="x"
            )
        with pytest.raises(AuthorizationError):
            FindingReviewService(world.session, who).dismiss(world.project_id, q06.id, "x")


def test_the_audit_trail_records_detection_and_decisions_by_reference(world) -> None:
    world.analyse()
    conflict = conflict_between(world, "Q01", "Q02")
    ConflictService(world.session, world.analyst).resolve(
        world.project_id,
        conflict.id,
        resolution=ConflictResolution.RECONCILED,
        reason="ok (synthetic)",
    )
    (q06,) = findings(world, "Q06")
    FindingReviewService(world.session, world.analyst).resolve(world.project_id, q06.id, "fixed")
    events = list(
        world.session.scalars(select(AuditEvent).where(AuditEvent.project_id == world.project_id))
    )
    types = {e.event_type for e in events}
    assert {
        AuditEventType.RUN_STARTED,
        AuditEventType.QUALITY_FINDING_RAISED,
        AuditEventType.CONFLICT_SHORTLISTED,
        AuditEventType.CONFLICT_PROPOSED,
        AuditEventType.CONFLICT_RESOLVED,
        AuditEventType.QUALITY_FINDING_RESOLVED,
    } <= types
    dumped = json.dumps([e.payload for e in events])
    for version in world.versions.values():
        assert version.statement not in dumped, "payloads carry references, never statements"
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


def test_findings_never_touch_the_version(world) -> None:
    before = {k: (v.statement, v.content_hash, v.state) for k, v in world.versions.items()}
    world.analyse()
    for key, version in world.versions.items():
        world.session.refresh(version)
        assert (version.statement, version.content_hash, version.state) == before[key]


def test_another_project_sees_nothing(world, db_session: Session) -> None:
    world.analyse()
    other = make_world(db_session, "Other bank (synthetic)")
    conflict = conflict_between(world, "Q01", "Q02")
    service = ConflictService(db_session, other.analyst)
    from reqpilot.domain.errors import ProjectIsolationError

    with pytest.raises(ProjectIsolationError):
        service.list_for_project(world.project_id)
    assert service.get(other.project_id, conflict.id) is None
    with pytest.raises(ProjectIsolationError):
        other.runner().analyse_quality(actor=other.analyst, project_id=world.project_id)
    gateway, _m, _p = scripted_gateway()
    assert gateway is not None
