"""P8 artefacts (FR-DOC-001..010, FR-TRC-002, FR-HIL-004) on the synthetic world:
generated only from an approved baseline, deterministic, versioned, traceable per
section, exported as Markdown / DOCX / CSV from one structure."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.artifacts.docx import docx_text
from reqpilot.domain.compliance.language import COMPLIANCE_ADVISORY_NOTICE, find_prohibited
from reqpilot.domain.enums import (
    ArtifactFormat,
    ArtifactType,
    AuditEventType,
    RequirementCategory,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
)
from reqpilot.domain.errors import ArtifactError, ImmutableRecordError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.artifacts import ArtifactVersion
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.services.documents import ArtifactService
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.traceability import TraceQueryService

pytestmark = pytest.mark.integration

N, L = TraceNodeType, TraceLinkType
IN_B1 = ["L01", "L03", "L04", "L08"]


@pytest.fixture
def world(db_session: Session) -> P8World:
    return make_p8_world(db_session)


@pytest.fixture
def b1(world: P8World) -> uuid.UUID:
    return world.govern_and_baseline(IN_B1, "B1")


def generate(world: P8World, baseline: uuid.UUID, artifact_type: ArtifactType):  # type: ignore[no-untyped-def]
    return ArtifactService(world.session, world.analyst).generate(
        world.project_id, baseline, artifact_type
    )


def test_there_is_no_document_without_a_baseline(world) -> None:
    with pytest.raises(ArtifactError, match="baseline"):
        generate(world, uuid.uuid4(), ArtifactType.SRS)


def test_an_unapproved_requirement_cannot_enter_a_document(world, b1) -> None:
    outcome = generate(world, b1, ArtifactType.SRS)
    assert not outcome.refused
    version = outcome.version
    members = {str(world.versions[k].id) for k in IN_B1}
    cited = {
        c["id"]
        for s in version.structure["sections"]
        for c in s["citations"]
        if c["kind"] == "requirement_version"
    }
    assert cited == members
    for key in ("L02", "L05", "L06", "L07"):  # never approved
        assert world.version(key).state is not RequirementState.BASELINED
        assert world.version(key).statement[:40] not in version.markdown


def test_every_artefact_type_is_generated_with_its_metadata(world, b1) -> None:
    service = ArtifactService(world.session, world.analyst)
    outcomes = service.generate_set(world.project_id, b1)
    assert {o.artifact_type for o in outcomes} == set(ArtifactType)
    for outcome in outcomes:
        assert not outcome.refused, (outcome.artifact_type, outcome.blockers)
        version = outcome.version
        assert version.baseline_id == b1 and version.version_no == 1
        assert version.model_identifier == "deterministic"  # FR-DOC-009, no pretence
        assert version.prompt_version is None
        assert version.kb_version is not None and version.kb_version_source == "evidence"
        assert version.template_id.startswith("reqpilot.") and version.template_version
        assert len(version.content_hash) == 64 and len(version.markdown_sha256) == 64
        assert version.generated_at and version.generated_by == world.analyst.actor_id
        stamp = dict(version.structure["stamp"])
        assert stamp["Model identifier"] == "deterministic"
        assert "Generated at" in stamp and "Knowledge-base version" in stamp
        service.verify(version)  # both hashes still match what is stored


def test_every_section_traces_back_to_the_baseline(world, b1) -> None:
    """FR-DOC-008, queryable: av CONTAINS section CITES requirement_version."""
    service = ArtifactService(world.session, world.analyst)
    outcomes = service.generate_set(world.project_id, b1)
    graph = TraceQueryService(world.session, world.analyst).graph(world.project_id)
    members = {str(world.versions[k].id) for k in IN_B1}
    for outcome in outcomes:
        version = outcome.version
        assert str(version.id) in graph.targets(N.BASELINE, b1, L.RENDERED_IN)
        sections = service.sections(world.project_id, version.id)
        contained = set(graph.targets(N.ARTIFACT_VERSION, version.id, L.CONTAINS))
        assert contained == {str(s.id) for s in sections}
        structure = {s["key"]: s for s in version.structure["sections"]}
        for section in sections:
            cites = set(graph.targets(N.ARTIFACT_SECTION, section.id, L.CITES))
            spec = structure[section.section_key]
            if section.kind == "content" and spec["scope"] == "baseline":
                assert cites & members, (outcome.artifact_type, section.number)
            assert {
                c for c in cites if c in {str(v.id) for v in world.versions.values()}
            } <= members
        assert set(graph.targets(N.ARTIFACT_VERSION, version.id, L.CITES)) <= members


def test_the_srs_has_every_approved_section_and_invents_nothing(world, b1) -> None:
    version = generate(world, b1, ArtifactType.SRS).version
    keys = {s["key"]: s for s in version.structure["sections"]}
    for key in (
        "scope",
        "stakeholders",
        "dim.fr",
        "dim.nfr",
        "dim.security",
        "dim.privacy",
        "dim.regulatory",
        "dim.performance",
        "dim.availability",
        "dim.audit_reporting",
        "dim.operational",
        "dim.business",
        "data.requirements",
        "data.retention",
        "data.privacy",
        "data.audit",
        "if.system",
        "if.user",
        "if.auth",
        "7.assumptions",
        "7.dependencies",
        "constraints",
        "open_issues",
        "compliance",
        "risk.requirements",
        "risk.project",
        "trace",
        "approvals",
    ):
        assert key in keys, key
    for key in IN_B1:
        assert f"req.{world.human_id(key)}" in keys
    # Nothing in B1 is classified performance or integration: declared empty, not filled.
    assert keys["dim.performance"]["kind"] == "empty"
    assert keys["if.system"]["kind"] == "empty"
    assert "not recorded" in version.markdown  # absent priority / justification
    assert COMPLIANCE_ADVISORY_NOTICE in version.markdown


def test_the_compliance_matrix_keeps_the_advisory_boundary(world, b1) -> None:
    version = generate(world, b1, ArtifactType.COMPLIANCE_MATRIX).version
    assert COMPLIANCE_ADVISORY_NOTICE in version.markdown
    assert not find_prohibited(version.markdown)
    assert "candidate" in version.markdown and "LO-RET-APPLICATION-RECORDS" in version.markdown
    matrix = next(s for s in version.structure["sections"] if s["key"] == "matrix")
    assert matrix["blocks"][0]["columns"][6] == "Mapping status"


def test_the_risk_register_reads_the_persisted_severity(world, b1) -> None:
    version = generate(world, b1, ArtifactType.RISK_REGISTER).version
    risks = {
        r.id: r
        for r in world.session.scalars(select(Risk).where(Risk.project_id == world.project_id))
    }
    details = [s for s in version.structure["sections"] if s["key"].startswith("risk.")]
    assert details
    for section in details:
        risk = risks[uuid.UUID(section["key"].split(".", 1)[1])]
        fields = dict(section["blocks"][0]["pairs"])
        assert fields["Severity"].startswith(risk.severity.value)
        assert "not recalculated" in fields["Severity"]
        if risk.requirement_version_id is not None:
            assert str(risk.requirement_version_id) in {str(world.versions[k].id) for k in IN_B1}
    assert (
        "requires human validation" in version.markdown or "accepted by a human" in version.markdown
    )


def test_user_stories_and_use_cases_reuse_persisted_criteria(world, b1) -> None:
    stories = generate(world, b1, ArtifactType.USER_STORIES).version
    cases = generate(world, b1, ArtifactType.USE_CASES).version
    l03 = world.human_id("L03")
    assert f"US-{l03}-v1" in stories.markdown and f"UC-{l03}-v1" in cases.markdown
    assert "the current loan status is shown" in stories.markdown
    assert "an applicant with a submitted application" in cases.markdown
    # Only functional requirements become stories; the NFR is listed, not storied.
    assert f"US-{world.human_id('L01')}" not in stories.markdown
    assert "Alternate / exception flows:** not recorded" in cases.markdown
    assert "stakeholder (not recorded)" not in stories.markdown  # speakers are recorded here


def test_the_assumptions_register_uses_recorded_fields_only(world) -> None:
    service = RequirementService(world.session, world.analyst)
    base = world.version("L03")
    _r, version = service.create_requirement(
        project_id=world.project_id,
        domain="LOAN",
        content=RequirementContent(
            statement="The system shall notify the applicant of a missing document.",
            category=RequirementCategory.FUNCTIONAL,
            assumptions=("applicants have a verified email address",),
            dependencies=(world.human_id("L03"), "the notification gateway"),
            source_refs=tuple(base.source_refs),
        ),
    )
    world.versions["A1"] = version
    baseline = world.govern_and_baseline(["L03", "A1"], "B-assumptions")
    register = generate(world, baseline, ArtifactType.ASSUMPTIONS_DEPENDENCIES).version
    assert (
        "A-001" in register.markdown
        and "applicants have a verified email address" in register.markdown
    )
    assert "D-001" in register.markdown and "resolves to" in register.markdown
    assert "not resolved to a requirement of this baseline" in register.markdown


def test_open_issues_stay_visibly_unresolved(world, b1) -> None:
    version = generate(world, b1, ArtifactType.OPEN_ISSUES).version
    assert "not in this baseline; not approved" in version.markdown
    tasks = next(s for s in version.structure["sections"] if s["key"] == "tasks")
    assert tasks["kind"] == "content" and tasks["scope"] == "project"


def test_regeneration_is_idempotent_and_history_is_kept(world, b1) -> None:
    first = generate(world, b1, ArtifactType.SRS)
    again = generate(world, b1, ArtifactType.SRS)
    assert again.reused and again.version.id == first.version.id
    snapshot = (first.version.content_hash, first.version.markdown)
    # A new baseline adds L02 (governed); the next SRS is a new version.
    world.validate("L02")
    b2 = world.approve_g1(world.submit(["L02"]), "B2")
    second = generate(world, b2, ArtifactType.SRS)
    assert not second.reused and second.version.version_no == 2 and second.version.baseline_id == b2
    assert str(world.versions["L02"].id) in {
        c["id"] for s in second.version.structure["sections"] for c in s["citations"]
    }
    stored = world.session.get(ArtifactVersion, first.version.id)
    assert (stored.content_hash, stored.markdown) == snapshot
    assert world.version("L02").statement[:40] not in stored.markdown
    # Regenerating from the *old* baseline still renders the old set.
    old = generate(world, b1, ArtifactType.SRS)
    assert old.reused and old.version.id == first.version.id


def test_an_unapproved_successor_cannot_replace_its_approved_version(world, b1) -> None:
    v1 = world.version("L03")
    RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=v1.requirement_id,
        content=RequirementContent(
            statement="The system shall display a completely different status page.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=tuple(v1.source_refs),
        ),
        change_reason="unapproved change",
    )
    srs = generate(world, b1, ArtifactType.SRS).version
    assert "completely different status page" not in srs.markdown
    assert v1.statement in srs.markdown


def test_an_ungoverned_project_high_risk_refuses_the_register_and_is_audited(world, b1) -> None:
    from reqpilot.repositories.risk import RiskRepository
    from reqpilot.services.risk.service import RiskService

    evidence = RiskRepository(world.session, world.analyst).evidence_ids(
        world.project_id,
        next(r for r in world.session.scalars(select(Risk)) if r.project_id == world.project_id).id,
    )
    RiskService(world.session, world.analyst).add_risk(
        project_id=world.project_id,
        category=RiskCategory.OPERATIONAL,
        title="Branch cut-over may stall disbursement",
        description="The cut-over weekend may stall disbursement processing.",
        likelihood=RiskLikelihood.L3,
        impact=RiskImpact.I3,
        likelihood_rationale="Cut-overs have slipped before (synthetic).",
        impact_rationale="Disbursement would stop (synthetic).",
        evidence_ids=list(evidence),
    )
    refused = generate(world, b1, ArtifactType.RISK_REGISTER)
    assert refused.refused and refused.version is None
    assert any("G8_UNREVIEWED" in b for b in refused.blockers)
    assert generate(world, b1, ArtifactType.SRS).refused
    assert not generate(world, b1, ArtifactType.USER_STORIES).refused
    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.ARTIFACT_GENERATION_REFUSED
    ]
    assert events and "G8_UNREVIEWED" in events[0].payload["blocker_codes"]


def test_exports_render_the_stored_structure(world, b1) -> None:
    service = ArtifactService(world.session, world.analyst)
    srs = generate(world, b1, ArtifactType.SRS).version
    rtm = generate(world, b1, ArtifactType.RTM).version
    md = service.export(world.project_id, srs.id, ArtifactFormat.MARKDOWN)
    assert md.data.decode() == srs.markdown and md.filename == "srs-v1.md"
    docx = service.export(world.project_id, srs.id, ArtifactFormat.DOCX)
    text = docx_text(docx.data)
    for key in IN_B1:
        assert world.human_id(key) in text
    assert "Software Requirements Specification" in text and "deterministic" in text
    csv = service.export(world.project_id, rtm.id, ArtifactFormat.CSV).data.decode()
    assert csv.startswith("Requirement ID,Version") and world.human_id("L01") in csv
    with pytest.raises(ArtifactError, match="CSV"):
        service.export(world.project_id, srs.id, ArtifactFormat.CSV)
    exported = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.ARTIFACT_EXPORTED
    ]
    assert len(exported) == 3 and all(len(e.payload["sha256"]) == 64 for e in exported)


def test_artefact_versions_are_immutable(world, b1) -> None:
    version = generate(world, b1, ArtifactType.SRS).version
    with pytest.raises(ImmutableRecordError), world.session.begin_nested():
        version.markdown = "rewritten"
        world.session.flush()
    world.session.expire_all()
    version = world.session.get(ArtifactVersion, version.id)
    with pytest.raises(ImmutableRecordError), world.session.begin_nested():
        world.session.delete(version)
        world.session.flush()


def test_injected_text_is_rendered_as_data_everywhere(world, b1) -> None:
    service = ArtifactService(world.session, world.analyst)
    srs = generate(world, b1, ArtifactType.SRS).version
    assert "<script>" not in srs.markdown and "&lt;script&gt;" in srs.markdown
    assert "{{ config }}" in srs.markdown  # printed, never evaluated
    text = docx_text(service.export(world.project_id, srs.id, ArtifactFormat.DOCX).data)
    assert "<script>alert(1)</script>" in text  # a literal string in the Word file
    # The text asked to be approved; it was approved only through G1 like the rest,
    # and nothing about the document changed its approval record.
    l08 = world.version("L08")
    assert l08.state is RequirementState.BASELINED
