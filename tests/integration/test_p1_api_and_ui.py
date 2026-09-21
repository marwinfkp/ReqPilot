"""API and UI tests: the same governance, driven over HTTP.

The service-level proof lives in ``tests/workflow/test_p1_exit_test.py``. This
file proves the same guarantees hold when the workflow is driven through the
endpoints and the demonstration UI, because a governance control that only works
when called from Python is not a control.

There is deliberately **no test-only implementation of the workflow**: the UI
routes call the same services the API calls.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from reqpilot.api.dependencies import get_db
from reqpilot.domain.enums import Role
from reqpilot.domain.models import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.main import create_app

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# A client wired to one shared in-memory database
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

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
    app = create_app()

    def override_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory):
    """Two projects, four people. The cast the isolation tests need."""
    session = session_factory()
    a = Project(name="Loan Origination", domain="loan_origination")
    b = Project(name="Payments", domain="payments")
    session.add_all([a, b])
    session.flush()

    def member(project: Project, role: Role, email: str) -> User:
        user = User(email=email, display_name=email.split("@")[0])
        session.add(user)
        session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        session.flush()
        return user

    data = {
        "project_a": a.id,
        "project_b": b.id,
        "author": member(a, Role.ANALYST, "author@example.test").id,
        "reviewer": member(a, Role.ANALYST, "reviewer@example.test").id,
        "officer": member(a, Role.COMPLIANCE_OFFICER, "officer@example.test").id,
        "outsider": member(b, Role.ANALYST, "outsider@example.test").id,
    }
    session.commit()
    session.close()
    return data


def hdr(user_id) -> dict[str, str]:
    return {"X-ReqPilot-Actor": str(user_id)}


REQUIREMENT_PAYLOAD = {
    "statement": "The system shall verify applicant identity before disbursal.",
    "domain": "LOAN",
    "kind": "FR",
    "category": "functional",
    "priority": "must",
    "source_refs": [{"kind": "utterance", "ref": "interview-1"}],
}


def create_requirement(client: TestClient, world, payload: dict | None = None):
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/requirements",
        json=payload or REQUIREMENT_PAYLOAD,
        headers=hdr(world["author"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def task_for(tasks: list[dict], role: str) -> dict:
    """The task in a G1 group that the given role must sign.

    G1 is a co-approval gate, so one submitted version yields two tasks.
    """
    return next(t for t in tasks if t["required_role"] == role)


def advance_to_validated(client: TestClient, world, version_id: str) -> None:
    for target in ("EXTRACTED", "CLASSIFIED", "ANALYZED", "VALIDATED"):
        response = client.post(
            f"/api/v1/requirement-versions/{version_id}/transition",
            json={"target": target},
            headers=hdr(world["author"]),
        )
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Authentication stand-in
# ---------------------------------------------------------------------------


def test_a_request_without_an_actor_is_refused(client: TestClient, world) -> None:
    response = client.get(f"/api/v1/projects/{world['project_a']}/requirements")
    assert response.status_code == 401


def test_an_unknown_actor_is_refused(client: TestClient, world) -> None:
    response = client.get(
        f"/api/v1/projects/{world['project_a']}/requirements",
        headers=hdr(uuid.uuid4()),
    )
    assert response.status_code == 401


def test_a_malformed_actor_header_is_refused(client: TestClient, world) -> None:
    response = client.get(
        f"/api/v1/projects/{world['project_a']}/requirements",
        headers={"X-ReqPilot-Actor": "not-a-uuid"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The workflow over HTTP
# ---------------------------------------------------------------------------


def test_full_workflow_through_the_api(client: TestClient, world) -> None:
    """create → version → validate → submit → G1 → approve → baseline."""
    detail = create_requirement(client, world)
    requirement_id = detail["requirement"]["id"]
    assert detail["requirement"]["human_id"] == "FR-LOAN-001"
    assert detail["current_version"]["state"] == "CANDIDATE"

    # Edit: a successor version, predecessor untouched.
    patched = client.patch(
        f"/api/v1/requirements/{requirement_id}",
        json={
            **REQUIREMENT_PAYLOAD,
            "statement": "The system shall verify identity (v2).",
            "change_reason": "clarified",
        },
        headers=hdr(world["author"]),
    )
    assert patched.status_code == 200, patched.text
    versions = patched.json()["versions"]
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[0]["state"] == "CANDIDATE", "v1 must not be mutated"
    v2 = patched.json()["current_version"]
    assert v2["version_no"] == 2

    advance_to_validated(client, world, v2["id"])

    # Submit → 202 with approval tasks, and no baseline yet.
    submitted = client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [v2["id"]]},
        headers=hdr(world["author"]),
    )
    assert submitted.status_code == 202, submitted.text
    tasks = submitted.json()
    # Co-approval: one task per required role, sharing one group.
    assert len(tasks) == 2
    assert {t["required_role"] for t in tasks} == {"analyst", "compliance_officer"}
    assert len({t["task_group_id"] for t in tasks}) == 1
    for task in tasks:
        assert task["gate"] == "G1"
        assert task["subject_version_hash"] == v2["content_hash"]

    baselines = client.get(
        f"/api/v1/projects/{world['project_a']}/baselines", headers=hdr(world["author"])
    ).json()
    assert baselines == [], "submitting must not create a baseline"

    # First required role. Closes its own task but not the gate.
    first = client.post(
        f"/api/v1/approval-tasks/{task_for(tasks, 'analyst')['id']}/decide",
        json={"decision": "APPROVE", "role_exercised": "analyst"},
        headers=hdr(world["reviewer"]),
    )
    assert first.status_code == 200, first.text
    assert first.json()["group_complete"] is False
    assert first.json()["baseline_id"] is None

    assert (
        client.get(
            f"/api/v1/projects/{world['project_a']}/baselines", headers=hdr(world["author"])
        ).json()
        == []
    ), "one role alone must not baseline anything"

    # Second required role closes the gate and commits the baseline.
    second = client.post(
        f"/api/v1/approval-tasks/{task_for(tasks, 'compliance_officer')['id']}/decide",
        json={
            "decision": "APPROVE",
            "role_exercised": "compliance_officer",
            "baseline_label": "release-1",
        },
        headers=hdr(world["officer"]),
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["task_closed"] is True
    assert body["baseline_id"] is not None

    baseline = client.get(
        f"/api/v1/baselines/{body['baseline_id']}", headers=hdr(world["officer"])
    ).json()
    assert baseline["baseline"]["label"] == "release-1"
    assert [m["version_no"] for m in baseline["members"]] == [2]
    assert all(m["state"] == "BASELINED" for m in baseline["members"])

    # The audit trail is readable and covers the flow.
    audit = client.get(
        f"/api/v1/projects/{world['project_a']}/audit", headers=hdr(world["officer"])
    ).json()
    types = {e["event_type"] for e in audit}
    assert {"REQUIREMENT_CREATED", "APPROVAL_GRANTED", "BASELINE_COMMITTED"} <= types


# ---------------------------------------------------------------------------
# Bypass attempts over HTTP
# ---------------------------------------------------------------------------


def test_no_endpoint_can_set_a_state_directly(client: TestClient, world) -> None:
    """``PATCH`` carries no state field, and a stray one is ignored."""
    detail = create_requirement(client, world)
    requirement_id = detail["requirement"]["id"]

    response = client.patch(
        f"/api/v1/requirements/{requirement_id}",
        json={**REQUIREMENT_PAYLOAD, "change_reason": "sneaky", "state": "APPROVED"},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 200
    assert response.json()["current_version"]["state"] == "CANDIDATE"


def test_transition_endpoint_cannot_reach_approved(client: TestClient, world) -> None:
    """Even the transition endpoint cannot approve: that guard needs a decision."""
    detail = create_requirement(client, world)
    version_id = detail["current_version"]["id"]
    advance_to_validated(client, world, version_id)

    client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [version_id]},
        headers=hdr(world["author"]),
    )
    response = client.post(
        f"/api/v1/requirement-versions/{version_id}/transition",
        json={"target": "APPROVED"},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 409
    assert "approval decision" in response.json()["detail"]


def test_the_author_cannot_approve_over_http(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    version_id = detail["current_version"]["id"]
    advance_to_validated(client, world, version_id)
    tasks = client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [version_id]},
        headers=hdr(world["author"]),
    ).json()

    response = client.post(
        f"/api/v1/approval-tasks/{task_for(tasks, 'analyst')['id']}/decide",
        json={"decision": "APPROVE", "role_exercised": "analyst"},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 409
    assert "may not approve" in response.json()["detail"]


def test_a_role_the_actor_lacks_is_refused(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    version_id = detail["current_version"]["id"]
    advance_to_validated(client, world, version_id)
    tasks = client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [version_id]},
        headers=hdr(world["author"]),
    ).json()

    response = client.post(
        f"/api/v1/approval-tasks/{task_for(tasks, 'compliance_officer')['id']}/decide",
        json={"decision": "APPROVE", "role_exercised": "compliance_officer"},
        headers=hdr(world["reviewer"]),  # an analyst, not an officer
    )
    assert response.status_code == 403


def test_a_reviewer_cannot_author(client: TestClient, world) -> None:
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/requirements",
        json=REQUIREMENT_PAYLOAD,
        headers=hdr(world["officer"]),
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Cross-project isolation over HTTP
# ---------------------------------------------------------------------------


def test_outsider_gets_404_for_a_foreign_project(client: TestClient, world) -> None:
    """Not 403: answering 'forbidden' would confirm the project exists."""
    response = client.get(
        f"/api/v1/projects/{world['project_a']}/requirements",
        headers=hdr(world["outsider"]),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "not found"


def test_outsider_cannot_read_a_foreign_requirement(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    response = client.get(
        f"/api/v1/requirements/{detail['requirement']['id']}",
        headers=hdr(world["outsider"]),
    )
    assert response.status_code == 404


def test_outsider_cannot_edit_a_foreign_requirement(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    response = client.patch(
        f"/api/v1/requirements/{detail['requirement']['id']}",
        json={**REQUIREMENT_PAYLOAD, "change_reason": "hijack"},
        headers=hdr(world["outsider"]),
    )
    assert response.status_code == 404


def test_outsider_cannot_submit_or_baseline(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    version_id = detail["current_version"]["id"]
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [version_id]},
        headers=hdr(world["outsider"]),
    )
    assert response.status_code == 404


def test_outsider_cannot_read_the_audit_trail(client: TestClient, world) -> None:
    response = client.get(
        f"/api/v1/projects/{world['project_a']}/audit", headers=hdr(world["outsider"])
    )
    assert response.status_code == 404


def test_a_nonexistent_requirement_answers_the_same_as_a_foreign_one(
    client: TestClient, world
) -> None:
    """The two cases must be indistinguishable to the caller."""
    detail = create_requirement(client, world)
    foreign = client.get(
        f"/api/v1/requirements/{detail['requirement']['id']}", headers=hdr(world["outsider"])
    )
    missing = client.get(f"/api/v1/requirements/{uuid.uuid4()}", headers=hdr(world["outsider"]))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_an_empty_statement_is_rejected(client: TestClient, world) -> None:
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/requirements",
        json={**REQUIREMENT_PAYLOAD, "statement": ""},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 422


def test_a_malformed_human_id_is_rejected(client: TestClient, world) -> None:
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/requirements",
        json={**REQUIREMENT_PAYLOAD, "human_id": "nonsense"},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 400
    assert "approved convention" in response.json()["detail"]


def test_an_invalid_transition_is_rejected(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    response = client.post(
        f"/api/v1/requirement-versions/{detail['current_version']['id']}/transition",
        json={"target": "BASELINED"},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 409


def test_submitting_an_unvalidated_version_is_rejected(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    response = client.post(
        f"/api/v1/projects/{world['project_a']}/baselines",
        json={"version_ids": [detail["current_version"]["id"]]},
        headers=hdr(world["author"]),
    )
    assert response.status_code == 409


def test_deciding_a_nonexistent_task_is_not_found(client: TestClient, world) -> None:
    response = client.post(
        f"/api/v1/approval-tasks/{uuid.uuid4()}/decide",
        json={"decision": "APPROVE", "role_exercised": "analyst"},
        headers=hdr(world["reviewer"]),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The demonstration UI, driving the same services
# ---------------------------------------------------------------------------


def test_ui_project_list_shows_only_your_projects(client: TestClient, world) -> None:
    page = client.get("/ui/", headers=hdr(world["author"]))
    assert page.status_code == 200
    assert "Loan Origination" in page.text
    assert "Payments" not in page.text


def test_ui_full_workflow(client: TestClient, world) -> None:
    """The same create → validate → submit → approve → baseline path, via forms."""
    project_a = world["project_a"]

    created = client.post(
        f"/ui/projects/{project_a}/requirements",
        data={
            "statement": "The system shall record every disbursal.",
            "domain": "LOAN",
            "kind": "FR",
            "category": "functional",
            "priority": "must",
            "source_ref": "interview-2",
        },
        headers=hdr(world["author"]),
        follow_redirects=False,
    )
    assert created.status_code == 303

    project_page = client.get(f"/ui/projects/{project_a}", headers=hdr(world["author"]))
    assert "FR-LOAN-001" in project_page.text

    detail = client.get(
        f"/api/v1/projects/{project_a}/requirements", headers=hdr(world["author"])
    ).json()
    requirement_id = detail[0]["id"]
    version_id = detail[0]["current_version_id"]

    for target in ("EXTRACTED", "CLASSIFIED", "ANALYZED", "VALIDATED"):
        step = client.post(
            f"/ui/requirement-versions/{version_id}/transition",
            data={"target": target, "requirement_id": requirement_id},
            headers=hdr(world["author"]),
            follow_redirects=False,
        )
        assert step.status_code == 303, target

    submitted = client.post(
        f"/ui/projects/{project_a}/submit",
        data={"version_ids": [version_id]},
        headers=hdr(world["author"]),
        follow_redirects=False,
    )
    assert submitted.status_code == 303

    queue = client.get(f"/ui/projects/{project_a}/tasks", headers=hdr(world["reviewer"]))
    assert "G1" in queue.text
    assert "outstanding" in queue.text

    tasks = client.get(
        f"/api/v1/projects/{project_a}/approval-tasks", headers=hdr(world["reviewer"])
    ).json()
    assert len(tasks) == 2, "G1 co-approval raises one task per required role"

    for user, role, label in (
        (world["reviewer"], "analyst", ""),
        (world["officer"], "compliance_officer", "ui-release-1"),
    ):
        decided = client.post(
            f"/ui/approval-tasks/{task_for(tasks, role)['id']}/decide",
            data={
                "project_id": str(project_a),
                "decision": "APPROVE",
                "role_exercised": role,
                "justification": "",
                "baseline_label": label,
            },
            headers=hdr(user),
            follow_redirects=False,
        )
        assert decided.status_code == 303

    baselines = client.get(
        f"/api/v1/projects/{project_a}/baselines", headers=hdr(world["officer"])
    ).json()
    assert len(baselines) == 1
    assert baselines[0]["label"] == "ui-release-1"

    baseline_page = client.get(f"/ui/baselines/{baselines[0]['id']}", headers=hdr(world["officer"]))
    assert "BASELINED" in baseline_page.text

    audit_page = client.get(f"/ui/projects/{project_a}/audit", headers=hdr(world["officer"]))
    assert "Hash chain verified" in audit_page.text
    assert "BASELINE_COMMITTED" in audit_page.text


def test_ui_hides_approval_from_the_author(client: TestClient, world) -> None:
    """The UI explains why, and the service would refuse anyway."""
    project_a = world["project_a"]
    detail = create_requirement(client, world)
    advance_to_validated(client, world, detail["current_version"]["id"])
    client.post(
        f"/api/v1/projects/{project_a}/baselines",
        json={"version_ids": [detail["current_version"]["id"]]},
        headers=hdr(world["author"]),
    )
    page = client.get(f"/ui/projects/{project_a}/tasks", headers=hdr(world["author"]))
    assert "you may not approve it" in page.text.lower()


def test_ui_refuses_a_foreign_project(client: TestClient, world) -> None:
    page = client.get(f"/ui/projects/{world['project_a']}", headers=hdr(world["outsider"]))
    assert page.status_code == 404


def test_ui_requirement_page_shows_version_history(client: TestClient, world) -> None:
    detail = create_requirement(client, world)
    requirement_id = detail["requirement"]["id"]
    client.patch(
        f"/api/v1/requirements/{requirement_id}",
        json={**REQUIREMENT_PAYLOAD, "statement": "Second wording.", "change_reason": "reword"},
        headers=hdr(world["author"]),
    )
    page = client.get(f"/ui/requirements/{requirement_id}", headers=hdr(world["author"]))
    assert page.status_code == 200
    assert "Version history" in page.text
    assert "Second wording." in page.text
    assert "reword" in page.text
