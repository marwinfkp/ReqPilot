"""Sources, extraction runs, classification and the review queue, driven over HTTP.

The same services as the Python-level tests, reached through the API and the
demonstration UI. The gateway dependency is overridden with the scripted
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
from tests.p3_helpers import scripted_gateway, workshop_text

from reqpilot.api.dependencies import get_db, get_llm_gateway
from reqpilot.domain.enums import Role
from reqpilot.domain.models import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.main import create_app

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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory) -> dict:
    session = session_factory()
    a = Project(name="Loan Origination", domain="loan_origination")
    b = Project(name="Payments", domain="payments")
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
        "officer": member(a, Role.COMPLIANCE_OFFICER, "officer@example.test"),
        "stakeholder": member(a, Role.STAKEHOLDER, "stakeholder@example.test"),
        "outsider": member(b, Role.ANALYST, "outsider@example.test"),
    }
    session.commit()
    session.close()
    return data


def add_source(client: TestClient, world: dict, who: str = "analyst") -> dict:
    response = client.post(
        f"/api/v1/projects/{world['a']}/sources",
        json={
            "doc_type": "transcript",
            "title": "Workshop (synthetic)",
            "text": workshop_text(),
            "sensitivity": "synthetic",
        },
        headers=hdr(world[who]),
    )
    return {"status": response.status_code, "body": response.json()}


def extract(client: TestClient, world: dict) -> dict:
    source = add_source(client, world)["body"]["source"]
    response = client.post(
        f"/api/v1/projects/{world['a']}/analysis-runs",
        json={"source_ids": [source["id"]], "domain": "LOAN"},
        headers=hdr(world["analyst"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def requirement_id(client: TestClient, world: dict, human_id: str) -> str:
    listing = client.get(
        f"/api/v1/projects/{world['a']}/requirements", headers=hdr(world["analyst"])
    )
    return next(r["id"] for r in listing.json() if r["human_id"] == human_id)


# --- sources --------------------------------------------------------------------------


def test_an_analyst_adds_a_source_and_reads_its_segments(client, world) -> None:
    added = add_source(client, world)
    assert added["status"] == 201 and added["body"]["created"] is True
    source = added["body"]["source"]
    assert (source["masking_status"], source["masker_id"]) == ("not_masked", "none")
    detail = client.get(f"/api/v1/sources/{source['id']}", headers=hdr(world["analyst"])).json()
    assert len(detail["chunks"]) == 11 and detail["chunks"][1]["speaker"] == "Facilitator"
    again = add_source(client, world)
    assert again["body"]["created"] is False and again["body"]["source"]["id"] == source["id"]


def test_source_access_follows_the_policy(client, world) -> None:
    assert add_source(client, world, "officer")["status"] == 403
    source = add_source(client, world)["body"]["source"]
    assert (
        client.get(f"/api/v1/sources/{source['id']}", headers=hdr(world["outsider"])).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/projects/{world['a']}/sources", headers=hdr(world["stakeholder"])
        ).status_code
        == 403
    )


def test_a_file_upload_is_accepted(client, world) -> None:
    response = client.post(
        f"/api/v1/projects/{world['a']}/sources/upload",
        data={"doc_type": "meeting_notes", "title": "Notes", "sensitivity": "synthetic"},
        files={"file": ("notes.md", b"Priya: We need exports of statements.", "text/markdown")},
        headers=hdr(world["analyst"]),
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"]["filename"] == "notes.md"


# --- runs --------------------------------------------------------------------------------


def test_a_batch_run_extracts_and_classifies(client, world) -> None:
    summary = extract(client, world)
    assert summary["status"] == "completed"
    assert (summary["accepted"], summary["merged"], summary["rejected"]) == (5, 1, 1)
    detail = client.get(f"/api/v1/runs/{summary['run_id']}", headers=hdr(world["analyst"])).json()
    extraction = next(a for a in detail["agent_runs"] if a["node"] == "extract_requirements")
    assert extraction["prompt"] == "requirement_extraction@1.0.0"
    assert (extraction["provider"], extraction["is_model"]) == ("scripted", False)
    assert {c["status"] for c in detail["candidates"]} == {"accepted", "merged", "rejected"}


def test_run_requests_are_validated_and_scoped(client, world) -> None:
    source = add_source(client, world)["body"]["source"]
    missing_domain = client.post(
        f"/api/v1/projects/{world['a']}/analysis-runs",
        json={"source_ids": [source["id"]]},
        headers=hdr(world["analyst"]),
    )
    assert missing_domain.status_code == 422
    officer = client.post(
        f"/api/v1/projects/{world['a']}/analysis-runs",
        json={"source_ids": [source["id"]], "domain": "LOAN"},
        headers=hdr(world["officer"]),
    )
    assert officer.status_code == 403
    outsider = client.post(
        f"/api/v1/projects/{world['a']}/analysis-runs",
        json={"source_ids": [source["id"]], "domain": "LOAN"},
        headers=hdr(world["outsider"]),
    )
    assert outsider.status_code == 404


def test_the_record_shows_all_thirteen_fields_honestly(client, world) -> None:
    extract(client, world)
    rid = requirement_id(client, world, "FR-LOAN-003")
    record = client.get(f"/api/v1/requirements/{rid}/record", headers=hdr(world["analyst"])).json()
    assert record["id"] == "FR-LOAN-003"
    assert record["statement"] == "The system shall allow customers to export their statements."
    assert record["category"] == ["data_management", "functional"]
    assert record["source_stakeholders"] == ["Meera (Compliance)"]
    assert record["business_justification"] is None and record["priority"] is None
    assert (
        record["applicable_regulations"].startswith("deferred")
        and "P6" in record["applicable_regulations"]
    )
    assert record["risk_level"].startswith("deferred") and "P7" in record["risk_level"]
    assert record["confidence_score"] == 0.4
    assert "not a calibrated probability" in record["confidence_interpretation"]
    assert record["approval_status"] == "CLASSIFIED"


def test_classification_can_be_read_and_overridden_over_http(client, world) -> None:
    extract(client, world)
    rid = requirement_id(client, world, "NFR-LOAN-002")
    version_id = client.get(
        f"/api/v1/requirements/{rid}/record", headers=hdr(world["analyst"])
    ).json()["version_id"]
    before = client.get(
        f"/api/v1/requirement-versions/{version_id}/classification", headers=hdr(world["officer"])
    )
    assert before.status_code == 200 and len(before.json()["history"]) == 1
    denied = client.put(
        f"/api/v1/requirement-versions/{version_id}/classification",
        json={"categories": ["performance"], "reason": "r"},
        headers=hdr(world["officer"]),
    )
    assert denied.status_code == 403
    after = client.put(
        f"/api/v1/requirement-versions/{version_id}/classification",
        json={"categories": ["performance"], "reason": "load belongs to performance"},
        headers=hdr(world["analyst"]),
    ).json()
    assert [label["category"] for label in after["current"]] == ["performance"]
    assert len(after["history"]) == 2 and after["current"][0]["source"] == "human"


def test_the_review_queue_over_http(client, world) -> None:
    extract(client, world)
    listing = client.get(
        f"/api/v1/projects/{world['a']}/review-items?status_filter=open",
        headers=hdr(world["analyst"]),
    ).json()
    assert {i["reason"] for i in listing} >= {
        "unresolved_source",
        "low_extraction_signal",
        "unknown_label",
    }
    assert all("approves nothing" in i["notice"] for i in listing)
    rejected = next(i for i in listing if i["reason"] == "unresolved_source")
    resolved = client.post(
        f"/api/v1/review-items/{rejected['id']}/resolve",
        json={"resolution": "acknowledged", "note": "injected instruction"},
        headers=hdr(world["analyst"]),
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    wrong = client.post(
        f"/api/v1/review-items/{rejected['id']}/resolve",
        json={"resolution": "acknowledged"},
        headers=hdr(world["analyst"]),
    )
    assert wrong.status_code == 409
    assert (
        client.post(
            f"/api/v1/review-items/{rejected['id']}/resolve",
            json={"resolution": "acknowledged"},
            headers=hdr(world["outsider"]),
        ).status_code
        == 404
    )


def test_criteria_and_merge_over_http(client, world) -> None:
    extract(client, world)
    upload = requirement_id(client, world, "FR-LOAN-001")
    export = requirement_id(client, world, "FR-LOAN-003")
    record = client.get(
        f"/api/v1/requirements/{upload}/record", headers=hdr(world["analyst"])
    ).json()
    criteria = client.get(
        f"/api/v1/requirement-versions/{record['version_id']}/acceptance-criteria",
        headers=hdr(world["analyst"]),
    ).json()
    assert criteria[0]["given_text"].startswith("an applicant")
    merged = client.post(
        f"/api/v1/requirements/{upload}/merge",
        json={"duplicate_requirement_id": export, "reason": "demonstration"},
        headers=hdr(world["analyst"]),
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["version_no"] == 2 and len(merged.json()["source_refs"]) == 2


# --- pages --------------------------------------------------------------------------------


def test_the_pages_render_and_say_what_they_are(client, world) -> None:
    summary = extract(client, world)
    sources = client.get(f"/ui/projects/{world['a']}/sources", headers=hdr(world["analyst"]))
    assert sources.status_code == 200 and "not a model" in sources.text
    assert "masking is <b>not implemented</b>" in sources.text
    run = client.get(f"/ui/runs/{summary['run_id']}", headers=hdr(world["analyst"]))
    assert run.status_code == 200 and "not a calibrated probability" in run.text
    review = client.get(f"/ui/projects/{world['a']}/review", headers=hdr(world["analyst"]))
    assert review.status_code == 200 and "This is not approval." in review.text
    rid = requirement_id(client, world, "FR-LOAN-001")
    page = client.get(f"/ui/requirements/{rid}", headers=hdr(world["analyst"]))
    assert page.status_code == 200
    assert "Requirement record (FR-EXT-002)" in page.text and "deferred" in page.text
    assert "Applicants must be able to upload their income documents" in page.text
    assert "review-prioritisation signal, not a calibrated probability" in page.text


def test_the_ui_forms_drive_the_same_services(client, world) -> None:
    created = client.post(
        f"/ui/projects/{world['a']}/sources",
        data={
            "doc_type": "transcript",
            "title": "UI source",
            "text": workshop_text(),
            "sensitivity": "synthetic",
        },
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert created.status_code == 303
    source_id = client.get(
        f"/api/v1/projects/{world['a']}/sources", headers=hdr(world["analyst"])
    ).json()[0]["id"]
    ran = client.post(
        f"/ui/projects/{world['a']}/analysis-runs",
        data={"domain": "LOAN", "source_ids": [source_id]},
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert ran.status_code == 303 and ran.headers["location"].startswith("/ui/runs/")
    rid = requirement_id(client, world, "NFR-LOAN-002")
    version_id = client.get(
        f"/api/v1/requirements/{rid}/record", headers=hdr(world["analyst"])
    ).json()["version_id"]
    override = client.post(
        f"/ui/requirement-versions/{version_id}/classification",
        data={"requirement_id": rid, "reason": "ui override", "categories": ["performance"]},
        headers=hdr(world["analyst"]),
        follow_redirects=False,
    )
    assert override.status_code == 303
