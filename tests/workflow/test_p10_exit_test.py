"""The P10 end-to-end exit test (roadmap P10 "Workflow generation").

The approved exit criterion (``docs/01-analysis.md`` §P):

    A generated workflow for a high-regulation project contains every mandatory
    compliance checkpoint derived from that project's own mapping and every
    high-risk mitigation from its register.

The high-regulation synthetic loan-origination project (``tests/p10_helpers.py``)
goes through the real P3-P9 pathways - extraction, analysis, G2/G3/G8, G4/G5, G1,
the SDLC recommendation and G6 by all four roles - and then asks for its workflow.

**The expectation is computed here, from the database, by queries written in this
file** - not by the derivation or validation code under test - so a defect in the
generator cannot also hide itself:

* the mandatory checkpoints: every compliance mapping of an approved-baseline
  requirement version that validation accepted or the Compliance Officer approved
  at G2 (the P6 semantics: a rejected or pending interpretation is not active);
* the high-risk mitigations: every recorded, not-rejected mitigation of every HIGH
  risk of the register that is in scope (a requirement-level risk of a baselined
  version, or a project-level risk) and governed (not rejected, not closed).

Each must be present in the persisted workflow *and* traceable: a
``workflow_source`` row naming the record, and a trace edge (N.2 #25 / #26) from the
record to an element that exists. A negative control shows the same check fails
when one element is missing. Nothing reaches a network; the workflow itself is
generated without a model call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p10_helpers import P10World, make_p10_world

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    Gate,
    GraphRunStatus,
    Role,
    WorkflowStatus,
)
from reqpilot.domain.errors import AuthorizationError, ProjectIsolationError
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.baseline import BaselineMember
from reqpilot.domain.models.compliance import ComplianceMapping
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.models.sdlc import SdlcFactor
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowGate,
    WorkflowPhase,
    WorkflowSource,
)

pytestmark = pytest.mark.workflow


# --- the expectation, from the database ----------------------------------------------------


@dataclass(frozen=True)
class Expected:
    mappings: frozenset[uuid.UUID]
    high_risks: frozenset[uuid.UUID]
    mitigations: frozenset[uuid.UUID]


def expected_from_the_records(
    session: Session, project_id: uuid.UUID, baseline_id: uuid.UUID
) -> Expected:
    members = set(
        session.scalars(
            select(BaselineMember.requirement_version_id).where(
                BaselineMember.baseline_id == baseline_id
            )
        )
    )
    mappings = frozenset(
        m.id
        for m in session.scalars(
            select(ComplianceMapping).where(ComplianceMapping.project_id == project_id)
        )
        if m.requirement_version_id in members and str(m.status) in ("candidate", "approved")
    )
    high = [
        r
        for r in session.scalars(select(Risk).where(Risk.project_id == project_id))
        if str(r.severity) == "high"
        and str(r.status) not in ("rejected", "closed")
        and (r.requirement_version_id is None or r.requirement_version_id in members)
    ]
    high_ids = frozenset(r.id for r in high)
    mitigations = frozenset(
        m.id
        for m in session.scalars(
            select(RiskMitigation).where(RiskMitigation.project_id == project_id)
        )
        if m.risk_id in high_ids and str(m.status) != "rejected"
    )
    return Expected(mappings, high_ids, mitigations)


@dataclass(frozen=True)
class Link:
    source_type: str
    source_id: uuid.UUID
    element_kind: str  # the gate or activity kind the source points at
    relation: str


def workflow_links(session: Session, workflow_id: uuid.UUID) -> set[Link]:
    kinds: dict[uuid.UUID, str] = {}
    for g in session.scalars(select(WorkflowGate).where(WorkflowGate.workflow_id == workflow_id)):
        kinds[g.id] = g.kind
    for a in session.scalars(
        select(WorkflowActivity).where(WorkflowActivity.workflow_id == workflow_id)
    ):
        kinds[a.id] = a.kind
    return {
        Link(s.source_type, s.source_id, kinds.get(s.element_id, "missing-element"), s.relation)
        for s in session.scalars(
            select(WorkflowSource).where(WorkflowSource.workflow_id == workflow_id)
        )
    }


def exit_criterion_violations(expected: Expected, links: set[Link]) -> list[str]:
    """The approved exit criterion, checked against the recorded provenance."""
    missing: list[str] = []
    for mapping in expected.mappings:
        if (
            Link("compliance_mapping", mapping, "compliance_checkpoint", "checkpoint_for")
            not in links
        ):
            missing.append(f"no compliance checkpoint for mapping {mapping}")
    for mitigation in expected.mitigations:
        if Link("risk_mitigation", mitigation, "risk_treatment", "implements") not in links:
            missing.append(f"mitigation {mitigation} is not implemented by an activity")
        if Link("risk_mitigation", mitigation, "risk_verification", "verifies") not in links:
            missing.append(f"mitigation {mitigation} is not verified by an activity")
    for risk in expected.high_risks:
        if not {
            lnk
            for lnk in links
            if lnk.source_type == "risk"
            and lnk.source_id == risk
            and lnk.element_kind in ("risk_treatment", "risk_verification")
        }:
            missing.append(f"HIGH risk {risk} has no workflow activity")
    return missing


# --- the story ------------------------------------------------------------------------------


@pytest.fixture
def world(db_session: Session) -> P10World:
    return make_p10_world(db_session)


def test_p10_exit_story(world: P10World) -> None:
    session, pid = world.session, world.project_id

    # 1. a high-regulation project, through G6 by all four roles
    run = world.select()
    factors = {
        f.factor_id: f.score
        for f in session.scalars(select(SdlcFactor).where(SdlcFactor.sdlc_run_id == run.id))
    }
    assert factors["regulatory_criticality"] >= 4, "step 1: a high-regulation project"
    tasks = list(
        session.scalars(
            select(ApprovalTask).where(
                ApprovalTask.project_id == pid,
                ApprovalTask.gate == Gate.G6_SDLC_SELECTION,
                ApprovalTask.task_group_id == run.g6_task_group_id,
            )
        )
    )
    assert len(tasks) == 4 and all(t.status is ApprovalTaskStatus.APPROVED for t in tasks)
    decisions = session.scalars(
        select(ApprovalDecision).where(ApprovalDecision.task_id.in_([t.id for t in tasks]))
    ).all()
    assert {d.decision for d in decisions} == {ApprovalDecisionType.APPROVE} and len(decisions) == 4

    expected = expected_from_the_records(session, pid, run.baseline_id)
    assert len(expected.mappings) >= 3, "step 1: the project maps several controls"
    assert expected.high_risks and expected.mitigations, "step 1: HIGH risks with mitigations"

    # 2. the workflow is generated from that selection - deterministically
    summary = world.generate(run.id)
    assert summary.status is GraphRunStatus.COMPLETED, summary.errors
    assert summary.provider_calls == 0, "step 2: workflow generation makes no model call"
    workflow = session.get(Workflow, summary.workflow_id)
    assert workflow is not None
    assert workflow.sdlc_run_id == run.id and workflow.candidate_key == run.selected_candidate

    # 3. THE EXIT CRITERION: every mandatory checkpoint and every high-risk mitigation
    links = workflow_links(session, workflow.id)
    assert exit_criterion_violations(expected, links) == []

    # 4. each is traceable to the record that caused it (N.2 #24-#26)
    edges = {
        (lnk.from_type, lnk.from_id, lnk.link_type, lnk.to_type, lnk.to_id)
        for lnk in session.scalars(
            select(TraceabilityLink).where(TraceabilityLink.project_id == pid)
        )
    }
    gates = {
        g.id: g
        for g in session.scalars(
            select(WorkflowGate).where(WorkflowGate.workflow_id == workflow.id)
        )
    }
    activities = {
        a.id: a
        for a in session.scalars(
            select(WorkflowActivity).where(WorkflowActivity.workflow_id == workflow.id)
        )
    }
    for source in session.scalars(
        select(WorkflowSource).where(WorkflowSource.workflow_id == workflow.id)
    ):
        if source.source_type == "compliance_mapping":
            assert source.element_id in gates
            assert (
                "compliance_mapping", str(source.source_id), "REQUIRES_CHECKPOINT",
                "workflow_gate", str(source.element_id),
            ) in edges  # fmt: skip
        if source.source_type in ("risk", "risk_mitigation"):
            assert source.element_id in activities
            assert (
                source.source_type, str(source.source_id), "REQUIRES_ACTIVITY",
                "workflow_activity", str(source.element_id),
            ) in edges  # fmt: skip
    assert (
        "sdlc_candidate", str(workflow.selected_candidate_id), "REALISED_AS",
        "workflow", str(workflow.id),
    ) in edges  # fmt: skip
    for mapping_id in expected.mappings:
        mapping = session.get(ComplianceMapping, mapping_id)
        assert mapping is not None and mapping.project_id == pid

    # 5. the production-readiness approval is a gate *inside* the workflow
    readiness = [g for g in gates.values() if g.kind == "production_readiness"]
    assert len(readiness) == 1 and readiness[0].approver_roles and readiness[0].required_evidence
    release = session.get(WorkflowPhase, readiness[0].phase_id)
    assert release is not None and "release" in release.stages

    # 6. persisted and retrievable - read back by another role, same content
    reader = world.workflows(world.p9.p8.compliance_officer)
    stored = reader.get(pid, workflow.id)
    assert stored is not None
    view = reader.view(pid, stored)
    assert view.plan.content_hash() == workflow.content_hash == workflow.generated_hash
    assert workflow.status in (WorkflowStatus.COMPLETE, WorkflowStatus.OPEN_ITEMS)

    # 7. project-scoped: another project's member cannot see it
    from tests.p3_helpers import member
    from tests.workflow.test_p1_exit_test import make_project

    elsewhere = make_project(session, "Another lender (synthetic)")
    outsider = member(session, elsewhere, Role.PROJECT_MANAGER, "pm-elsewhere@example.test")
    with pytest.raises((ProjectIsolationError, AuthorizationError)):
        world.workflows(outsider).get(pid, workflow.id)
    assert all(
        row.project_id == pid
        for model in (WorkflowPhase, WorkflowActivity, WorkflowGate, WorkflowSource)
        for row in session.scalars(select(model).where(model.workflow_id == workflow.id))
    )

    # 8. the check is not vacuous: remove one checkpoint link, or one verification,
    #    and the same function reports exactly that element as missing.
    a_mapping = next(iter(expected.mappings))
    without_checkpoint = {lnk for lnk in links if lnk.source_id != a_mapping}
    assert exit_criterion_violations(expected, without_checkpoint) == [
        f"no compliance checkpoint for mapping {a_mapping}"
    ]
    a_mitigation = next(iter(expected.mitigations))
    without_verification = {
        lnk
        for lnk in links
        if not (lnk.source_id == a_mitigation and lnk.element_kind == "risk_verification")
    }
    assert exit_criterion_violations(expected, without_verification) == [
        f"mitigation {a_mitigation} is not verified by an activity"
    ]
