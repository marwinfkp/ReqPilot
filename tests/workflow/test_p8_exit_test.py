"""The P8 end-to-end exit test (P8 brief §39; roadmap P8 "Approval, traceability & documents").

The roadmap exit, verbatim: *"An unapproved requirement cannot enter a baseline
or a document" (automated); end-to-end SRS + RTM + risk register produced; E6
computed.*

One synthetic loan-origination workshop (``tests.p8_helpers.WORKSHOP``, fictional
speakers) goes through the real P3 extraction/classification, P5 quality and
conflict detection, P6 compliance/security, P7 risk analysis, the P1 lifecycle
guards, the one approval service and P8's own readiness, trace and artefact
services. The model is the scripted P8 model; nothing reaches a network.

The seventeen steps the brief asks for, and where each is asserted:

1.  a synthetic loan-origination project ....................... ``world`` fixture
2.  requirements from the existing P3/P4 pathways ............. step 2
3.  P5 conflict / quality outputs ............................. step 3
4.  P6 compliance / security outputs .......................... step 4
5.  P7 risks .................................................. step 5
6.  at least one requirement stays unapproved ................. step 6
7.  it cannot enter a baseline or a document .................. step 7 (and 11)
8.  the approval workflow (G2/G3/G8, G4, G5, G1) .............. step 8
9.  an approved baseline ...................................... step 9
10. SRS + RTM + risk register ................................. step 10
11. baseline-only content, graph-sourced RTM, persisted risks,
    per-section trace links, metadata, hashes ................. step 11
12. Markdown export ........................................... step 12
13. DOCX export ............................................... step 13
14. the DOCX re-opened and read ............................... step 14
15. the audit chain ........................................... step 15
16. project isolation ......................................... step 16
17. historical artefact / version preservation ................ step 17

The story is told as one test because every step acts on what the previous one
produced; each step is a labelled block whose assertion message names it.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.artifacts.docx import docx_text
from reqpilot.domain.enums import (
    ApprovalTaskStatus,
    ArtifactFormat,
    ArtifactType,
    AuditEventType,
    Gate,
    RequirementCategory,
    RiskSeverity,
    Role,
)
from reqpilot.domain.errors import (
    ArtifactError,
    BaselineInvariantError,
    GovernanceBlockedError,
    ProjectIsolationError,
    ReqPilotError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.artifacts import ArtifactVersion
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.baseline import Baseline
from reqpilot.domain.models.extraction import RequirementClassification
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.baseline import BaselineService
from reqpilot.services.documents import ArtifactService
from reqpilot.services.governance import GovernanceReadinessService, UnifiedReviewQueue
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.traceability import NOT_LINKED, TraceGraphSync, TraceQueryService

pytestmark = pytest.mark.workflow

N, L = TraceNodeType, TraceLinkType

#: What B1 holds: L01 (G2/G3/G8), L03 (plain), L05 (G5), L06 (G4 conflict winner),
#: L08 (the injection attempt). L02 and L04 stay unapproved; L07 is withdrawn by G4.
B1_KEYS = ["L01", "L03", "L05", "L06", "L08"]
UNAPPROVED = ["L02", "L04", "L07"]
EXIT_TYPES = (ArtifactType.SRS, ArtifactType.RTM, ArtifactType.RISK_REGISTER)


@pytest.fixture
def world(db_session: Session) -> P8World:
    return make_p8_world(db_session)


def blocker_codes(world: P8World, key: str) -> set[str]:
    readiness = GovernanceReadinessService(world.session, world.analyst)
    return {
        b.code
        for b in readiness.evaluate(
            world.project_id, world.version(key), stage="submission"
        ).blockers
    }


def cited_versions(version: ArtifactVersion) -> set[str]:
    return {
        c["id"]
        for s in version.structure["sections"]
        for c in s["citations"]
        if c["kind"] == "requirement_version"
    }


def test_p8_exit_story(world: P8World, db_session: Session) -> None:
    session, pid = world.session, world.project_id
    artefacts = ArtifactService(session, world.analyst)

    # -- 2. requirements from the P3 pathway (extraction + classification) ------------
    assert set(world.versions) == {f"L0{i}" for i in range(1, 9)}, "step 2"
    for key in world.versions:
        version = world.version(key)
        assert version.source_refs, f"step 2: {key} carries its source reference"
        labels = session.scalars(
            select(RequirementClassification).where(
                RequirementClassification.requirement_version_id == version.id
            )
        ).all()
        assert labels, f"step 2: {key} was classified (P3 labels)"

    # -- 3. P5 conflict and quality outputs -------------------------------------------
    conflict = world.conflict()
    assert conflict.involves_stakeholder_disagreement, "step 3: the L06/L07 conflict"
    assert "OPEN_CONFLICT" in blocker_codes(world, "L06"), "step 3"

    # -- 4. P6 compliance / security outputs ------------------------------------------
    # -- 5. P7 risks ------------------------------------------------------------------
    l01 = blocker_codes(world, "L01")
    assert {"G2_PENDING", "G3_PENDING"} <= l01, "step 4: P6 raised G2 and G3"
    assert "G8_UNREVIEWED" in l01, "step 5: P7 raised a high-severity risk (G8)"
    risks = list(session.scalars(select(Risk).where(Risk.project_id == pid)))
    assert any(r.severity is RiskSeverity.HIGH for r in risks), "step 5"
    assert "G5_REQUIRED" in blocker_codes(world, "L05"), "step 5b: M.3 architecture-critical"

    # -- 6/7. an unapproved requirement cannot enter a baseline or a document ----------
    with pytest.raises(ArtifactError, match="baseline"):
        artefacts.generate(pid, uuid.uuid4(), ArtifactType.SRS)  # no baseline, no document
    with pytest.raises(ReqPilotError), session.begin_nested():
        world.submit(["L02"])  # step 7: not VALIDATED, cannot even be submitted
    world.validate("L05")
    with pytest.raises(GovernanceBlockedError, match="G5"), session.begin_nested():
        world.submit(["L05"])  # step 7: validated, but G5 is not decided
    world.validate("L03")
    world.resolve_conflict()
    world.validate("L06")
    with pytest.raises(GovernanceBlockedError, match="G4"), session.begin_nested():
        world.submit(["L06"])  # step 7: the P5 resolution alone is not a G4 decision

    # -- 8. the approval workflow, every gate decided by its own human role -----------
    fan = world.fan_out()
    assert {t.required_role for t in fan.g4} == {Role.ANALYST, Role.STAKEHOLDER}, "step 8"
    assert {t.assignee_user_id for t in fan.g4 if t.required_role is Role.STAKEHOLDER} == {
        world.priya.actor_id,
        world.omar.actor_id,
    }, "step 8: each affected stakeholder signs G4 personally"
    assert [t.required_role for t in fan.g5] == [Role.PROJECT_MANAGER], "step 8"
    world.sign_g4()
    world.sign_g5()
    for key in ("L01", "L08"):
        world.validate(key)  # decides the open G2/G3/G8 tasks as their own roles
    for key in B1_KEYS:
        assert not blocker_codes(world, key), f"step 8: {key} is ready"
    g1 = world.submit(B1_KEYS)
    assert {t.required_role for t in g1} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}, "step 8"

    # -- 9. an approved baseline ------------------------------------------------------
    b1 = world.approve_g1(g1, "B1")
    assert b1 is not None, "step 9"
    members = {m.id for m in world.baseline_members(b1)}
    assert members == {world.versions[k].id for k in B1_KEYS}, "step 9"
    for key in B1_KEYS:
        assert world.version(key).state is RequirementState.BASELINED, f"step 9: {key}"
    for key in UNAPPROVED:
        assert world.version(key).state is not RequirementState.BASELINED, f"step 9: {key}"
    assert world.version("L07").state is RequirementState.WITHDRAWN, "step 9: G4 outcome"
    baseline_row = session.get(Baseline, b1)
    with pytest.raises(BaselineInvariantError), session.begin_nested():
        # step 7 again: even with a genuine approval decision in hand, an
        # unapproved version cannot be written into a baseline.
        BaselineService(session, world.analyst).commit(
            project_id=pid,
            label="B-rogue",
            version_ids=[world.versions["L02"].id],
            approval_decision_id=baseline_row.approval_decision_id,
        )

    # -- 10. SRS + RTM + risk register ------------------------------------------------
    outcomes = {o.artifact_type: o for o in artefacts.generate_set(pid, b1, EXIT_TYPES)}
    for artifact_type in EXIT_TYPES:
        assert not outcomes[artifact_type].refused, (
            f"step 10: {artifact_type}",
            outcomes[artifact_type].blockers,
        )
    srs = outcomes[ArtifactType.SRS].version
    rtm = outcomes[ArtifactType.RTM].version
    register = outcomes[ArtifactType.RISK_REGISTER].version

    # -- 11. the invariant, checked against the records -------------------------------
    member_ids = {str(m) for m in members}
    for version in (srs, rtm, register):
        assert cited_versions(version) <= member_ids, f"step 11: {version.artifact_type}"
        for key in UNAPPROVED:
            statement = world.version(key).statement
            assert statement[:40] not in version.markdown, f"step 11: {key} leaked"
    assert cited_versions(srs) == member_ids, "step 11: every baselined version is in the SRS"

    graph = TraceQueryService(session, world.analyst).graph(pid)
    rows = artefacts.rtm_rows(pid, b1)
    assert {r.get("version_id") for r in rows} == member_ids, "step 11: RTM = baseline"
    for row in rows:
        vid = uuid.UUID(row.get("version_id"))
        has_source = bool(graph.incoming(N.REQUIREMENT_VERSION, vid, L.SOURCES))
        assert (row.get("sources") != NOT_LINKED) is has_source, "step 11: RTM from graph"
        has_risk = bool(
            graph.targets(N.REQUIREMENT_VERSION, vid, L.HAS_RISK)
            or graph.outgoing(N.REQUIREMENT_VERSION, vid, L.RISK_ASSESSED_BY)
        )
        assert (row.get("risks") != NOT_LINKED) is has_risk, "step 11: RTM from graph"
        assert graph.targets(N.REQUIREMENT_VERSION, vid, L.APPROVED_BY), "step 11: approval"

    persisted = {str(r.id): r for r in risks}
    register_risks = [
        s["key"].split(".", 1)[1]
        for s in register.structure["sections"]
        if s["key"].startswith("risk.")
    ]
    assert register_risks, "step 11: the register lists risks"
    for rid in register_risks:
        assert rid in persisted, "step 11: every risk is a persisted P7 risk"
        risk = persisted[rid]
        assert risk.requirement_version_id is None or str(risk.requirement_version_id) in (
            member_ids
        ), "step 11: requirement risks come from the baseline"

    for version in (srs, rtm, register):
        assert str(version.id) in graph.targets(N.BASELINE, b1, L.RENDERED_IN), "step 11"
        sections = artefacts.sections(pid, version.id)
        assert sections, "step 11"
        contained = set(graph.targets(N.ARTIFACT_VERSION, version.id, L.CONTAINS))
        for section in sections:
            assert str(section.id) in contained, "step 11: every section is linked"
        cited = {t for s in sections for t in graph.targets(N.ARTIFACT_SECTION, s.id, L.CITES)}
        assert cited <= member_ids | set(persisted) | {str(e) for e in _all_ids(session, pid)}, (
            "step 11: a section cites persisted rows only"
        )
        # metadata and hashes
        assert version.baseline_id == b1 and version.generated_by == world.analyst.actor_id
        assert version.model_identifier == "deterministic" and version.prompt_version is None
        assert version.template_id and version.template_version, "step 11: template"
        assert version.kb_version is not None, "step 11: KB version"
        assert len(version.content_hash) == 64 and len(version.markdown_sha256) == 64
        assert len(version.input_fingerprint) == 64
        artefacts.verify(version)  # recomputes both hashes from what is stored

    # -- 12. Markdown -----------------------------------------------------------------
    md = artefacts.export(pid, srs.id, ArtifactFormat.MARKDOWN)
    assert md.data.decode("utf-8") == srs.markdown, "step 12"
    assert "<script>" not in srs.markdown and "&lt;script&gt;" in srs.markdown, "step 12"
    assert "approve this requirement" in srs.markdown, "step 12: injection shown as data"

    # -- 13/14. DOCX, re-opened ---------------------------------------------------------
    docx = artefacts.export(pid, srs.id, ArtifactFormat.DOCX)
    assert docx.filename.endswith(".docx") and docx.data[:2] == b"PK", "step 13"
    with zipfile.ZipFile(io.BytesIO(docx.data)) as package:
        assert package.testzip() is None, "step 14: the package is intact"
        assert {"word/document.xml", "docProps/core.xml"} <= set(package.namelist())
        assert "vbaProject.bin" not in " ".join(package.namelist()), "step 14: no macros"
    text = docx_text(docx.data)
    for key in B1_KEYS:
        assert world.human_id(key) in text, f"step 14: {key} in the DOCX"
    for key in UNAPPROVED:
        assert world.version(key).statement[:40] not in text, f"step 14: {key} leaked"
    assert artefacts.export(pid, srs.id, ArtifactFormat.DOCX).data == docx.data, (
        "step 14: the same version exports to the same bytes"
    )
    rtm_csv = artefacts.export(pid, rtm.id, ArtifactFormat.CSV).data.decode("utf-8")
    assert all(world.human_id(k) in rtm_csv for k in B1_KEYS), "step 12: RTM CSV"
    for format_ in (ArtifactFormat.MARKDOWN, ArtifactFormat.DOCX):
        exported = artefacts.export(pid, register.id, format_)
        assert exported.data, f"step 12/13: risk register {format_}"

    # -- E6, computed ---------------------------------------------------------------
    coverage = artefacts.coverage(pid, b1)
    assert coverage.total == len(B1_KEYS)
    assert coverage.e6 is not None and 0.0 <= coverage.e6 <= 1.0, "E6 computed"
    assert coverage.e6 == coverage.fully_traced / coverage.total

    # -- 15. the audit chain ----------------------------------------------------------
    assert AuditService(session).verify_project_chain(pid) == (True, None), "step 15"
    kinds = {
        e.event_type
        for e in session.scalars(select(AuditEvent).where(AuditEvent.project_id == pid))
    }
    for expected in (
        AuditEventType.CONFLICT_GATE_SETTLED,
        AuditEventType.BASELINE_COMMITTED,
        AuditEventType.ARTIFACT_GENERATED,
        AuditEventType.ARTIFACT_VERSION_CREATED,
        AuditEventType.ARTIFACT_EXPORTED,
    ):
        assert expected in kinds, f"step 15: {expected} audited"

    # -- 16. project isolation --------------------------------------------------------
    other = make_p8_world(db_session, "Another lender (synthetic)")
    intruder = ArtifactService(db_session, other.analyst)
    for attempt in (
        lambda: intruder.generate(pid, b1, ArtifactType.SRS),
        lambda: intruder.export(pid, srs.id, ArtifactFormat.DOCX),
        lambda: intruder.list_artifacts(pid),
        lambda: intruder.rtm_rows(pid, b1),
        lambda: intruder.coverage(pid, b1),
        lambda: TraceQueryService(db_session, other.analyst).graph(pid),
        lambda: TraceGraphSync(db_session, other.analyst).sync(pid),
        lambda: UnifiedReviewQueue(db_session, other.analyst).build(pid),
        lambda: ApprovalService(db_session, other.analyst).list_tasks(pid),
    ):
        with pytest.raises(ProjectIsolationError):
            attempt()
    assert intruder.get_version(other.project_id, srs.id) is None, "step 16"
    with pytest.raises(ArtifactError):
        # Project B's own baseline id space does not contain A's baseline.
        intruder.generate(other.project_id, b1, ArtifactType.SRS)

    # -- 17. historical preservation --------------------------------------------------
    snapshot = {v.id: (v.content_hash, v.markdown, v.markdown_sha256) for v in (srs, rtm, register)}
    docx_before = docx.data
    v1 = world.version("L03")
    service = RequirementService(session, world.analyst)
    v2 = service.create_version(
        project_id=pid,
        requirement_id=v1.requirement_id,
        content=RequirementContent(
            statement="The system shall display the current loan status and the next step "
            "to the applicant.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=tuple(v1.source_refs),
        ),
        change_reason="the applicant also sees the next step (synthetic)",
    )
    # The unapproved successor does not replace v1 in B1's documents.
    again = artefacts.generate(pid, b1, ArtifactType.SRS)
    assert again.reused and again.version.id == srs.id, "step 17"
    assert v2.statement not in again.version.markdown
    g7 = world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE, status=ApprovalTaskStatus.OPEN)
    assert {t.subject_id for t in g7} == {v2.id}, "step 17: G7 governs the change"
    for task in g7:
        world.decide(task)
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
        RequirementState.VALIDATED,
    ):
        service.transition(project_id=pid, version_id=v2.id, target=target)
    b2 = world.approve_g1(
        ApprovalService(session, world.analyst).submit_versions_for_baseline(
            project_id=pid, version_ids=[v2.id]
        ),
        "B2",
    )
    assert b2 is not None and b2 != b1
    assert world.version("L03").state is RequirementState.SUPERSEDED, "step 17"
    srs2 = artefacts.generate(pid, b2, ArtifactType.SRS)
    assert not srs2.refused and srs2.version.version_no == 2, "step 17"
    assert v2.statement in srs2.version.markdown
    assert str(v1.id) not in cited_versions(srs2.version)
    session.expire_all()
    for vid, (content_hash, markdown, markdown_sha) in snapshot.items():
        stored = session.get(ArtifactVersion, vid)
        assert (stored.content_hash, stored.markdown, stored.markdown_sha256) == (
            content_hash,
            markdown,
            markdown_sha,
        ), "step 17: the old artefact version is untouched"
    assert artefacts.export(pid, srs.id, ArtifactFormat.DOCX).data == docx_before, "step 17"
    assert {m.id for m in world.baseline_members(b1)} == members, "step 17: B1 unchanged"
    assert v1.statement in session.get(ArtifactVersion, srs.id).markdown
    assert AuditService(session).verify_project_chain(pid) == (True, None), "step 17"


def _all_ids(session: Session, pid: uuid.UUID) -> set[uuid.UUID]:
    """Every id a section may legitimately cite: rows of this project's records."""
    from reqpilot.domain.models import (
        AcceptanceCriterion,
        ComplianceMapping,
        Conflict,
        RequirementVersion,
        SecurityPrivacyFinding,
    )
    from reqpilot.domain.models.knowledge import Evidence

    ids: set[uuid.UUID] = set()
    for model in (
        AcceptanceCriterion,
        ComplianceMapping,
        Conflict,
        Evidence,
        RequirementVersion,
        Risk,
        SecurityPrivacyFinding,
    ):
        ids |= set(session.scalars(select(model.id).where(model.project_id == pid)))
    return ids


