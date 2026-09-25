"""The typed trace graph (FR-TRC-001..004): materialised from persisted facts, typed
and allowlisted, append-only, version-preserving; the RTM and coverage read from it."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.domain.enums import RequirementCategory
from reqpilot.domain.errors import (
    ImmutableRecordError,
    ProjectIsolationError,
    TraceabilityError,
)
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.traceability import ALLOWED_TRIPLES, TraceLinkType, TraceNodeType, is_allowed
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.traceability import (
    NOT_LINKED,
    CoverageService,
    Edge,
    RtmBuilder,
    ScopeService,
    TraceGraphSync,
    TraceQueryService,
    rtm_csv,
)

pytestmark = pytest.mark.integration

N, L = TraceNodeType, TraceLinkType


@pytest.fixture
def world(db_session: Session) -> P8World:
    return make_p8_world(db_session)


def sync(world: P8World):  # type: ignore[no-untyped-def]
    return TraceGraphSync(world.session, world.analyst).sync(world.project_id)


def links(world: P8World) -> list[TraceabilityLink]:
    return list(
        world.session.scalars(
            select(TraceabilityLink).where(TraceabilityLink.project_id == world.project_id)
        )
    )


def test_the_sync_derives_typed_allowlisted_links_from_persisted_facts(world) -> None:
    result = sync(world)
    assert result.created > 0
    rows = links(world)
    for row in rows:
        assert is_allowed(row.from_type, row.link_type, row.to_type), row
        assert row.origin and row.project_id == world.project_id
    kinds = {row.link_type for row in rows}
    for expected in (
        "SOURCES",
        "CLASSIFIED_AS",
        "HAS_MAPPING",
        "EVIDENCED_BY",
        "HAS_SECURITY_FINDING",
        "HAS_RISK",
        "RISK_ASSESSED_BY",
        "MITIGATED_BY",
        "SATISFIED_BY",
        "HAS_CONFLICT",
        "CONFLICTS_WITH",
        "MAPPED_TO",
        "DRAWN_FROM",
        "ISSUED_UNDER",
    ):
        assert expected in kinds, expected
    # Every requirement version traces to its stakeholder input (a transcript chunk).
    graph = TraceQueryService(world.session, world.analyst).graph(world.project_id)
    for key in world.versions:
        sources = graph.incoming(N.REQUIREMENT_VERSION, world.versions[key].id, L.SOURCES)
        assert sources and all(s.from_type == "source_chunk" for s in sources), key


def test_the_sync_is_idempotent_and_audited_by_reference(world) -> None:
    first = sync(world)
    second = sync(world)
    assert first.created > 0 and second.created == 0
    assert second.already_present == first.created + first.already_present
    from reqpilot.domain.enums import AuditEventType
    from reqpilot.domain.models.audit import AuditEvent

    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.TRACE_LINKS_SYNCED
    ]
    assert len(events) == 1 and events[0].payload["created"] == first.created


def test_links_are_append_only(world) -> None:
    sync(world)
    row = links(world)[0]
    with pytest.raises(ImmutableRecordError), world.session.begin_nested():
        row.to_id = "tampered"
        world.session.flush()
    world.session.expire_all()
    row = links(world)[0]
    with pytest.raises(ImmutableRecordError), world.session.begin_nested():
        world.session.delete(row)
        world.session.flush()


def test_a_link_outside_the_allowlist_is_refused_by_code_and_database(world) -> None:
    bad = Edge(
        N.RISK, str(uuid.uuid4()), L.SOURCES, N.REQUIREMENT_VERSION, str(uuid.uuid4()), None, "x"
    )
    with pytest.raises(TraceabilityError):
        TraceGraphSync(world.session, world.analyst).record(world.project_id, [bad])
    # The database CHECK refuses it too, whatever writes it.
    with pytest.raises(IntegrityError):
        world.session.execute(
            text(
                "INSERT INTO traceability_link (id, project_id, from_type, from_id, link_type, "
                "to_type, to_id, origin, created_at) VALUES (:id, :p, 'risk', 'a', 'SOURCES', "
                "'requirement_version', 'b', 'x', CURRENT_TIMESTAMP)"
            ),
            {"id": uuid.uuid4().hex, "p": world.project_id.hex},
        )
    world.session.rollback()


def test_trace_links_are_preserved_across_versions(world) -> None:
    """FR-TRC-004: v1's graph stays reconstructable after v2 exists."""
    sync(world)
    v1 = world.version("L03")
    before = {
        (r.link_type, r.to_id)
        for r in TraceQueryService(world.session, world.analyst).version_links(
            world.project_id, v1.id
        )
    }
    assert before
    v2 = RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=v1.requirement_id,
        content=RequirementContent(
            statement="The system shall display the current loan status and its date "
            "to the applicant.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=tuple(v1.source_refs),
        ),
        change_reason="the date was requested",
    )
    sync(world)
    query = TraceQueryService(world.session, world.analyst)
    after_v1 = {(r.link_type, r.to_id) for r in query.version_links(world.project_id, v1.id)}
    v2_links = query.version_links(world.project_id, v2.id)
    assert after_v1 == before, "the predecessor's graph is not rewritten"
    assert {r.link_type for r in v2_links} >= {"SOURCES"}  # stable relation carried
    assert not any(r.link_type == "CLASSIFIED_AS" for r in v2_links)  # analysis recomputed later


