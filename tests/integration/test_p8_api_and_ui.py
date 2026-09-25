"""Governance, traceability and artefacts over HTTP and in the demonstration UI (P8).

The world is built through the owning services (the scripted P8 model; nothing
reaches a network) and committed; every assertion below then goes through the
HTTP API or the ``/ui`` pages exactly as a client would.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.api.dependencies import get_db
from reqpilot.domain.models import Base
from reqpilot.main import create_app

pytestmark = pytest.mark.integration


def hdr(actor) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"X-ReqPilot-Actor": str(actor.actor_id)}


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    def provide() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = provide
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class Setup:
    """Run service-level setup steps on a fresh session and commit them."""

    def __init__(self, session_factory) -> None:  # type: ignore[no-untyped-def]
        self.factory = session_factory

    def __call__(self, world: P8World, step) -> object:  # type: ignore[no-untyped-def]
        session = self.factory()
        world.session = session
        world.retriever.session = session
        try:
            result = step(world)
            session.commit()
            return result
        finally:
            session.close()


@pytest.fixture
def worlds(session_factory):  # type: ignore[no-untyped-def]
    session = session_factory()
    first = make_p8_world(session)
    other = make_p8_world(session, "Another lender (synthetic)")
    session.commit()
    session.close()
    return first, other, Setup(session_factory)


API = "/api/v1"


def test_the_review_queue_is_one_ordered_list(client, worlds) -> None:
    first, other, _setup = worlds
    response = client.get(
        f"{API}/projects/{first.project_id}/review-queue", headers=hdr(first.analyst)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "blocking" in body["ordering_rule"]
    entries = body["entries"]
    assert entries, "the analysed world has open review work"
    blocking = [e["blocking"] for e in entries]
    assert blocking == sorted(blocking, reverse=True), "blocking items come first"
    kinds = {e["kind"] for e in entries}
    assert "approval_task" in kinds
    # Another project's analyst sees a 404, not the queue.
    assert (
        client.get(
            f"{API}/projects/{first.project_id}/review-queue", headers=hdr(other.analyst)
        ).status_code
        == 404
    )


def test_readiness_names_the_blockers_and_fan_out_raises_but_never_decides(client, worlds) -> None:
    first, _other, _setup = worlds
    l05 = first.versions["L05"].id
    readiness = client.get(
        f"{API}/requirement-versions/{l05}/readiness", headers=hdr(first.analyst)
    ).json()
    assert readiness["ready"] is False
    assert "G5_REQUIRED" in {b["code"] for b in readiness["blockers"]}

    for who in (first.compliance_officer, first.project_manager, first.auditor, first.priya):
        forbidden = client.post(
            f"{API}/projects/{first.project_id}/governance/fan-out", headers=hdr(who)
        )
        assert forbidden.status_code == 403, who
    raised = client.post(
        f"{API}/projects/{first.project_id}/governance/fan-out", headers=hdr(first.analyst)
    )
    assert raised.status_code == 200, raised.text
    g5 = raised.json()["g5"]
    assert [t["required_role"] for t in g5] == ["project_manager"]
    assert all(t["status"] == "OPEN" for t in g5)
    again = client.post(
        f"{API}/projects/{first.project_id}/governance/fan-out", headers=hdr(first.analyst)
    ).json()
    assert again == {"g4": [], "g5": [], "g7": []}, "raising is idempotent"

    after = client.get(
        f"{API}/requirement-versions/{l05}/readiness", headers=hdr(first.analyst)
    ).json()
    assert "G5_PENDING" in {b["code"] for b in after["blockers"]}

    # The project manager decides it through the one approval path.
    decide = client.post(
        f"{API}/approval-tasks/{g5[0]['id']}/decide",
        json={"decision": "APPROVE", "role_exercised": "project_manager"},
        headers=hdr(first.project_manager),
    )
    assert decide.status_code == 200, decide.text
    final = client.get(
        f"{API}/requirement-versions/{l05}/readiness", headers=hdr(first.analyst)
    ).json()
    assert not {"G5_REQUIRED", "G5_PENDING"} & {b["code"] for b in final["blockers"]}


def test_the_analyst_flag_raises_g5_and_nobody_else_may(client, worlds) -> None:
    first, other, _setup = worlds
    l03 = first.versions["L03"].id
    path = f"{API}/requirement-versions/{l03}/architecture-flag"
    body = {"reason": "Integrates with the core banking ledger (synthetic)."}
    for who in (first.project_manager, first.compliance_officer, first.auditor):
        assert client.post(path, json=body, headers=hdr(who)).status_code == 403
    assert client.post(path, json=body, headers=hdr(other.analyst)).status_code == 404
    assert client.post(path, json={"reason": ""}, headers=hdr(first.analyst)).status_code == 422
    assert (
        client.post(path, json={**body, "severity": "low"}, headers=hdr(first.analyst)).status_code
        == 422
    )
    created = client.post(path, json=body, headers=hdr(first.analyst))
    assert created.status_code == 201, created.text
    assert created.json()["gate"] == "G5"
    assert created.json()["required_role"] == "project_manager"


def test_generation_from_an_unready_baseline_is_impossible_and_a_real_one_works(
    client, worlds
) -> None:
    first, other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L01", "L03", "L08"], "B1"))
    path = f"{API}/baselines/{baseline}/artifacts"

    for who in (first.compliance_officer, first.project_manager, first.auditor, first.priya):
        assert client.post(path, json={}, headers=hdr(who)).status_code == 403
    assert client.post(path, json={}, headers=hdr(other.analyst)).status_code == 404
    assert (
        client.post(
            path, json={"artifact_types": ["srs"], "approved": True}, headers=hdr(first.analyst)
        ).status_code
        == 422
    )

    response = client.post(
        path, json={"artifact_types": ["srs", "rtm", "risk_register"]}, headers=hdr(first.analyst)
    )
    assert response.status_code == 201, response.text
    outcomes = response.json()
    assert [o["artifact_type"] for o in outcomes] == ["srs", "rtm", "risk_register"]
    assert all(not o["refused"] for o in outcomes)
    srs = outcomes[0]["version"]
    assert srs["model_identifier"] == "deterministic" and srs["baseline_id"] == str(baseline)
    assert len(srs["content_hash"]) == 64

    # Identical content on the same baseline reuses the version.
    again = client.post(path, json={"artifact_types": ["srs"]}, headers=hdr(first.analyst))
    assert again.status_code == 201
    assert again.json()[0]["reused_identical_version"] is True
    assert again.json()[0]["version"]["id"] == srs["id"]

    listed = client.get(
        f"{API}/projects/{first.project_id}/artifacts", headers=hdr(first.auditor)
    ).json()
    assert {a["artifact_type"] for a in listed} == {"srs", "rtm", "risk_register"}
    srs_artifact = next(a for a in listed if a["artifact_type"] == "srs")
    history = client.get(
        f"{API}/artifacts/{srs_artifact['id']}/versions", headers=hdr(first.auditor)
    ).json()
    assert [v["version_no"] for v in history] == [1]

    detail = client.get(f"{API}/artifact-versions/{srs['id']}", headers=hdr(first.auditor))
    assert detail.status_code == 200
    sections = detail.json()["sections"]
    assert any(s["cites_requirement_versions"] for s in sections)
    cited = {c for s in sections for c in s["cites_requirement_versions"]}
    assert str(first.versions["L03"].id) in cited
    assert "<script>" not in detail.json()["markdown"]


def test_a_refused_generation_is_a_409_with_its_reasons(client, worlds) -> None:
    first, _other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L03"], "B1"))

    def ungoverned_project_risk(world: P8World) -> None:
        from reqpilot.domain.enums import RiskCategory, RiskImpact, RiskLikelihood
        from reqpilot.domain.models.risk import Risk
        from reqpilot.repositories.risk import RiskRepository
        from reqpilot.services.risk.service import RiskService

        anchor = next(
            r for r in world.session.scalars(select(Risk)) if r.project_id == world.project_id
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
            evidence_ids=list(
                RiskRepository(world.session, world.analyst).evidence_ids(
                    world.project_id, anchor.id
                )
            ),
        )

    setup(first, ungoverned_project_risk)
    path = f"{API}/baselines/{baseline}/artifacts"
    refused = client.post(
        path, json={"artifact_types": ["risk_register"]}, headers=hdr(first.analyst)
    )
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body[0]["refused"] is True and body[0]["version"] is None
    assert any("G8_UNREVIEWED" in b for b in body[0]["blockers"])
    # An artefact that does not present project risks is still generated: 201.
    mixed = client.post(
        path, json={"artifact_types": ["risk_register", "user_stories"]}, headers=hdr(first.analyst)
    )
    assert mixed.status_code == 201
    assert [o["refused"] for o in mixed.json()] == [True, False]
    # Unknown artefact types (P9/P10 reports) are not accepted at all.
    unknown = client.post(
        path, json={"artifact_types": ["sdlc_report"]}, headers=hdr(first.analyst)
    )
    assert unknown.status_code == 422


def test_exports_carry_hashes_and_safe_filenames(client, worlds) -> None:
    first, _other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L03", "L08"], "B1"))
    outcomes = client.post(
        f"{API}/baselines/{baseline}/artifacts",
        json={"artifact_types": ["srs", "rtm"]},
        headers=hdr(first.analyst),
    ).json()
    srs, rtm = (o["version"] for o in outcomes)

    md = client.get(
        f"{API}/artifact-versions/{srs['id']}/export?format=markdown", headers=hdr(first.auditor)
    )
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.headers["x-content-type-options"] == "nosniff"
    assert md.headers["content-disposition"].startswith('attachment; filename="')
    assert "<script>" not in md.text and "&lt;script&gt;" in md.text

    docx = client.get(
        f"{API}/artifact-versions/{srs['id']}/export?format=docx", headers=hdr(first.auditor)
    )
    assert docx.status_code == 200
    assert docx.headers["content-disposition"].endswith('.docx"')
    with zipfile.ZipFile(io.BytesIO(docx.content)) as package:
        assert "word/document.xml" in package.namelist()
        assert "<script>" not in package.read("word/document.xml").decode("utf-8")
    assert len(docx.headers["x-content-sha256"]) == 64

    rows = list(
        csv.DictReader(
            io.StringIO(
                client.get(
                    f"{API}/artifact-versions/{rtm['id']}/export?format=csv",
                    headers=hdr(first.auditor),
                ).text
            )
        )
    )
    assert len(rows) == 2
    # A CSV of a non-RTM artefact is not a thing.
    assert (
        client.get(
            f"{API}/artifact-versions/{srs['id']}/export?format=csv", headers=hdr(first.auditor)
        ).status_code
        == 409
    )
    by_number = client.get(
        f"{API}/artifacts/{srs['artifact_id']}/versions/1?format=docx", headers=hdr(first.auditor)
    )
    assert by_number.status_code == 200 and by_number.content == docx.content
    missing = client.get(
        f"{API}/artifacts/{srs['artifact_id']}/versions/9", headers=hdr(first.auditor)
    )
    assert missing.status_code == 404


def test_rtm_sync_coverage_and_links_over_http(client, worlds) -> None:
    first, other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L01", "L03"], "B1"))
    base = f"{API}/projects/{first.project_id}"
    assert client.post(f"{base}/traceability/sync", headers=hdr(first.auditor)).status_code == 403
    synced = client.post(f"{base}/traceability/sync", headers=hdr(first.analyst))
    assert synced.status_code == 200, synced.text
    assert synced.json()["created"] > 0
    assert (
        client.post(f"{base}/traceability/sync", headers=hdr(first.analyst)).json()["created"] == 0
    ), "materialising is idempotent"

    rtm = client.get(
        f"{base}/traceability?baseline_id={baseline}", headers=hdr(first.auditor)
    ).json()
    assert rtm["scope"].startswith("baseline")
    assert len(rtm["rows"]) == 2
    assert {"requirement_id", "sources", "approval", "risks"} <= set(rtm["columns"])
    csv_response = client.get(
        f"{base}/traceability?baseline_id={baseline}&format=csv", headers=hdr(first.auditor)
    )
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert 'filename="rtm.csv"' in csv_response.headers["content-disposition"]
    md_response = client.get(
        f"{base}/traceability?baseline_id={baseline}&format=md", headers=hdr(first.auditor)
    )
    assert md_response.text.startswith("| ")
    assert client.get(
        f"{base}/traceability?format=pdf", headers=hdr(first.auditor)
    ).status_code == (422)

    coverage = client.get(
        f"{base}/traceability/coverage?baseline_id={baseline}", headers=hdr(first.auditor)
    ).json()
    assert coverage["definition_version"] == "N.3-v1"
    assert coverage["total"] == 2
    assert coverage["e6"] == coverage["fully_traced"] / coverage["total"]

    links = client.get(
        f"{API}/requirement-versions/{first.versions['L01'].id}/trace-links",
        headers=hdr(first.auditor),
    ).json()
    assert {"SOURCES", "CLASSIFIED_AS", "APPROVED_BY"} <= {link["link_type"] for link in links}

    for path in (
        f"{base}/traceability",
        f"{base}/traceability/coverage",
        f"{API}/requirement-versions/{first.versions['L01'].id}/trace-links",
        f"{API}/requirement-versions/{first.versions['L01'].id}/readiness",
    ):
        assert client.get(path, headers=hdr(other.analyst)).status_code == 404, path


def test_nothing_crosses_projects(client, worlds) -> None:
    first, other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L03"], "B1"))
    srs = client.post(
        f"{API}/baselines/{baseline}/artifacts",
        json={"artifact_types": ["srs"]},
        headers=hdr(first.analyst),
    ).json()[0]["version"]
    for path in (
        f"{API}/projects/{first.project_id}/artifacts",
        f"{API}/artifacts/{srs['artifact_id']}/versions",
        f"{API}/artifact-versions/{srs['id']}",
        f"{API}/artifact-versions/{srs['id']}/export?format=docx",
        f"{API}/artifacts/{srs['artifact_id']}/versions/1",
        f"/ui/artifact-versions/{srs['id']}",
        f"/ui/projects/{first.project_id}/artifacts",
        f"/ui/projects/{first.project_id}/traceability",
        f"/ui/projects/{first.project_id}/governance",
    ):
        assert client.get(path, headers=hdr(other.analyst)).status_code in (403, 404), path
    assert (
        client.post(
            f"{API}/baselines/{baseline}/artifacts", json={}, headers=hdr(other.analyst)
        ).status_code
        == 404
    )


def test_the_ui_pages_render_and_escape(client, worlds) -> None:
    first, _other, setup = worlds
    baseline = setup(first, lambda w: w.govern_and_baseline(["L03", "L08"], "B1"))
    ui = f"/ui/projects/{first.project_id}"
    governance = client.get(f"{ui}/governance", headers=hdr(first.analyst))
    assert governance.status_code == 200
    assert "review queue" in governance.text.lower()

    generated = client.post(
        f"/ui/baselines/{baseline}/artifacts",
        data={"artifact_types": ["srs", "rtm"]},
        headers=hdr(first.analyst),
    )
    assert generated.status_code == 200, generated.text
    page = client.get(f"{ui}/artifacts", headers=hdr(first.auditor))
    assert page.status_code == 200 and "srs" in page.text.lower()

    versions = client.get(
        f"{API}/projects/{first.project_id}/artifacts", headers=hdr(first.auditor)
    ).json()
    srs = next(a for a in versions if a["artifact_type"] == "srs")
    srs_version = client.get(
        f"{API}/artifacts/{srs['id']}/versions", headers=hdr(first.auditor)
    ).json()[0]
    detail = client.get(f"/ui/artifact-versions/{srs_version['id']}", headers=hdr(first.auditor))
    assert detail.status_code == 200
    assert "<script>alert(1)</script>" not in detail.text
    assert srs_version["content_hash"] in detail.text
    download = client.get(
        f"/ui/artifact-versions/{srs_version['id']}/download?format=docx",
        headers=hdr(first.auditor),
    )
    assert download.status_code == 200 and download.content[:2] == b"PK"

    trace = client.get(f"{ui}/traceability?baseline_id={baseline}", headers=hdr(first.auditor))
    assert trace.status_code == 200
    assert "<script>alert(1)</script>" not in trace.text
    # The auditor sees no generate or sync button.
    assert "traceability/sync" not in trace.text
