"""The P5 end-to-end exit test (P5 brief §29; roadmap P5 "Quality + conflict detection").

One run over the synthetic development fixture (``data/dev/quality``) through the
real ``analysis_graph`` quality path, the real gateway (scripted model), the P1
lifecycle guards and the P4 clarification loop. Steps are numbered as in the
brief. The frozen benchmark (E2/E3) is exercised by its own test and script.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p4_helpers import elicitation_rules, extraction_rules
from tests.p5_helpers import make_world

from reqpilot.domain.enums import (
    AuditEventType,
    ClarificationStatus,
    ConflictClass,
    ConflictResolution,
    ConflictStatus,
    FindingDetector,
    QualityFindingType,
    StakeholderAuthority,
)
from reqpilot.domain.errors import ProjectIsolationError, StateTransitionError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.services.audit import AuditService
from reqpilot.services.elicitation import StakeholderService
from reqpilot.services.quality import ConflictService, FindingReviewService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import assert_version_unmodified

pytestmark = pytest.mark.workflow


def test_p5_exit_quality_and_conflict_detection(db_session: Session) -> None:
    # 1-2. A seeded project with multiple requirements (13, synthetic).
    world = make_world(db_session)
    q01, q02 = world.versions["Q01"], world.versions["Q02"]
    snapshot = {k: (v.statement, v.content_hash) for k, v in world.versions.items()}

    # 3-4. The fixture holds a definite conflict (Q01/Q02) and a near-miss (Q04/Q05).
    # 5. Quality analysis and conflict detection run.
    summary = world.analyse()
    assert summary.status.value == "completed" and not summary.errors

    # 6-7. The intended conflict is detected and persisted.
    conflicts = list(db_session.scalars(select(Conflict)))
    by_pair = {frozenset({c.version_a_id, c.version_b_id}): c for c in conflicts}
    conflict = by_pair[frozenset({q01.id, q02.id})]
    assert conflict.conflict_class is ConflictClass.DEFINITE
    assert conflict.status is ConflictStatus.OPEN
    # ...and the near-miss is not.
    near = frozenset({world.versions["Q04"].id, world.versions["Q05"].id})
    assert near not in by_pair
    # Quality findings of several kinds were recorded, by rules and by the model.
    detected = {
        f.finding_type
        for f in FindingReviewService(db_session, world.analyst).list_for_project(world.project_id)
    }
    assert {
        QualityFindingType.AMBIGUITY,
        QualityFindingType.INCOMPLETENESS,
        QualityFindingType.DUPLICATION,
        QualityFindingType.UNDEFINED_TERM,
        QualityFindingType.MISSING_SECURITY_CONSIDERATION,
    } <= detected

    # 8. Source and version references: exact versions, evidence from each side,
    # both stakeholders named (FR-CNF-002), the run that found it.
    a = db_session.get(RequirementVersion, conflict.version_a_id)
    b = db_session.get(RequirementVersion, conflict.version_b_id)
    assert {a.id, b.id} == {q01.id, q02.id} and a.project_id == b.project_id == world.project_id
    assert conflict.evidence_a in a.statement and conflict.evidence_b in b.statement
    assert conflict.involves_stakeholder_disagreement and conflict.graph_run_id == summary.run_id
    assert conflict.stakeholder_a and conflict.stakeholder_b

    # 9. The open conflict blocks ANALYZED -> VALIDATED.
    requirements = RequirementService(db_session, world.analyst)
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        requirements.transition(project_id=world.project_id, version_id=q01.id, target=target)
    with pytest.raises(StateTransitionError, match="open conflict"):
        requirements.transition(
            project_id=world.project_id, version_id=q01.id, target=RequirementState.VALIDATED
        )

    # 14. The clarification path: the conflict enters the P4 loop on Q02's side
    # (the P4 raise paths start at CLASSIFIED).
    for target in (RequirementState.EXTRACTED, RequirementState.CLASSIFIED):
        requirements.transition(project_id=world.project_id, version_id=q02.id, target=target)
    stakeholder = StakeholderService(db_session, world.analyst, elicitation_rules()).create(
        project_id=world.project_id,
        name="Marco Silva (fictional)",
        stakeholder_role="product_owner",
        authority_level=StakeholderAuthority.DECISION_MAKER,
    )
    service = ConflictService(db_session, world.analyst)
    finding = service.clarification_finding(
        world.project_id, conflict.id, side="a" if a.id == q02.id else "b"
    )
    assert finding.finding_type is QualityFindingType.INCONSISTENCY
    assert finding.requirement_version_id == q02.id and finding.related_version_id == q01.id
    world.model.overrides["clarification_question"] = lambda r: json.dumps(
        {
            "question": "How many minutes without interaction should pass before the app locks?",
            "expected_answer_shape": "a number of minutes",
            "defect_id": r.instructions.split(" id ")[1][:36],
        }
    )
    raised = ClarificationRunner(
        db_session, world.gateway, elicitation_rules(), extraction_rules(), settings=TEST_SETTINGS
    ).raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding.id,
        asked_of=stakeholder.id,
    )
    assert raised.clarification is not None, raised.error
    assert raised.clarification.status is ClarificationStatus.OPEN

    # 10. An authorised human resolves the conflict (the G4 decision).
    service.review(world.project_id, conflict.id)
    service.resolve(
        world.project_id,
        conflict.id,
        resolution=ConflictResolution.CHOOSE_A if a.id == q01.id else ConflictResolution.CHOOSE_B,
        reason="The 5-minute lock stands; the product owner agreed (synthetic).",
    )
    db_session.refresh(conflict)
    assert (
        conflict.status is ConflictStatus.RESOLVED
        and conflict.resolved_by == world.analyst.actor_id
    )

    # 11. The guard lifts: Q01 has no open finding and no open conflict now.
    ctx = requirements.build_context(world.project_id, q01)
    assert ctx.open_conflict_count == 0 and ctx.open_defect_count == 0
    requirements.transition(
        project_id=world.project_id, version_id=q01.id, target=RequirementState.VALIDATED
    )
    assert q01.state is RequirementState.VALIDATED

    # 12. The audit trail records it all, by reference, and the chain verifies.
    events = list(
        db_session.scalars(select(AuditEvent).where(AuditEvent.project_id == world.project_id))
    )
    types = {e.event_type for e in events}
    assert {
        AuditEventType.RUN_STARTED,
        AuditEventType.QUALITY_FINDING_RAISED,
        AuditEventType.CONFLICT_SHORTLISTED,
        AuditEventType.CONFLICT_PROPOSED,
        AuditEventType.CONFLICT_REVIEWED,
        AuditEventType.CONFLICT_RESOLVED,
        AuditEventType.CLARIFICATION_RAISED,
    } <= types
    assert AuditService(db_session).verify_project_chain(world.project_id) == (True, None)

    # 13. No requirement version was mutated; nothing was approved or baselined.
    for key, version in world.versions.items():
        db_session.refresh(version)
        assert (version.statement, version.content_hash) == snapshot[key]
        assert_version_unmodified(version)
    assert db_session.scalars(select(ApprovalTask)).first() is None
    assert all(
        v.state not in (RequirementState.APPROVED, RequirementState.BASELINED)
        for v in world.versions.values()
    )
    # The model's conflict was recorded as a proposal of the agent, nothing more.
    agent = next(c for c in conflicts if c.detected_by is FindingDetector.AGENT)
    assert agent.status is ConflictStatus.OPEN

    # Isolation: another project cannot see or touch any of it.
    other = make_world(db_session, "Other bank (synthetic)")
    with pytest.raises(ProjectIsolationError):
        ConflictService(db_session, other.analyst).list_for_project(world.project_id)
