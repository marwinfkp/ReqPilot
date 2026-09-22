"""P5 and the P4 clarification loop (FR-CLR-003's quality half; FR-QAL-007).

A detected finding enters the P4 loop unchanged, and a clarification's
re-analysis now continues into quality analysis for the version it created.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p4_helpers import extraction_rules, make_world, run_persona_interview

from reqpilot.domain.enums import (
    AuditEventType,
    FindingDetector,
    QualityFindingType,
    ReanalysisStatus,
)
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.runner import AnalysisRunner

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    world = make_world(db_session)
    turn = run_persona_interview(world, world.start())
    AnalysisRunner(db_session, world.gateway, extraction_rules(), settings=TEST_SETTINGS).extract(
        actor=world.analyst,
        project_id=world.project_id,
        session_ids=[turn.session.id],
        domain="LOAN",
    )
    return world


def test_a_detected_finding_enters_the_clarification_loop_and_re_analysis_is_quality_checked(
    world,
) -> None:
    runner = AnalysisRunner(
        world.session, world.gateway, extraction_rules(), settings=TEST_SETTINGS
    )
    summary = runner.analyse_quality(actor=world.analyst, project_id=world.project_id)
    assert summary.status.value == "completed"
    target = next(
        v
        for v in world.session.scalars(select(RequirementVersion))
        if "decision letter quickly" in v.statement
    )
    detected = next(
        f
        for f in world.session.scalars(
            select(QualityFinding).where(QualityFinding.requirement_version_id == target.id)
        )
        if f.finding_type is QualityFindingType.AMBIGUITY
    )
    assert detected.detected_by is FindingDetector.RULE and detected.span_quote == "quickly"

    clarifications = ClarificationRunner(
        world.session, world.gateway, world.rules, extraction_rules(), settings=TEST_SETTINGS
    )
    raised = clarifications.raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=detected.id,
        asked_of=world.stakeholder.id,
    )
    assert raised.clarification is not None, raised.error
    answered = clarifications.answer(
        actor=world.stakeholder_user,
        project_id=world.project_id,
        clarification_id=raised.clarification.id,
        text=world.model.data["clarification"]["answer"],
    )
    assert answered.clarification.reanalysis_status is ReanalysisStatus.NEW_VERSION
    requirement = world.session.get(Requirement, target.requirement_id)
    successor = world.session.get(RequirementVersion, requirement.current_version_id)
    assert successor.id != target.id

    # The re-analysis run continued into the P5 nodes for the new version.
    run_id = answered.reanalysis.run_id
    nodes = [
        e.payload.get("node")
        for e in world.session.scalars(
            select(AuditEvent).where(
                AuditEvent.graph_run_id == run_id,
                AuditEvent.event_type == AuditEventType.NODE_STARTED,
            )
        )
    ]
    assert "quality_analysis" in nodes and "conflict_shortlist" in nodes
    # The clarified statement names a time, so "quickly" no longer applies.
    assert not [
        f
        for f in world.session.scalars(
            select(QualityFinding).where(QualityFinding.requirement_version_id == successor.id)
        )
        if f.finding_type is QualityFindingType.AMBIGUITY and f.span_quote == "quickly"
    ]
    # The predecessor's finding is about the predecessor; it is not rewritten.
    world.session.refresh(detected)
    assert detected.requirement_version_id == target.id
    assert json.dumps(detected.evidence)