def test_coverage_reports_what_is_missing_and_never_fabricates(world) -> None:
    sync(world)
    scope = ScopeService(world.session, world.analyst).project_scope(world.project_id)
    report = CoverageService(world.session, world.analyst).report(scope)
    assert report.total == len(world.versions)
    assert report.counts["with_source"] == report.total
    assert report.counts["with_classification"] == report.total
    # Nothing is approved or baselined yet, so those elements are not required.
    assert report.counts["approved"] == 0
    assert report.e6 is not None and 0.0 <= report.e6 <= 1.0
    assert report.fully_traced == sum(1 for v in report.versions if not v.missing)


def test_orphans_and_unsourced_statements_are_flagged(world) -> None:
    _requirement, version = RequirementService(world.session, world.analyst).create_requirement(
        project_id=world.project_id,
        domain="LOAN",
        content=RequirementContent(
            statement="The system shall print a paper statement.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=({"kind": "utterance", "ref": "interview-1"},),
        ),
    )
    sync(world)
    scope = ScopeService(world.session, world.analyst).project_scope(world.project_id)
    report = CoverageService(world.session, world.analyst).report(scope)
    label = next(v for v in report.versions if v.version_id == version.id)
    assert not label.has_source and not label.fully_traced
    assert any(label.human_id in o for o in report.orphan_requirements)
    assert any(label.human_id in u for u in report.unsourced_statements)


def test_an_empty_scope_has_no_e6_rather_than_a_perfect_one(world, db_session) -> None:
    from tests.workflow.test_p1_exit_test import make_member, make_project

    from reqpilot.domain.enums import Role
    from reqpilot.domain.ids import ProjectId

    project = make_project(db_session, "Empty (synthetic)")
    analyst = make_member(db_session, project, Role.ANALYST, "empty@example.test")
    scope = ScopeService(db_session, analyst).project_scope(ProjectId(project.id))
    report = CoverageService(db_session, analyst).report(scope)
    assert report.total == 0 and report.e6 is None


