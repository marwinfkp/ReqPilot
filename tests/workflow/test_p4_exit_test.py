"""The P4 roadmap exit test (approved Phase 0 §P, roadmap row P4):

    "A scripted persona interview yields a usable requirement set; follow-ups
     trigger on seeded vague answers; answering a clarification creates a new
     version."

One end-to-end run with the synthetic persona (``data/dev/personas``) through
the real ``elicitation_graph`` (interrupt/resume on a checkpointer), the real P3
``analysis_graph`` over the interview's utterances, and the clarification loop.
Only the model is scripted. The steps are lettered as in the P4 brief (A-R).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p4_helpers import extraction_rules, make_world, persona, run_persona_interview

from reqpilot.domain.enums import (
    AuditEventType,
    ClarificationStatus,
    FindingSeverity,
    GraphRunStatus,
    QualityFindingType,
    ReanalysisStatus,
    SpeakerKind,
    TopicStatus,
)
from reqpilot.domain.errors import ProjectIsolationError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.elicitation import Utterance
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.audit import AuditService
from reqpilot.services.clarification import QualityFindingService
from reqpilot.services.elicitation import InterviewSessionService
from reqpilot.services.requirements.service import assert_version_unmodified

pytestmark = pytest.mark.workflow


def test_p4_exit_scripted_persona_interview_to_clarified_new_version(db_session: Session) -> None:
    world = make_world(db_session)
    data = persona()

    # A. The scripted persona interview starts. B. The first question is generated.
    turn = world.start()
    assert turn.question is not None and turn.question.topic_id == "business_objectives"
    session_id = turn.session.id

    # C-I. Answers are recorded, seeded vague/incomplete answers trigger targeted,
    # bounded follow-ups, and the interview continues to complete coverage.
    followup_seen: dict[str, list[bool]] = {}
    for _ in range(40):
        if turn.complete:
            break
        assert turn.question is not None
        topic = turn.question.topic_id
        followup_seen.setdefault(topic, []).append(turn.question.is_followup)
        counter_before = turn.session.followups_this_topic
        turn = world.answer(session_id, world.persona_answer(turn))
        if turn.question is not None and turn.question.topic_id == topic:
            # E/F. A follow-up on the same topic, with the counter incremented.
            assert turn.question.is_followup
            assert turn.session.followups_this_topic == counter_before + 1
    assert turn.complete, "the interview reached complete coverage"

    # D/E. The seeded incomplete ("inputs") and vague ("performance") answers each
    # earned exactly one follow-up; the topic was then covered.
    assert followup_seen["inputs"] == [False, True]
    assert followup_seen["performance"] == [False, True]
    # G. The bound: "business_rules" stayed vague - 1 question + max follow-ups,
    # then the topic closed UNRESOLVED instead of looping.
    assert followup_seen["business_rules"] == [False] + [True] * world.rules.max_followups_per_topic
    entries = turn.coverage.entries
    assert entries["business_rules"]["status"] == TopicStatus.UNRESOLVED
    assert entries["inputs"]["status"] == TopicStatus.COVERED
    # I. Coverage is complete and every applicable topic was addressed.
    assert turn.coverage.summary.remaining == ()
    utterances = list(
        db_session.scalars(
            select(Utterance).where(Utterance.session_id == session_id).order_by(Utterance.seq)
        )
    )
    answers = [u for u in utterances if u.speaker_kind is SpeakerKind.STAKEHOLDER]
    assert all(a.replies_to_id is not None and not a.on_behalf for a in answers)
    assert [u.seq for u in utterances] == list(range(1, len(utterances) + 1))

    # J. A usable requirement set, through the existing P3 path.
    summary = AnalysisRunner(
        db_session, world.gateway, extraction_rules(), settings=TEST_SETTINGS
    ).extract(
        actor=world.analyst, project_id=world.project_id, session_ids=[session_id], domain="LOAN"
    )
    assert summary.status is GraphRunStatus.COMPLETED
    assert summary.accepted == len(data["extraction"])
    versions = {
        v.statement: v
        for v in db_session.scalars(
            select(RequirementVersion).where(RequirementVersion.project_id == world.project_id)
        )
    }
    assert all(v.state is RequirementState.CLASSIFIED for v in versions.values())
    for version in versions.values():
        (ref,) = version.source_refs
        assert ref["kind"] == "utterance" and ref["session"] == str(session_id)
        cited = db_session.get(Utterance, uuid.UUID(ref["ref"]))
        assert cited.speaker_kind is SpeakerKind.STAKEHOLDER, "requirements cite stakeholders"
        assert cited.text[ref["span"][0] : ref["span"][1]] == ref["quote"]
    # The injected line in the persona's answers produced no requirement.
    assert not any("approve" in s.lower() for s in versions)

    # K. A seeded quality finding, and a targeted clarification against it.
    clarified = data["clarification"]
    target = next(v for s, v in versions.items() if "decision letter quickly" in s)
    predecessor_hash = target.content_hash
    predecessor_statement = target.statement
    finding = QualityFindingService(db_session, world.analyst).record(
        project_id=world.project_id,
        version_id=target.id,
        finding_type=QualityFindingType.AMBIGUITY,
        severity=FindingSeverity.MEDIUM,
        rationale=clarified["finding"]["rationale"],
        span_quote=clarified["finding"]["span"],
    )
    runner = ClarificationRunner(
        db_session, world.gateway, world.rules, extraction_rules(), settings=TEST_SETTINGS
    )
    raised = runner.raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding.id,
        asked_of=world.stakeholder.id,
    )
    assert raised.clarification is not None, raised.error
    clarification = raised.clarification
    assert clarification.question == clarified["question"]
    assert clarification.requirement_version_id == target.id
    assert clarification.quality_finding_id == finding.id
    assert target.state is RequirementState.CLARIFICATION_REQUIRED

    # L. The stakeholder answers. M. The requirement gets a new immutable version.
    outcome = runner.answer(
        actor=world.stakeholder_user,
        project_id=world.project_id,
        clarification_id=clarification.id,
        text=clarified["answer"],
    )
    assert outcome.error is None
    assert clarification.status is ClarificationStatus.ANSWERED
    assert clarification.reanalysis_status is ReanalysisStatus.NEW_VERSION
    requirement = db_session.get(Requirement, target.requirement_id)
    successor = db_session.get(RequirementVersion, requirement.current_version_id)
    assert successor.id != target.id and successor.id == clarification.resulting_version_id
    assert successor.version_no == target.version_no + 1
    assert successor.statement == clarified["revised"]["statement"]

    # N. The predecessor is unchanged: same content, same hash, history kept.
    db_session.refresh(target)
    assert target.statement == predecessor_statement
    assert target.content_hash == predecessor_hash
    assert_version_unmodified(target)  # the stored hash still matches the content
    assert target.state is RequirementState.CLARIFIED

    # O. Not approved, not baselined: it starts its own lifecycle, classified again.
    assert successor.state is RequirementState.CLASSIFIED
    assert db_session.scalars(select(ApprovalTask)).first() is None
    assert requirement.baselined_version_id is None

    # P. Provenance: the original stakeholder source, plus the clarification answer.
    kinds = {(r["kind"], r["ref"]) for r in successor.source_refs}
    assert ("utterance", target.source_refs[0]["ref"]) in kinds
    assert ("utterance", str(clarification.answer_utterance_id)) in kinds
    assert "clarification" in successor.change_reason

    # Q. The audit trail records the whole loop, and its chain verifies.
    events = {
        e.event_type
        for e in db_session.scalars(
            select(AuditEvent).where(AuditEvent.project_id == world.project_id)
        )
    }
    assert {
        AuditEventType.QUALITY_FINDING_RAISED,
        AuditEventType.CLARIFICATION_RAISED,
        AuditEventType.CLARIFICATION_ANSWERED,
        AuditEventType.CLARIFICATION_REANALYSED,
        AuditEventType.REQUIREMENT_VERSION_CREATED,
    } <= events
    assert AuditService(db_session).verify_project_chain(world.project_id) == (True, None)

    # R. No cross-project access: another project's stakeholder cannot see it.
    other = make_world(db_session, "Another project (synthetic)")
    service = InterviewSessionService(db_session, other.stakeholder_user, other.rules)
    with pytest.raises(ProjectIsolationError):
        service.require(world.project_id, session_id)
    with pytest.raises(ProjectIsolationError):
        runner.answer(
            actor=other.stakeholder_user,
            project_id=world.project_id,
            clarification_id=clarification.id,
            text="injected from another project",
        )


def test_the_follow_up_is_targeted_to_the_assessed_issue(db_session: Session) -> None:
    """F. The follow-up request carries the issue the assessment found, as data."""
    world = make_world(db_session)
    run_persona_interview(world, world.start())
    followups = [
        r
        for r in world.provider.requests
        if r.prompt_template_id.startswith("stakeholder_interview_question")
        and "exactly one follow-up question" in r.instructions
    ]
    assert followups, "follow-up questions were requested"
    issues = [r.untrusted_content.get("followup_issue", "") for r in followups]
    assert any("which documents are required" in i for i in issues)
    assert any("no measurable response time" in i for i in issues)
    for request in followups:
        assert "class=model_output" in request.untrusted_content["followup_issue"]
