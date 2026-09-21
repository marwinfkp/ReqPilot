"""Live P4 smoke test. **BILLABLE**: sends a small number of real requests on the developer's key.

Opt-in only, exactly as ``test_openai_live.py``: the ``llm`` mark is excluded by
``addopts``, and the test skips unless the developer's own configuration selects
``LLM_PROVIDER=openai`` with a key and a model. Run with::

    pytest -m llm tests/llm/test_p4_openai_live.py -s

Only the fictional persona of ``data/dev/personas/`` is sent, in a session
declared ``SYNTHETIC``. A few interview turns (question + assessment), one
extraction over those answers, and one clarification raise/answer. The key is
never printed; the summary shows counts and outcomes only.

A smoke test of the integration, not a quality measurement: it asserts the
invariants (typed outputs or a visible stall, deterministic bounds, no
approval), never that the model phrased something well.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.llm.test_openai_live import live_settings  # noqa: F401 - the shared fixture
from tests.p4_helpers import elicitation_rules, extraction_rules, make_world, persona

from reqpilot.config import Settings
from reqpilot.domain.enums import (
    AgentRole,
    ClarificationStatus,
    DataSensitivity,
    FindingSeverity,
    QualityFindingType,
)
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.runs import AgentRun
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.elicitation_runner import ElicitationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import build_gateway
from reqpilot.services.clarification import QualityFindingService

pytestmark = pytest.mark.llm

#: Interview turns to answer - enough to exercise a follow-up, small enough to be cheap.
TURNS = 4


def test_a_short_live_interview_extraction_and_clarification(
    db_session: Session,
    live_settings: Settings,  # noqa: F811
) -> None:
    world = make_world(db_session)
    gateway = build_gateway(live_settings)
    rules = elicitation_rules()
    runner = ElicitationRunner(db_session, gateway, rules, settings=live_settings)
    data = persona()

    turn = runner.start(
        actor=world.analyst,
        project_id=world.project_id,
        stakeholder_id=world.stakeholder.id,
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    print(f"\n[live] first question on {turn.question.topic_id if turn.question else None}")
    for _ in range(TURNS):
        if turn.stalled or turn.complete or turn.question is None:
            break
        topic = turn.question.topic_id
        answers = data["topics"][topic]["answers"]
        text = answers[min(turn.session.followups_this_topic, len(answers) - 1)]["text"]
        turn = runner.answer(
            actor=world.stakeholder_user,
            project_id=world.project_id,
            session_id=turn.session.id,
            text=text,
        )
        print(
            f"[live] -> {turn.question.topic_id if turn.question else None} "
            f"followup={turn.question.is_followup if turn.question else None} "
            f"stalled={turn.session.stall_reason if turn.stalled else None}"
        )
    # The deterministic bound holds whatever the model said.
    for entry in turn.coverage.entries.values():
        assert entry["followups"] <= rules.max_followups_per_topic
    assert not turn.stalled, f"the live interview stalled: {turn.session.stall_reason}"

    runner.pause(actor=world.analyst, project_id=world.project_id, session_id=turn.session.id)
    summary = AnalysisRunner(
        db_session, gateway, extraction_rules(), settings=live_settings
    ).extract(
        actor=world.analyst,
        project_id=world.project_id,
        session_ids=[turn.session.id],
        domain="LOAN",
    )
    versions = list(db_session.scalars(select(RequirementVersion)))
    print(f"[live] extraction: {summary.status}, {len(versions)} requirement version(s)")
    assert versions, "the live extraction produced no requirement from the answers"

    target = versions[0]
    finding = QualityFindingService(db_session, world.analyst).record(
        project_id=world.project_id,
        version_id=target.id,
        finding_type=QualityFindingType.UNTESTABILITY,
        severity=FindingSeverity.MEDIUM,
        rationale="No measurable acceptance criterion is stated (synthetic check).",
        span_quote=None,
    )
    clarifications = ClarificationRunner(
        db_session, gateway, rules, extraction_rules(), settings=live_settings
    )
    raised = clarifications.raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding.id,
        asked_of=world.stakeholder.id,
    )
    print(f"[live] clarification raised: {raised.clarification is not None} ({raised.error})")
    assert raised.clarification is not None, raised.error
    print(f"[live]   question: {raised.clarification.question}")
    answered = clarifications.answer(
        actor=world.stakeholder_user,
        project_id=world.project_id,
        clarification_id=raised.clarification.id,
        text=data["clarification"]["answer"],
    )
    print(f"[live] re-analysis: {answered.clarification.reanalysis_status} ({answered.error})")
    assert answered.clarification.status is ClarificationStatus.ANSWERED
    assert answered.clarification.reanalysis_status is not None
    # Nothing was approved by any of it.
    assert db_session.scalars(select(ApprovalTask)).first() is None

    runs = [r for r in db_session.scalars(select(AgentRun)) if r.model_version_id]
    by_role: dict[AgentRole, int] = {}
    for run in runs:
        by_role[run.role] = by_role.get(run.role, 0) + 1
    print(f"[live] model calls by role: { {str(k): v for k, v in by_role.items()} }")
    print(f"[live] total model calls: {len(runs)}")
