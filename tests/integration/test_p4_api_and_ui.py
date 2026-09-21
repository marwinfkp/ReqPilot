"""Interviews and clarifications, driven over HTTP and through the demonstration UI.

The same services as the Python-level P4 tests, reached through the API. The
gateway dependency is overridden with the scripted persona; nothing reaches a
network.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p4_helpers import persona, scripted_gateway

from reqpilot.api.dependencies import get_db, get_llm_gateway
from reqpilot.domain.enums import Role
from reqpilot.domain.models import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.main import create_app

pytestmark = pytest.mark.integration

PERSONA = persona()


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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory) -> dict:
    session = session_factory()
    a = Project(name="Loan Origination (synthetic)", domain="loan_origination")
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
        "dana": member(a, Role.STAKEHOLDER, "dana@example.test"),
        "other_stakeholder": member(a, Role.STAKEHOLDER, "other@example.test"),
        "auditor": member(a, Role.AUDITOR, "auditor@example.test"),
        "outsider": member(b, Role.ANALYST, "outsider@example.test"),
    }
    session.commit()
    session.close()
    return data


def add_stakeholder(client: TestClient, world: dict) -> dict:
    data = PERSONA["persona"]
    response = client.post(
        f"/api/v1/projects/{world['a']}/stakeholders",
        json={
            "name": data["name"],
            "stakeholder_role": data["stakeholder_role"],
            "authority_level": data["authority_level"],
            "user_id": str(world["dana"]),
        },
        headers=hdr(world["analyst"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def start(client: TestClient, world: dict) -> dict:
    stakeholder = add_stakeholder(client, world)
    response = client.post(
        f"/api/v1/projects/{world['a']}/sessions",
        json={"stakeholder_id": stakeholder["id"], "sensitivity": "synthetic"},
        headers=hdr(world["analyst"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def persona_answer(turn: dict) -> str:
    topic = turn["next_question"]["topic_id"]
    answers = PERSONA["topics"][topic]["answers"]
    depth = turn["session"]["followups_this_topic"]
    return answers[min(depth, len(answers) - 1)]["text"]


def answer(client: TestClient, world: dict, session_id: str, text: str, who: str = "dana"):
    return client.post(
        f"/api/v1/sessions/{session_id}/answer", json={"text": text}, headers=hdr(world[who])
    )


def interview(client: TestClient, world: dict) -> dict:
    turn = start(client, world)
    for _ in range(40):
        if turn["complete"]:
            return turn
        response = answer(client, world, turn["session"]["id"], persona_answer(turn))
        assert response.status_code == 200, response.text
        turn = response.json()
    raise AssertionError("the interview did not complete")


# --- interviews --------------------------------------------------------------------------


def test_a_session_starts_with_the_first_required_topic(client, world) -> None:
    turn = start(client, world)
    assert turn["session"]["status"] == "active" and not turn["complete"]
    assert turn["next_question"]["topic_id"] == "business_objectives"
    assert turn["next_question"]["speaker_kind"] == "system"
    assert "approves nothing" in turn["notice"]


def test_a_request_cannot_choose_the_topic_or_the_coverage(client, world) -> None:
    turn = start(client, world)
    response = client.post(
        f"/api/v1/sessions/{turn['session']['id']}/answer",
        json={"text": "fine", "topic_id": "project_budget", "coverage": {}},
        headers=hdr(world["dana"]),
    )
    assert response.status_code == 422


def test_the_whole_interview_runs_over_http(client, world) -> None:
    turn = interview(client, world)
    session_id = turn["session"]["id"]
    assert turn["session"]["status"] == "completed"
    coverage = client.get(
        f"/api/v1/sessions/{session_id}/coverage", headers=hdr(world["analyst"])
    ).json()
    assert coverage["remaining"] == [] and "business_rules" in coverage["unresolved"]
    assert set(coverage["covered"]) | set(coverage["unresolved"]) == set(coverage["applicable"])
    utterances = client.get(
        f"/api/v1/sessions/{session_id}/utterances", headers=hdr(world["auditor"])
    ).json()
    assert [u["seq"] for u in utterances] == list(range(1, len(utterances) + 1))
    assert {u["speaker_kind"] for u in utterances} == {"system", "stakeholder"}


def test_pause_and_resume_over_http(client, world) -> None:
    turn = start(client, world)
    session_id = turn["session"]["id"]
    pending = turn["next_question"]["id"]
    paused = client.post(f"/api/v1/sessions/{session_id}/pause", headers=hdr(world["analyst"]))
    assert paused.json()["session"]["status"] == "paused"
    assert answer(client, world, session_id, "an answer").status_code == 409
    assert (
        client.post(f"/api/v1/sessions/{session_id}/pause", headers=hdr(world["dana"])).status_code
        == 403
    )
    resumed = client.post(f"/api/v1/sessions/{session_id}/resume", headers=hdr(world["analyst"]))
    assert resumed.json()["next_question"]["id"] == pending


def test_only_the_linked_stakeholder_or_an_analyst_answers(client, world) -> None:
    turn = start(client, world)
    session_id = turn["session"]["id"]
    assert answer(client, world, session_id, "x", who="other_stakeholder").status_code == 404
    assert answer(client, world, session_id, "x", who="auditor").status_code == 403
    assert answer(client, world, session_id, "x", who="outsider").status_code == 404
    on_behalf = answer(client, world, session_id, persona_answer(turn), who="analyst")
    assert on_behalf.status_code == 200
    assert on_behalf.json()["utterance"]["on_behalf"] is True


def test_another_project_cannot_see_the_session(client, world) -> None:
    turn = start(client, world)
    session_id = turn["session"]["id"]
    for path in ("", "/coverage", "/utterances"):
        response = client.get(
            f"/api/v1/sessions/{session_id}{path}", headers=hdr(world["outsider"])
        )
        assert response.status_code == 404
    listing = client.get(f"/api/v1/projects/{world['a']}/sessions", headers=hdr(world["outsider"]))
    assert listing.status_code == 404


def test_the_next_stakeholder_suggestion_explains_itself(client, world) -> None:
    interview(client, world)
    suggestion = client.get(
        f"/api/v1/projects/{world['a']}/stakeholder-suggestion", headers=hdr(world["analyst"])
    ).json()
    assert suggestion["stakeholder_role"] not in (None, "product_owner")
    assert suggestion["gap_topics"] and "weighted" in suggestion["basis"]


# --- clarifications -------------------------------------------------------------------------


def extracted_target(client, world, session_factory) -> tuple[dict, str]:
    turn = interview(client, world)
    run = client.post(
        f"/api/v1/projects/{world['a']}/analysis-runs",
        json={"session_ids": [turn["session"]["id"]], "domain": "LOAN"},
        headers=hdr(world["analyst"]),
    )
    assert run.status_code == 201, run.text
    with session_factory() as session:
        target = next(
            v
            for v in session.scalars(select(RequirementVersion))
            if "decision letter quickly" in v.statement
        )
    return turn, str(target.id)


def raise_clarification(client, world, session_factory) -> dict:
    turn, version_id = extracted_target(client, world, session_factory)
    data = PERSONA["clarification"]["finding"]
    finding = client.post(
        f"/api/v1/requirement-versions/{version_id}/quality-findings",
        json={
            "finding_type": "ambiguity",
            "severity": "medium",
            "rationale": data["rationale"],
            "span_quote": data["span"],
        },
        headers=hdr(world["analyst"]),
    )
    assert finding.status_code == 201, finding.text
    raised = client.post(
        f"/api/v1/quality-findings/{finding.json()['id']}/clarifications",
        json={"asked_of_stakeholder_id": turn["session"]["stakeholder_id"]},
        headers=hdr(world["analyst"]),
    )
    assert raised.status_code == 201, raised.text
    return raised.json()


def test_a_clarification_is_raised_answered_and_re_analysed(client, world, session_factory) -> None:
    clarification = raise_clarification(client, world, session_factory)
    assert clarification["status"] == "open" and clarification["question"]
    listing = client.get(
        f"/api/v1/projects/{world['a']}/clarifications", headers=hdr(world["dana"])
    ).json()
    assert [c["id"] for c in listing] == [clarification["id"]]
    answered = client.post(
        f"/api/v1/clarifications/{clarification['id']}/answer",
        json={"answer": PERSONA["clarification"]["answer"]},
        headers=hdr(world["dana"]),
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["error"] is None
    assert body["clarification"]["status"] == "answered"
    assert body["clarification"]["reanalysis_status"] == "new_version"
    assert body["clarification"]["resulting_version_no"] == 2
    again = client.post(
        f"/api/v1/clarifications/{clarification['id']}/answer",
        json={"answer": "a second answer"},
        headers=hdr(world["dana"]),
    )
    assert again.status_code == 409


def test_dismissal_is_an_analyst_decision_with_a_reason(client, world, session_factory) -> None:
    clarification = raise_clarification(client, world, session_factory)
    path = f"/api/v1/clarifications/{clarification['id']}/dismiss"
    assert client.post(path, json={"reason": "no"}, headers=hdr(world["dana"])).status_code == 403
    assert client.post(path, json={"reason": ""}, headers=hdr(world["analyst"])).status_code == 422
    dismissed = client.post(
        path, json={"reason": "Covered elsewhere (synthetic)."}, headers=hdr(world["analyst"])
    )
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    outsider = client.get(
        f"/api/v1/clarifications/{clarification['id']}", headers=hdr(world["outsider"])
    )
    assert outsider.status_code == 404


# --- the demonstration UI -----------------------------------------------------------------


def test_the_ui_pages_render_and_drive_the_interview(client, world, session_factory) -> None:
    add_stakeholder(client, world)
    page = client.get(f"/ui/projects/{world['a']}/interviews", headers=hdr(world["analyst"]))
    assert page.status_code == 200 and "Dana Reyes" in page.text
    stakeholder_id = client.get(
        f"/api/v1/projects/{world['a']}/stakeholders", headers=hdr(world["analyst"])
    ).json()[0]["id"]
    started = client.post(
        f"/ui/projects/{world['a']}/sessions",
        data={"stakeholder_id": stakeholder_id, "sensitivity": "synthetic"},
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert started.status_code == 303
    console = client.get(started.headers["location"], headers=hdr(world["dana"]))
    assert console.status_code == 200
    first_question = PERSONA["topics"]["business_objectives"]["questions"][0]
    assert first_question in console.text
    answered = client.post(
        started.headers["location"] + "/answer",
        data={"text": PERSONA["topics"]["business_objectives"]["answers"][0]["text"]},
        headers=hdr(world["dana"]),
        follow_redirects=False,
    )
    assert answered.status_code == 303
    clarifications = client.get(
        f"/ui/projects/{world['a']}/clarifications", headers=hdr(world["analyst"])
    )
    assert clarifications.status_code == 200
    denied = client.get(f"/ui/projects/{world['a']}/interviews", headers=hdr(world["outsider"]))
    assert denied.status_code == 404