def test_the_three_roadmap_exit_criteria_hold(world: P8World) -> None:
    """The roadmap exit, each criterion measured rather than asserted by name."""
    pid = world.project_id
    service = ArtifactService(world.session, world.analyst)
    # (1) an unapproved requirement cannot enter a baseline or a document.
    b1 = world.govern_and_baseline(["L03"], "B1")
    for outcome in service.generate_set(pid, b1):
        assert not outcome.refused, (outcome.artifact_type, outcome.blockers)
        assert cited_versions(outcome.version) <= {str(world.versions["L03"].id)}
        for key in ("L01", "L02", "L04", "L05", "L06", "L07", "L08"):
            assert world.version(key).statement[:40] not in outcome.version.markdown
    with pytest.raises(BaselineInvariantError), world.session.begin_nested():
        BaselineService(world.session, world.analyst).commit(
            project_id=pid,
            label="B-rogue",
            version_ids=[world.versions["L02"].id],
            approval_decision_id=world.session.get(Baseline, b1).approval_decision_id,
        )
    # (2) SRS + RTM + risk register produced end to end.
    produced = {v.artifact_type for v in service.all_versions(pid)}
    assert set(EXIT_TYPES) <= produced
    # (3) E6 computed.
    assert service.coverage(pid, b1).e6 is not None
