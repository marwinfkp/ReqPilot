"""The SDLC recommendation over HTTP and in the demonstration UI (P9).

The world is built through the owning services (the scripted P9 model; nothing
reaches a network) and committed; every assertion below then goes through the
HTTP API or the ``/ui`` pages exactly as a client would. The app's gateway is
the world's scripted gateway, so the role #10 calls are answered offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p9_helpers import P9World, consistent_explanation, make_p9_world, ranking_of

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
def worlds(engine: Engine) -> tuple[P9World, P9World]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    first = make_p9_world(session)
    other = make_p9_world(session, "Another lender (synthetic)")
    session.commit()
    session.close()
    return first, other


@pytest.fixture
def client(engine: Engine, worlds: tuple[P9World, P9World]) -> Iterator[TestClient]:
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
    app.dependency_overrides[get_llm_gateway] = lambda: worlds[0].p8.gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def start(client: TestClient, world: P9World, actor=None):  # type: ignore[no-untyped-def]
    return client.post(
        f"{API}/projects/{world.project_id}/sdlc-runs",
        json={"baseline_id": str(world.baseline_id)},
        headers=hdr(actor or world.p8.analyst),
    )


def test_start_view_and_decide_g6_over_http(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other = worlds
    response = start(client, world)
    assert response.status_code == 201, response.text
    started = response.json()
    assert started["status"] == "completed" and started["explanation_status"] == "generated"
    assert len(started["g6_task_ids"]) == 4
    run_id = started["sdlc_run_id"]

    body = client.get(f"{API}/sdlc-runs/{run_id}", headers=hdr(world.architect)).json()
    assert "advisory" in body["notice"] and "G6" in body["notice"]
    assert len(body["factors"]) == 13 and len(body["candidates"]) == 7
    first = body["candidates"][0]
    assert first["candidate_key"] == body["top_candidate"] and first["rank"] == 1
    assert body["explanation"]["status"] == "generated"
    assert body["explanation"]["discrepancies"] == []
    assert body["g6_status"].startswith("open (0/4")
    factor = body["factors"][0]
    assert {"evidence_refs", "evidence_state", "source", "derived_score", "proposal_status"} <= set(
        factor
    )
    assert body["ruleset_ref"].startswith("sdlc_rules@1.0.0#")
    listed = client.get(
        f"{API}/projects/{world.project_id}/sdlc-runs", headers=hdr(world.p8.auditor)
    ).json()
    assert [r["id"] for r in listed] == [run_id]

    # Another project's people get a 404, not the run.
    assert client.get(f"{API}/sdlc-runs/{run_id}", headers=hdr(other.p8.analyst)).status_code == 404
    # G6 is decided only through the one approval path, each role its own task.
    roles = {t["required_role"]: t["id"] for t in body["g6_tasks"]}
    deciders = {
        "project_manager": world.p8.project_manager,
        "architect": world.architect,
        "security_reviewer": world.p8.security_reviewer,
        "compliance_officer": world.p8.compliance_officer,
    }
    for role, task_id in roles.items():
        decided = client.post(
            f"{API}/approval-tasks/{task_id}/decide",
            json={"decision": "APPROVE", "role_exercised": role},
            headers=hdr(deciders[role]),
        )
        assert decided.status_code == 200, decided.text
    after = client.get(f"{API}/sdlc-runs/{run_id}", headers=hdr(world.p8.analyst)).json()
    assert after["status"] == "selected"
    assert after["selected_candidate"] == after["top_candidate"]
    assert after["g6_status"].startswith("passed (4/4")


def test_start_and_override_refusals_over_http(client: TestClient, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other = worlds
    for who in (world.p8.project_manager, world.p8.auditor, world.architect):
        assert start(client, world, who).status_code == 403, who
    assert start(client, world, other.p8.analyst).status_code == 404
    run_id = start(client, world).json()["sdlc_run_id"]
    url = f"{API}/sdlc-runs/{run_id}/factors/system_size/override"
    no_reason = client.post(
        url, json={"score": 4, "reason": "", "role": "analyst"}, headers=hdr(world.p8.analyst)
    )
    assert no_reason.status_code == 422
    bad_score = client.post(
        url, json={"score": 7, "reason": "r", "role": "analyst"}, headers=hdr(world.p8.analyst)
    )
    assert bad_score.status_code == 422
    auditor = client.post(
        url, json={"score": 4, "reason": "r", "role": "auditor"}, headers=hdr(world.p8.auditor)
    )
    assert auditor.status_code == 403
    done = client.post(
        url,
        json={"score": 4, "reason": "Two more channels (synthetic).", "role": "project_manager"},
        headers=hdr(world.p8.project_manager),
    )
    assert done.status_code == 201, done.text
    new_id = done.json()["sdlc_run_id"]
    assert new_id != run_id
    new = client.get(f"{API}/sdlc-runs/{new_id}", headers=hdr(world.p8.analyst)).json()
    assert new["supersedes_run_id"] == run_id
    size = next(f for f in new["factors"] if f["factor_id"] == "system_size")
    assert size["is_overridden"] and size["override_role"] == "project_manager"
    stale = client.post(
        url, json={"score": 5, "reason": "again", "role": "analyst"}, headers=hdr(world.p8.analyst)
    )
    assert stale.status_code == 409, "the old run has been superseded"
    retry = client.post(f"{API}/sdlc-runs/{new_id}/explanation", headers=hdr(world.p8.analyst))
    assert retry.status_code == 409, "an explained run is not re-explained"


def test_the_ui_shows_the_profile_ranking_explanation_and_discrepancy(
    client: TestClient, worlds
) -> None:  # type: ignore[no-untyped-def]
    world, _other = worlds

    def wrong_top(request):  # type: ignore[no-untyped-def]
        body = consistent_explanation(request)
        body["asserted_top_candidate"] = ranking_of(request)[1][0]
        return json.dumps(body)

    world.model.overrides["sdlc_explanation"] = wrong_top
    page = client.get(f"/ui/projects/{world.project_id}/sdlc", headers=hdr(world.p8.analyst))
    assert page.status_code == 200 and "Compute recommendation" in page.text
    posted = client.post(
        f"/ui/projects/{world.project_id}/sdlc-runs",
        data={"baseline_id": str(world.baseline_id)},
        headers=hdr(world.p8.analyst),
        follow_redirects=False,
    )
    assert posted.status_code == 303
    detail = client.get(posted.headers["location"], headers=hdr(world.p8.project_manager))
    assert detail.status_code == 200
    html = detail.text
    assert "AI-generated, advisory" in html
    assert "Discrepancy." in html and "TOP_MISMATCH" in html
    assert "Factor profile" in html and "requirement_stability" in html
    assert "What would reverse the ranking" in html
    assert "request revision" in html, "the project manager sees a G6 decision form"
    run_path = posted.headers["location"]
    override = client.post(
        f"{run_path}/factors/system_size/override",
        data={"score": "4", "reason": "Scope grows (synthetic).", "role": "project_manager"},
        headers=hdr(world.p8.project_manager),
        follow_redirects=False,
    )
    assert override.status_code == 303 and override.headers["location"] != run_path
    tasks = client.get(f"/ui/projects/{world.project_id}/tasks", headers=hdr(world.architect))
    assert tasks.status_code == 200 and "SDLC recommendation" in tasks.text
