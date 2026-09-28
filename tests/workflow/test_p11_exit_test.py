"""The P11 end-to-end exit test (roadmap P11 "Guardrails hardening").

The approved exit criterion (``docs/01-analysis.md`` §P), all three parts:

1. **Zero approval-gate bypasses under the adversarial suite.** Every case of
   :mod:`tests.p11_adversarial` - agent, pipeline and cross-project deciders,
   wrong roles and wrong people, forged records, direct lifecycle moves, decision
   reuse, stale bindings, replays, forged capabilities, compromised models and
   injected documents, across all eight gates - is attempted against the real
   paths; each must reach an enforcement point, and none may pass a gate.
2. **A masking test on synthetic financial identifiers.** A synthetic transcript
   carrying the whole identifier corpus is ingested and extracted by the real
   pipeline against a provider that *leaves the machine*; the provider receives
   masked content only, and every category is masked.
3. **Replay reconstructs a requirement's and a risk's history.** A requirement
   with two versions through G1 and a change, and a HIGH risk decided at G8 with
   an accepted mitigation, are reconstructed from the audit trail; the result is
   compared with an expectation computed *here* from the persisted records, and a
   tampered trail is shown not to pass as complete.

Nothing reaches a network. Synthetic data only.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS, extraction_rules, ingest, member, segment_id
from tests.p8_helpers import make_p8_world
from tests.p11_adversarial import g6_cases, gate_cases, make_g6_world, make_gate_world, run_cases
from tests.p11_helpers import (
    RAW_VALUES,
    SYNTHETIC_IDENTIFIERS,
    identifier_text,
    leaked,
    request_text,
)

from reqpilot.domain.enums import (
    AuditEventType,
    DataSensitivity,
    MaskCategory,
    MaskingStatus,
    MitigationStatus,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.guardrails import MaskingMapEntry
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider
from reqpilot.repositories.risk import RiskMitigationRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.audit.replay import ReplayService
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.risk.service import RiskService

pytestmark = pytest.mark.workflow


# --- criterion 1 ---------------------------------------------------------------------


def test_criterion_1_zero_approval_gate_bypasses(db_session: Session, capsys) -> None:
    gate_report = run_cases(db_session, make_gate_world(db_session), gate_cases())
    g6_report = run_cases(db_session, make_g6_world(db_session), g6_cases())
    outcomes = gate_report.outcomes + g6_report.outcomes
    attempted = len(outcomes)
    exercised = [o for o in outcomes if o.exercised]
    bypasses = [o for o in outcomes if o.bypass]
    with capsys.disabled():
        print(f"\nP11 exit criterion 1: {attempted} attempted, {len(exercised)} exercised, "
              f"{len(bypasses)} successful bypasses")  # fmt: skip
        print(gate_report.table())
        print(g6_report.table())
    assert attempted >= 28
    assert len(exercised) == attempted, [o.case.name for o in outcomes if not o.exercised]
    assert bypasses == [], [(o.case.name, o.bypass) for o in bypasses]
    covered = gate_report.gates_covered() | g6_report.gates_covered()
    assert covered == {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"}, "every gate attacked"
    vectors = Counter(o.case.vector for o in outcomes)
    assert {"model output", "injected document", "forged record", "agent capability",
            "cross-project", "wrong role"} <= set(vectors)  # fmt: skip


# --- criterion 2 ---------------------------------------------------------------------


def _responder(request: LLMRequest) -> str:
    kind = request.prompt_template_id.split("@", 1)[0]
    if kind == "requirement_extraction":
        seg = segment_id(request, "[MASKED_PAN_1]")
        return json.dumps(
            {
                "requirements": [
                    {
                        "candidate_key": "c1",
                        "statement": "The system shall validate the applicant's PAN "
                        "[MASKED_PAN_1] against the tax registry.",
                        "requirement_type": "functional",
                        "evidence": [
                            {
                                "segment_id": seg,
                                "quote": "The PAN [MASKED_PAN_1] must be validated against "
                                "the tax registry.",
                            }
                        ],
                        "acceptance_criteria": [],
                        "review_signal": 0.9,
                    }
                ]
            }
        )
    return json.dumps(
        {"labels": [{"category": "functional", "review_signal": 0.9, "rationale": "r"}]}
    )


def test_criterion_2_synthetic_financial_identifiers_are_masked_before_egress(
    db_session: Session,
) -> None:
    from tests.workflow.test_p1_exit_test import make_project

    project = make_project(db_session, "P11 exit - masking (synthetic)")
    analyst = member(db_session, project, Role.ANALYST, f"a-{uuid.uuid4().hex[:6]}@example.test")
    provider = ScriptedProvider(_responder)
    provider.leaves_machine = True  # type: ignore[attr-defined] - an external model
    gateway = LLMGateway(provider, settings=TEST_SETTINGS, sleep=lambda _s: None)

    document = ingest(
        db_session,
        analyst,
        project.id,
        identifier_text(),
        title="Loan intake with identifiers (synthetic)",
        sensitivity=DataSensitivity.UNCLASSIFIED,  # not synthetic: egress needs masking
    )
    summary = AnalysisRunner(
        db_session, gateway, extraction_rules(), settings=TEST_SETTINGS
    ).extract(
        actor=analyst, project_id=ProjectId(project.id), source_ids=[document.id], domain="LOAN"
    )

    # The boundary: what the external provider actually received.
    assert provider.requests, "the model was called - masking is what let the content leave"
    sent = [request_text(r) for r in provider.requests]
    assert leaked(sent) == [], "no raw synthetic identifier reached the provider"
    assert summary.status.value == "completed", summary.errors

    # Every category of the corpus was recognised and replaced (13 values, 11 kinds).
    assert document.masking_status is MaskingStatus.MASKED
    entries = db_session.scalars(
        select(MaskingMapEntry).where(MaskingMapEntry.source_id == document.id)
    ).all()
    assert Counter(e.category for e in entries) == Counter(
        i.category.value for i in SYNTHETIC_IDENTIFIERS
    )
    assert {e.category for e in entries} == {c.value for c in MaskCategory}
    assert sorted(e.value for e in entries) == sorted(RAW_VALUES), "the map is kept, apart"

    # Nothing else carries a raw value either: stored text, the requirement, the audit.
    assert leaked(document.text) == []
    versions = db_session.scalars(
        select(RequirementVersion).where(RequirementVersion.project_id == project.id)
    ).all()
    assert versions and leaked([v.statement for v in versions]) == []
    assert leaked([e.payload for e in AuditService(db_session).list_for_project(project.id)]) == []

    # Negative control: the same check does see a raw value when one is present.
    assert leaked(identifier_text()) == list(RAW_VALUES)


# --- criterion 3 ---------------------------------------------------------------------


def test_criterion_3_replay_reconstructs_a_requirements_and_a_risks_history(
    db_session: Session,
) -> None:
    world = make_p8_world(db_session, "P11 exit - replay (synthetic)")
    world.govern_and_baseline(["L01"], "B1")  # L01: validated, G1 co-approved, baselined
    l01 = world.version("L01")
    v2 = RequirementService(db_session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=l01.requirement_id,
        content=RequirementContent(
            statement=l01.statement.rstrip(".") + ", in a tamper-evident archive.",
            source_refs=tuple(l01.source_refs or ()),
        ),
        change_reason="Archive requirement added (synthetic).",
    )
    risk = db_session.scalars(
        select(Risk).where(
            Risk.project_id == world.project_id, Risk.requirement_version_id == l01.id
        )
    ).first()
    assert risk is not None and str(risk.severity) == "high", "L01 carries a HIGH risk (G8)"
    mitigation = RiskMitigationRepository(db_session, world.analyst).list_for_risk(
        world.project_id, risk.id
    )[0]
    RiskService(db_session, world.security_reviewer).decide_mitigation(
        project_id=world.project_id,
        mitigation_id=mitigation.id,
        status=MitigationStatus.ACCEPTED,
        rationale="Adopted (synthetic).",
    )
    replay = ReplayService(db_session, world.auditor)

    # -- the requirement --------------------------------------------------------------
    result = replay.requirement(world.project_id, l01.requirement_id)
    rows = db_session.scalars(
        select(RequirementVersion)
        .where(RequirementVersion.requirement_id == l01.requirement_id)
        .order_by(RequirementVersion.version_no)
    ).all()
    expected_versions = {
        str(v.id): {
            "version_no": v.version_no,
            "content_hash": v.content_hash,
            "state": str(v.state),
        }
        for v in rows
    }
    assert result.complete, result.gaps
    assert {
        vid: {k: v[k] for k in ("version_no", "content_hash", "state")}
        for vid, v in result.reconstructed["versions"].items()
    } == expected_versions
    assert result.reconstructed["current_version_id"] == str(v2.id)
    # The path v1 took, reconstructed transition by transition, in chain order:
    trail = db_session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.project_id == world.project_id,
            AuditEvent.subject_type == "requirement_version",
            AuditEvent.subject_id == str(l01.id),
            AuditEvent.event_type == AuditEventType.STATE_TRANSITION,
        )
        .order_by(AuditEvent.seq)
    ).all()
    expected_path = [(e.payload["from"], e.payload["to"]) for e in trail]
    replayed_path = [
        (s.change["from"], s.change["to"])
        for s in result.steps
        if s.subject_id == str(l01.id) and "to" in s.change
    ]
    assert replayed_path == expected_path
    assert expected_path[-2:] == [("PENDING_APPROVAL", "APPROVED"), ("APPROVED", "BASELINED")]
    assert [s.seq for s in result.steps] == sorted(s.seq for s in result.steps)

    # -- the risk -----------------------------------------------------------------------
    risk_result = replay.risk(world.project_id, risk.id)
    db_session.refresh(risk)
    assert risk_result.complete, risk_result.gaps
    data = risk_result.reconstructed
    for field in ("status", "severity", "likelihood", "impact", "matrix_version"):
        assert data[field] == str(getattr(risk, field)), field
    assert data["severity_source"] == "matrix", "the matrix's result, not the model's proposal"
    assert data["decisions"][-1]["gate"] == "G8" and data["decisions"][-1]["decision"] == "APPROVE"
    persisted = {
        str(m.id): str(m.status)
        for m in db_session.scalars(select(RiskMitigation).where(RiskMitigation.risk_id == risk.id))
        if str(m.status) != "suggested"
    }
    assert data["mitigations"] == persisted == {str(mitigation.id): "accepted"}

    # -- not vacuous: a tampered trail never passes as complete --------------------------
    first = trail[0]
    db_session.execute(
        update(AuditEvent).where(AuditEvent.id == first.id).values(payload={"from": "X", "to": "Y"})
    )
    db_session.expire_all()
    tampered = replay.requirement(world.project_id, l01.requirement_id)
    assert not tampered.complete and not tampered.chain_ok
