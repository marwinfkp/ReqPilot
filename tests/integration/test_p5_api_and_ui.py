"""Quality runs, findings, conflicts and the glossary over HTTP and in the demonstration UI.

The gateway is the scripted P5 model and the embedder the offline hashing
provider; nothing reaches a network.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p5_helpers import fixture, scripted_gateway, seed_requirements

from reqpilot.api.dependencies import get_db, get_embedding_provider, get_llm_gateway
from reqpilot.domain.enums import ActorKind, Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.policy import Actor
from reqpilot.main import create_app
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider

pytestmark = pytest.mark.integration


def hdr(user_id) -> dict[str, str]:
    return {"X-ReqPilot-Actor": str(user_id)}


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
def session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:
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
    app.dependency_overrides[get_llm_gateway] = lambda: scripted_gateway()[0]
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory) -> dict:
    session = session_factory()
    a = Project(name="Mobile banking (synthetic)", domain="retail")
    b = Project(name="Payments (synthetic)", domain="payments")
    session.add_all([a, b])
    session.flush()

    def member(project: Project, role: Role, email: str) -> uuid.UUID:
        user = User(email=email, display_name=email.split("@")[0])
        session.add(user)
        session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        session.flush()
        return user.id

    data = {
        "a": a.id,
        "b": b.id,
        "analyst": member(a, Role.ANALYST, "analyst@example.test"),
        "stakeholder": member(a, Role.STAKEHOLDER, "sh@example.test"),
        "auditor": member(a, Role.AUDITOR, "auditor@example.test"),
        "outsider": member(b, Role.ANALYST, "outsider@example.test"),
    }
    analyst = Actor(
        actor_id=ActorId(data["analyst"]),
        kind=ActorKind.HUMAN,
        roles_by_project={ProjectId(a.id): frozenset({Role.ANALYST})},
    )
    versions = seed_requirements(session, analyst, ProjectId(a.id), fixture()["requirements"])
    data["versions"] = {k: v.id for k, v in versions.items()}
    session.commit()
    session.close()
    return data


def run(client: TestClient, world: dict, who: str = "analyst", **body) -> dict:  # type: ignore[no-untyped-def]
    response = client.post(
        f"/api/v1/projects/{world['a']}/quality-runs", json=body, headers=hdr(world[who])
    )
    return {"status": response.status_code, "body": response.json()}


def conflicts(client: TestClient, world: dict, who: str = "analyst") -> list[dict]:
    response = client.get(f"/api/v1/projects/{world['a']}/conflicts", headers=hdr(world[who]))
    assert response.status_code == 200, response.text
    return response.json()


def test_a_quality_run_over_http(client, world) -> None:
    result = run(client, world)
    assert result["status"] == 201, result
    body = result["body"]
    assert body["status"] == "completed" and body["conflict_ids"] and body["quality_finding_ids"]
    assert "Nothing here approves" in body["notice"]


def test_only_an_analyst_starts_a_run(client, world) -> None:
    assert run(client, world, "stakeholder")["status"] == 403
    assert run(client, world, "auditor")["status"] == 403
    assert run(client, world, "outsider")["status"] == 404


def test_a_request_cannot_supply_authority_fields(client, world) -> None:
    for extra in ({"severity": "low"}, {"approved": True}, {"detected_by": "human"}):
        assert run(client, world, **extra)["status"] == 422


def test_conflicts_come_with_both_sides(client, world) -> None:
    run(client, world)
    listing = conflicts(client, world, "stakeholder")
    q01 = str(world["versions"]["Q01"])
    conflict = next(c for c in listing if q01 in (c["a"]["version_id"], c["b"]["version_id"]))
    for side in ("a", "b"):
        assert (
            conflict[side]["statement"]
            and conflict[side]["evidence"] in conflict[side]["statement"]
        )
        assert conflict[side]["requirement_human_id"].startswith("FR-BANK-")
    assert conflict["review_priority"] in ("high", "medium", "low")
    assert conflict["involves_stakeholder_disagreement"] is True


def test_the_g4_decision_over_http(client, world) -> None:
    run(client, world)
    conflict = conflicts(client, world)[0]
    path = f"/api/v1/conflicts/{conflict['id']}"
    assert client.post(f"{path}/review", headers=hdr(world["stakeholder"])).status_code == 403
    reviewed = client.post(f"{path}/review", headers=hdr(world["analyst"]))
    assert reviewed.json()["status"] == "under_review"
    missing_reason = client.post(
        f"{path}/resolve", json={"resolution": "choose_a"}, headers=hdr(world["analyst"])
    )
    assert missing_reason.status_code == 422
    resolved = client.post(
        f"{path}/resolve",
        json={"resolution": "reconciled", "reason": "Both hold with a stated condition."},
        headers=hdr(world["analyst"]),
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    again = client.post(f"{path}/dismiss", json={"reason": "late"}, headers=hdr(world["analyst"]))
    assert again.status_code == 409


def test_findings_are_listed_and_closed_by_an_analyst(client, world) -> None:
    run(client, world)
    listing = client.get(
        f"/api/v1/projects/{world['a']}/quality-findings",
        params={"finding_type": "ambiguity"},
        headers=hdr(world["auditor"]),
    )
    assert listing.status_code == 200
    (finding,) = [f for f in listing.json() if f["span_quote"] == "promptly"]
    assert finding["detected_by"] == "rule" and finding["evidence"][0]["quote"] == "promptly"
    path = f"/api/v1/quality-findings/{finding['id']}"
    assert (
        client.post(
            f"{path}/dismiss", json={"reason": "no"}, headers=hdr(world["auditor"])
        ).status_code
        == 403
    )
    closed = client.post(f"{path}/resolve", json={"reason": "fixed"}, headers=hdr(world["analyst"]))
    assert closed.json()["status"] == "resolved"
    assert client.get(path, headers=hdr(world["outsider"])).status_code == 404


def test_another_project_sees_nothing(client, world) -> None:
    run(client, world)
    conflict = conflicts(client, world)[0]
    assert (
        client.get(
            f"/api/v1/projects/{world['a']}/conflicts", headers=hdr(world["outsider"])
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/conflicts/{conflict['id']}", headers=hdr(world["outsider"])
        ).status_code
        == 404
    )
    resolve = client.post(
        f"/api/v1/conflicts/{conflict['id']}/resolve",
        json={"resolution": "choose_a", "reason": "x"},
        headers=hdr(world["outsider"]),
    )
    assert resolve.status_code == 404
    assert (
        client.get(f"/api/v1/conflicts/{uuid.uuid4()}", headers=hdr(world["analyst"])).status_code
        == 404
    )
    assert (
        client.get("/api/v1/conflicts/not-a-uuid", headers=hdr(world["analyst"])).status_code == 422
    )


def test_the_glossary_over_http(client, world) -> None:
    path = f"/api/v1/projects/{world['a']}/glossary"
    added = client.post(
        path,
        json={"term": "KYC", "definition": "Know your customer."},
        headers=hdr(world["analyst"]),
    )
    assert added.status_code == 201
    assert (
        client.post(
            path, json={"term": "X", "definition": "y"}, headers=hdr(world["stakeholder"])
        ).status_code
        == 403
    )
    assert [t["term"] for t in client.get(path, headers=hdr(world["auditor"])).json()] == ["KYC"]


def test_the_quality_page_renders_and_its_forms_work(client, world) -> None:
    page = client.get(f"/ui/projects/{world['a']}/quality", headers=hdr(world["analyst"]))
    assert page.status_code == 200 and "Run quality" in page.text
    posted = client.post(
        f"/ui/projects/{world['a']}/quality-runs",
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert posted.status_code == 303
    page = client.get(f"/ui/projects/{world['a']}/quality", headers=hdr(world["analyst"]))
    assert "definite" in page.text and "promptly" in page.text
    conflict = conflicts(client, world)[0]
    dismissed = client.post(
        f"/ui/conflicts/{conflict['id']}/dismiss",
        data={"reason": "Not a conflict (synthetic)."},
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert dismissed.status_code == 303
    viewer = client.get(f"/ui/projects/{world['a']}/quality", headers=hdr(world["stakeholder"]))
    assert viewer.status_code == 200 and "Run quality" not in viewer.text
    assert (
        client.get(f"/ui/projects/{world['a']}/quality", headers=hdr(world["outsider"])).status_code
        == 404
    )
