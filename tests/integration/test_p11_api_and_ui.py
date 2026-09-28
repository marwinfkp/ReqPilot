"""P11 over HTTP and in the demonstration UI: sessions, deletion, the audit viewer, replay.

Two synthetic P8 worlds are built through the owning services and committed;
every assertion then goes through the API or the ``/ui`` pages as a client would.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.api.dependencies import get_db, get_llm_gateway
from reqpilot.domain.models import Base
from reqpilot.domain.models.guardrails import AuthSession
from reqpilot.domain.models.identity import User
from reqpilot.main import create_app
from reqpilot.repositories.risk import RiskRepository

pytestmark = pytest.mark.integration

API = "/api/v1"
NAME = "P8 loan origination (synthetic)"


def hdr(actor) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"X-ReqPilot-Actor": str(actor.actor_id)}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
def worlds(engine: Engine) -> tuple[P8World, P8World, dict[str, str]]:
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    world = make_p8_world(session)
    world.govern_and_baseline(["L01", "L03"], "B1")
    other = make_p8_world(session, "Another lender (synthetic)")
    risk = next(
        r
        for r in RiskRepository(session, world.analyst).list_for_project(world.project_id)
        if str(r.severity) == "high"
    )
    ids = {
        "requirement": str(world.versions["L03"].requirement_id),
        "risk": str(risk.id),
    }
    session.commit()
    session.close()
    return world, other, ids


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
    app.dependency_overrides[get_llm_gateway] = lambda: worlds[0].gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def open_session(client: TestClient, actor) -> str:  # type: ignore[no-untyped-def]
    response = client.post(f"{API}/auth/sessions", headers=hdr(actor))
    assert response.status_code == 201, response.text
    return response.json()["token"]


# --- sessions (ADR-009) -------------------------------------------------------------------


def test_a_session_is_resolved_server_side_and_never_falls_back(client, worlds, engine) -> None:  # type: ignore[no-untyped-def]
    world, other, _ids = worlds
    response = client.post(f"{API}/auth/sessions", headers=hdr(world.analyst))
    assert response.status_code == 201
    token = response.json()["token"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie

    fresh = TestClient(client.app)  # no cookie jar carried over
    me = fresh.get(f"{API}/auth/whoami", headers=bearer(token)).json()
    assert me["user_id"] == str(world.analyst.actor_id) and me["authenticated_by"] == "session"
    assert me["roles_by_project"] == {str(world.project_id): ["analyst"]}

    # A session and a different identity claim: refused, not resolved either way.
    mixed = {**bearer(token), **hdr(other.project_manager)}
    assert fresh.get(f"{API}/auth/whoami", headers=mixed).status_code == 401
    # An invalid token is a 401 even alongside a perfectly valid identity claim.
    bad = {"Authorization": "Bearer not-a-real-session", **hdr(world.analyst)}
    assert fresh.get(f"{API}/auth/whoami", headers=bad).status_code == 401

    # Revoked, then expired, then an inactive user: each refused.
    assert fresh.delete(f"{API}/auth/sessions/current", headers=bearer(token)).status_code == 204
    assert fresh.get(f"{API}/auth/whoami", headers=bearer(token)).status_code == 401
    second = open_session(fresh, world.analyst)
    with Session(engine) as s:
        s.execute(
            update(AuthSession).values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1))
        )
        s.commit()
    assert fresh.get(f"{API}/auth/whoami", headers=bearer(second)).status_code == 401
    # The expired session's cookie is still in the jar - and is refused rather than
    # ignored - so a new session is opened from a clean client.
    assert fresh.post(f"{API}/auth/sessions", headers=hdr(world.analyst)).status_code == 401
    fresh.cookies.clear()
    third = open_session(fresh, world.analyst)
    with Session(engine) as s:
        s.execute(update(User).where(User.id == world.analyst.actor_id).values(is_active=False))
        s.commit()
    assert fresh.get(f"{API}/auth/whoami", headers=bearer(third)).status_code == 401
    assert fresh.get(f"{API}/auth/whoami", headers=hdr(world.analyst)).status_code == 401
    with Session(engine) as s:
        stored = s.scalars(select(AuthSession.token_hash)).all()
    assert token not in stored and second not in stored, "only hashes are stored"


def test_a_session_reaches_only_its_users_projects(client, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other, ids = worlds
    token = open_session(client, other.analyst)
    fresh = TestClient(client.app)
    own = fresh.get(f"{API}/projects/{other.project_id}/requirements", headers=bearer(token))
    assert own.status_code == 200 and own.json()
    for path in (
        f"/projects/{world.project_id}/requirements",
        f"/requirements/{ids['requirement']}",
        f"/requirements/{ids['requirement']}/history",
        f"/risks/{ids['risk']}/history",
        f"/projects/{world.project_id}/audit",
        f"/projects/{world.project_id}/risks",
    ):
        assert fresh.get(f"{API}{path}", headers=bearer(token)).status_code == 404, path


def test_nothing_a_client_sends_forges_a_role_or_a_capability(client, worlds) -> None:  # type: ignore[no-untyped-def]
    world, _other, _ids = worlds
    forged = {
        **hdr(world.analyst),
        "X-ReqPilot-Role": "project_manager",
        "X-ReqPilot-Capability": "coordinator",
        "X-ReqPilot-Superuser": "true",
    }
    deleted = client.request(
        "DELETE", f"{API}/projects/{world.project_id}", json={"confirm_name": NAME}, headers=forged
    )
    assert deleted.status_code == 403
    for extra in ({"role": "project_manager"}, {"capability": {"role": "coordinator"}},
                  {"project_id": str(world.project_id)}):  # fmt: skip
        response = client.request(
            "DELETE",
            f"{API}/projects/{world.project_id}",
            json={"confirm_name": NAME, **extra},
            headers=hdr(world.project_manager),
        )
        assert response.status_code == 422, extra
    me = client.get(f"{API}/auth/whoami", headers=forged).json()
    assert me["roles_by_project"] == {str(world.project_id): ["analyst"]}


# --- deletion (FR-ADM-006) ----------------------------------------------------------------


def test_deletion_over_http(client, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other, ids = worlds
    url = f"{API}/projects/{world.project_id}"
    assert (
        client.request(
            "DELETE", url, json={"confirm_name": NAME}, headers=hdr(world.analyst)
        ).status_code
        == 403
    )
    assert client.request("DELETE", url, json={"confirm_name": NAME},
                          headers=hdr(other.project_manager)).status_code == 404  # fmt: skip
    wrong = client.request(
        "DELETE", url, json={"confirm_name": "wrong"}, headers=hdr(world.project_manager)
    )
    assert wrong.status_code == 400
    done = client.request(
        "DELETE", url, json={"confirm_name": NAME}, headers=hdr(world.project_manager)
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["deleted"] and not body["already_deleted"] and body["total_removed"] > 100
    assert "audit_event" in " ".join(body["retained"])
    again = client.request(
        "DELETE", url, json={"confirm_name": "x"}, headers=hdr(world.project_manager)
    )
    assert again.status_code == 200 and again.json()["already_deleted"]

    created = client.post(
        f"{API}/projects/{world.project_id}/requirements",
        json={"statement": "The system shall do something new.", "domain": "LOAN",
              "source_refs": [{"kind": "utterance", "ref": "x"}]},
        headers=hdr(world.analyst),
    )  # fmt: skip
    assert created.status_code == 410, created.text  # gone: nothing is written to a tombstone
    trail = client.get(f"{API}/projects/{world.project_id}/audit", headers=hdr(world.auditor))
    assert trail.status_code == 200 and trail.json()[-1]["event_type"] == "PROJECT_DELETED"
    history = client.get(
        f"{API}/requirements/{ids['requirement']}/history", headers=hdr(world.auditor)
    )
    assert history.status_code == 200
    assert history.json()["complete"] is False and history.json()["redacted"] is True
    # The other project is untouched.
    assert client.get(f"{API}/projects/{other.project_id}/requirements",
                      headers=hdr(other.analyst)).json()  # fmt: skip


# --- the audit viewer and replay ----------------------------------------------------------


def test_audit_filters_verification_and_replay_over_http(client, worlds) -> None:  # type: ignore[no-untyped-def]
    world, other, ids = worlds
    base = f"{API}/projects/{world.project_id}/audit"
    everything = client.get(base, headers=hdr(world.auditor)).json()
    by_requirement = client.get(
        base, params={"requirement_id": ids["requirement"]}, headers=hdr(world.auditor)
    ).json()
    assert 0 < len(by_requirement) < len(everything)
    by_risk = client.get(base, params={"risk_id": ids["risk"]}, headers=hdr(world.auditor)).json()
    assert {e["event_type"] for e in by_risk} >= {"RISK_RECORDED", "RISK_SEVERITY_COMPUTED"}
    typed = client.get(base, params={"event_type": "APPROVAL_GRANTED"}, headers=hdr(world.auditor))
    assert typed.json() and {e["event_type"] for e in typed.json()} == {"APPROVAL_GRANTED"}

    verify = client.get(f"{base}/verify", headers=hdr(world.auditor))
    assert verify.status_code == 200 and verify.json()["intact"] is True
    assert client.get(f"{base}/verify", headers=hdr(world.analyst)).status_code == 403
    assert client.get(f"{base}/verify", headers=hdr(other.auditor)).status_code == 404

    history = client.get(
        f"{API}/requirements/{ids['requirement']}/history", headers=hdr(world.auditor)
    )
    body = history.json()
    assert history.status_code == 200 and body["complete"] is True and body["gaps"] == []
    assert body["current"] and body["reconstructed"]["versions"] and body["steps"]
    risk = client.get(f"{API}/risks/{ids['risk']}/history", headers=hdr(world.compliance_officer))
    assert risk.status_code == 200 and risk.json()["reconstructed"]["severity"] == "high"
    assert (
        client.get(f"{API}/risks/{ids['risk']}/history", headers=hdr(world.priya)).status_code
        == 404
    )


def test_the_ui_shows_history_deletion_and_a_session_login(client, worlds) -> None:  # type: ignore[no-untyped-def]
    world, _other, ids = worlds
    page = client.get(f"/ui/requirements/{ids['requirement']}/history", headers=hdr(world.auditor))
    assert page.status_code == 200
    assert "Current record" in page.text and "Reconstructed from the audit trail" in page.text
    assert "Complete:" in page.text
    audit = client.get(
        f"/ui/projects/{world.project_id}/audit",
        params={"requirement_id": ids["requirement"]},
        headers=hdr(world.auditor),
    )
    assert audit.status_code == 200 and "Filter (FR-AUD-003)" in audit.text

    as_analyst = client.get(f"/ui/projects/{world.project_id}/delete", headers=hdr(world.analyst))
    assert "Only the Project Manager" in as_analyst.text
    form = client.get(f"/ui/projects/{world.project_id}/delete", headers=hdr(world.project_manager))
    assert 'name="confirm_name"' in form.text
    refused = client.post(
        f"/ui/projects/{world.project_id}/delete",
        data={"confirm_name": "wrong"},
        headers=hdr(world.project_manager),
        follow_redirects=False,
    )
    assert refused.status_code == 303 and "error=" in refused.headers["location"]
    done = client.post(
        f"/ui/projects/{world.project_id}/delete",
        data={"confirm_name": NAME},
        headers=hdr(world.project_manager),
    )
    assert done.status_code == 200 and "verified and committed" in done.text
    after = client.get(f"/ui/requirements/{ids['requirement']}/history", headers=hdr(world.auditor))
    assert "cannot be shown as complete" in after.text and "redacted" in after.text

    login = TestClient(client.app)
    logged = login.post("/ui/login", data={"user_id": str(world.auditor.actor_id)},
                        follow_redirects=False)  # fmt: skip
    assert logged.status_code == 303 and "reqpilot_session" in logged.headers["set-cookie"]
    home = login.get("/ui/")
    assert home.status_code == 200, "the cookie session identifies the user without a header"
    login.post("/ui/logout", follow_redirects=False)
    login.cookies.clear()
    assert login.get("/ui/").status_code == 401
