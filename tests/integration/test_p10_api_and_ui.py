"""The project workflow over HTTP and in the demonstration UI (P10).

The high-regulation world is built through the owning services up to a G6
selection and committed; a second project (a P9 world with a run still awaiting
G6) stands beside it. Every assertion then goes through the HTTP API or the
``/ui`` pages exactly as a client would.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p9_helpers import P9World, make_p9_world
from tests.p10_helpers import P10World, make_p10_world

from reqpilot.api.dependencies import get_db, get_llm_gateway
from reqpilot.domain.models import Base
from reqpilot.main import create_app

pytestmark = pytest.mark.integration

API = "/api/v1"


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
def worlds(engine: Engine) -> tuple[P10World, P9World, str, str]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    world = make_p10_world(session)
    run = world.select()
    other = make_p9_world(session, "Another lender (synthetic)")
    pending = other.start()
    session.commit()
    session.close()
    return world, other, str(run.id), str(pending.sdlc_run_id)


@pytest.fixture
def client(engine: Engine, worlds) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def provide() -> Iterator[Session]:
        session = factory()
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
    app.dependency_overrides[get_llm_gateway] = lambda: worlds[0].p9.p8.gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def generate(client: TestClient, run_id: str, actor) -> dict:  # type: ignore[no-untyped-def,type-arg]
    response = client.post(f"{API}/sdlc-runs/{run_id}/workflow", headers=hdr(actor))
    return {**response.json(), "http": response.status_code}


def test_generate_read_edit_and_export_over_http(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    world, _other, run_id, _pending = worlds
    created = generate(client, run_id, world.analyst)
    assert created["http"] == 201 and created["workflow_id"], created
    again = generate(client, run_id, world.analyst)
    assert (
        again["http"] == 200 and again["reused"] and again["workflow_id"] == created["workflow_id"]
    )

    body = client.get(f"{API}/sdlc-runs/{run_id}/workflow", headers=hdr(world.manager)).json()
    assert body["id"] == created["workflow_id"] and "not ReqPilot gates G1-G8" in body["notice"]
    assert [p["position"] for p in body["phases"]] == list(range(1, len(body["phases"]) + 1))
    gates = [g for p in body["phases"] for g in p["gates"]]
    assert all(g["is_reqpilot_gate"] is False for g in gates)
    assert [g["kind"] for g in gates].count("production_readiness") == 1
    for gate in (g for g in gates if g["kind"] == "compliance_checkpoint"):
        assert gate["sources"] and all(
            s["source_type"] == "compliance_mapping" for s in gate["sources"]
        )
    assert body["realises"][0]["source_type"] == "sdlc_candidate"
    assert any(f["code"] == "COMPLIANCE_GAP_OPEN" for f in body["open_items"])

    workflow_id = body["id"]
    phase = body["phases"][0]
    edited = client.patch(
        f"{API}/workflows/{workflow_id}/phases/{phase['id']}",
        json={
            "reason": "Clearer wording (synthetic).",
            "name": "Requirements analysis and sign-off",
        },
        headers=hdr(world.manager),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2 and edited.json()["role_exercised"] == "project_manager"
    log = client.get(f"{API}/workflows/{workflow_id}/changes", headers=hdr(world.analyst)).json()
    assert [c["revision"] for c in log] == [2]

    md = client.get(
        f"{API}/workflows/{workflow_id}/export?format=markdown", headers=hdr(world.analyst)
    )
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert (
        "Requirements analysis and sign-off" in md.text and "X-Content-SHA256".lower() in md.headers
    )
    docx = client.get(
        f"{API}/workflows/{workflow_id}/export?format=docx", headers=hdr(world.p9.architect)
    )
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert 'filename="workflow-' in docx.headers["content-disposition"]


def test_a_refusal_is_a_409_with_its_findings(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    _world, other, _run, pending = worlds
    refused = generate(client, pending, other.p8.analyst)
    assert refused["http"] == 409 and refused["workflow_id"] is None
    assert [f["code"] for f in refused["findings"]] == ["G6_NOT_PASSED"]


def test_edits_are_the_project_managers_and_cannot_forge_anything(
    client: TestClient, worlds
) -> None:  # type: ignore[no-untyped-def]
    world, _other, run_id, _pending = worlds
    workflow_id = generate(client, run_id, world.analyst)["workflow_id"]
    body = client.get(f"{API}/workflows/{workflow_id}", headers=hdr(world.manager)).json()
    phase = body["phases"][0]
    url = f"{API}/workflows/{workflow_id}/phases/{phase['id']}"
    ok = {"reason": "r (synthetic)", "name": "Renamed"}
    assert client.patch(url, json=ok, headers=hdr(world.analyst)).status_code == 403
    assert (
        client.patch(url, json={"name": "no reason"}, headers=hdr(world.manager)).status_code == 422
    )
    for forged in (
        {"sources": [{"source_type": "risk", "source_id": "x"}]},
        {"mandatory": False},
        {"kind": "manual"},
        {"key": "other"},
        {"project_id": str(world.project_id)},
        {"stages": ["release"]},
    ):
        response = client.patch(url, json={**ok, **forged}, headers=hdr(world.manager))
        assert response.status_code == 422, forged

    treatment = next(
        a for p in body["phases"] for a in p["activities"] if a["kind"] == "risk_treatment"
    )
    refused = client.post(
        f"{API}/workflows/{workflow_id}/activities/{treatment['id']}/remove",
        json={"reason": "try (synthetic)"},
        headers=hdr(world.manager),
    )
    assert refused.status_code == 409
    assert [f["code"] for f in refused.json()["findings"]] == ["EDIT_REMOVES_MANDATORY"]

    added = client.post(
        f"{API}/workflows/{workflow_id}/phases/{phase['id']}/activities",
        json={
            "reason": "Regulator request (synthetic).",
            "name": "Regulator walkthrough",
            "responsible_roles": ["compliance_officer"],
            "deliverables": ["Minutes"],
        },
        headers=hdr(world.manager),
    )
    assert added.status_code == 201, added.text
    unknown_role = client.post(
        f"{API}/workflows/{workflow_id}/phases/{phase['id']}/activities",
        json={"reason": "r", "name": "x", "responsible_roles": ["wizard"], "deliverables": ["y"]},
        headers=hdr(world.manager),
    )
    assert unknown_role.status_code == 409 and "UNKNOWN_ROLE" in unknown_role.text


def test_another_projects_workflow_does_not_exist_for_you(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other, run_id, _pending = worlds
    workflow_id = generate(client, run_id, world.analyst)["workflow_id"]
    stranger = other.p8.analyst
    assert client.get(f"{API}/workflows/{workflow_id}", headers=hdr(stranger)).status_code == 404
    assert (
        client.get(f"{API}/sdlc-runs/{run_id}/workflow", headers=hdr(stranger)).status_code == 404
    )
    assert (
        client.get(f"{API}/workflows/{workflow_id}/export", headers=hdr(stranger)).status_code
        == 404
    )
    assert generate(client, run_id, stranger)["http"] == 404
    body = client.get(f"{API}/workflows/{workflow_id}", headers=hdr(world.manager)).json()
    phase_id = body["phases"][0]["id"]
    patched = client.patch(
        f"{API}/workflows/{workflow_id}/phases/{phase_id}",
        json={"reason": "r", "name": "x"},
        headers=hdr(other.p8.project_manager),
    )
    assert patched.status_code == 404
    # A stakeholder of the same project holds no workflow read.
    assert (
        client.get(f"{API}/workflows/{workflow_id}", headers=hdr(world.p9.p8.priya)).status_code
        == 404
    )


def test_the_ui_generates_shows_edits_and_labels_the_gates(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    world, _other, run_id, _pending = worlds
    page = client.get(f"/ui/projects/{world.project_id}/workflows", headers=hdr(world.analyst))
    assert page.status_code == 200 and f"/ui/sdlc-runs/{run_id}/workflow" in page.text
    posted = client.post(
        f"/ui/sdlc-runs/{run_id}/workflow", headers=hdr(world.analyst), follow_redirects=False
    )
    assert posted.status_code == 303 and posted.headers["location"].startswith("/ui/workflows/")
    location = posted.headers["location"]

    as_manager = client.get(location, headers=hdr(world.manager))
    assert as_manager.status_code == 200
    assert (
        "not ReqPilot gates" in as_manager.text
        and "Production-readiness approval" in as_manager.text
    )
    assert 'name="reason"' in as_manager.text, "the Project Manager sees the edit forms"
    as_analyst = client.get(location, headers=hdr(world.analyst))
    assert 'name="reason"' not in as_analyst.text and "export Markdown" in as_analyst.text

    workflow_id = location.rsplit("/", 1)[-1]
    body = client.get(f"{API}/workflows/{workflow_id}", headers=hdr(world.manager)).json()
    phase_id = body["phases"][0]["id"]
    no_reason = client.post(
        f"/ui/workflows/{workflow_id}/phase/{phase_id}/edit",
        data={"field": "name", "value": "Renamed", "reason": ""},
        headers=hdr(world.manager),
        follow_redirects=False,
    )
    assert no_reason.status_code == 303 and "EDIT_REASON_REQUIRED" in no_reason.headers["location"]
    saved = client.post(
        f"/ui/workflows/{workflow_id}/phase/{phase_id}/edit",
        data={"field": "exit_criteria", "value": "One\nTwo", "reason": "Split (synthetic)."},
        headers=hdr(world.manager),
        follow_redirects=False,
    )
    assert saved.status_code == 303 and "error" not in saved.headers["location"]
    log = client.get(f"{API}/workflows/{workflow_id}/changes", headers=hdr(world.manager)).json()
    assert log[0]["changes"]["exit_criteria"]["after"] == ["One", "Two"]