def test_the_rtm_is_read_from_the_graph_and_says_what_is_not_linked(world) -> None:
    sync(world)
    scope = ScopeService(world.session, world.analyst).project_scope(world.project_id)
    graph = TraceQueryService(world.session, world.analyst).graph(world.project_id)
    rows = {r.version_id: r for r in RtmBuilder(world.session, world.analyst).rows(scope, graph)}
    l01 = rows[world.versions["L01"].id]
    assert "LO-RET-APPLICATION-RECORDS" in l01.get("compliance")
    assert "candidate" in l01.get("compliance")
    assert "evidence" in l01.get("evidence")
    l03 = rows[world.versions["L03"].id]
    assert l03.get("compliance") == NOT_LINKED
    assert l03.get("approval") == NOT_LINKED and l03.get("baseline") == NOT_LINKED
    assert "AC1" in l03.get("acceptance_criteria")
    csv_text = rtm_csv(list(rows.values()))
    assert csv_text.splitlines()[0].startswith("Requirement ID,Version")
    assert len(csv_text.strip().splitlines()) >= len(rows) + 1
    # Without a sync the RTM claims nothing.
    fresh = make_p8_world(world.session, "Unsynced (synthetic)")
    scope = ScopeService(world.session, fresh.analyst).project_scope(fresh.project_id)
    graph = TraceQueryService(world.session, fresh.analyst).graph(fresh.project_id)
    for row in RtmBuilder(world.session, fresh.analyst).rows(scope, graph):
        assert row.get("sources") == NOT_LINKED and row.get("risks") == NOT_LINKED


def test_trace_graphs_are_isolated_per_project(world, db_session) -> None:
    other = make_p8_world(db_session, "Another bank (synthetic)")
    sync(world)
    sync(other)
    mine = {r.id for r in links(world)}
    theirs = {r.id for r in links(other)}
    assert mine and theirs and not mine & theirs
    for action in (
        lambda: TraceGraphSync(db_session, other.analyst).sync(world.project_id),
        lambda: TraceQueryService(db_session, other.analyst).graph(world.project_id),
        lambda: TraceQueryService(db_session, other.analyst).version_links(
            world.project_id, world.versions["L01"].id
        ),
    ):
        with pytest.raises(ProjectIsolationError):
            action()


def test_every_n2_row_p8_produces_is_in_the_allowlist(world) -> None:
    sync(world)
    produced = {(r.from_type, r.link_type, r.to_type) for r in links(world)}
    allowed = {(str(f), str(link), str(t)) for (f, link, t) in ALLOWED_TRIPLES}
    assert produced <= allowed


def test_an_interview_answer_traces_to_its_stakeholder(world) -> None:
    """P4 input: utterance SOURCES the version, and the stakeholder STATED the utterance."""
    from reqpilot.domain.enums import (
        DataSensitivity,
        InterviewSessionKind,
        InterviewSessionStatus,
        SpeakerKind,
    )
    from reqpilot.domain.models.elicitation import InterviewSession, Stakeholder, Utterance

    stakeholder = world.session.scalars(
        select(Stakeholder).where(Stakeholder.project_id == world.project_id)
    ).first()
    session_row = InterviewSession(
        project_id=world.project_id,
        stakeholder_id=stakeholder.id,
        kind=InterviewSessionKind.CLARIFICATION,
        sensitivity=DataSensitivity.SYNTHETIC,
        status=InterviewSessionStatus.COMPLETED,
        started_by=world.analyst.actor_id,
    )
    world.session.add(session_row)
    world.session.flush()
    answer = Utterance(
        project_id=world.project_id,
        session_id=session_row.id,
        seq=1,
        speaker_kind=SpeakerKind.STAKEHOLDER,
        speaker_ref=stakeholder.id,
        recorded_by=world.analyst.actor_id,
        on_behalf=True,
        text="Applicants must be told when a document is missing.",
    )
    world.session.add(answer)
    world.session.flush()
    _requirement, version = RequirementService(world.session, world.analyst).create_requirement(
        project_id=world.project_id,
        domain="LOAN",
        content=RequirementContent(
            statement="The system shall tell the applicant when a document is missing.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=(
                {"kind": "utterance", "ref": str(answer.id), "session": str(session_row.id)},
            ),
        ),
    )
    sync(world)
    graph = TraceQueryService(world.session, world.analyst).graph(world.project_id)
    assert graph.sources(N.REQUIREMENT_VERSION, version.id, L.SOURCES) == [str(answer.id)]
    assert graph.sources(N.UTTERANCE, answer.id, L.STATED) == [str(stakeholder.id)]
