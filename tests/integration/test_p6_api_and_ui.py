"""Compliance and security analysis over HTTP and in the demonstration UI (P6).

The gateway is the scripted P6 model and retrieval the allowlist-joined SQLite
test double (injected through the retriever dependency); nothing reaches a network.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.p6_helpers import FixtureRetriever, make_world, scripted_gateway

from reqpilot.api.dependencies import (
    get_db,
    get_embedding_provider,
    get_llm_gateway,
    get_retriever_factory,
)
from reqpilot.domain.compliance.language import COMPLIANCE_ADVISORY_NOTICE, find_prohibited
from reqpilot.domain.models import Base
from reqpilot.main import create_app
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider

pytestmark = pytest.mark.integration


def hdr(actor) -> dict[str, str]:
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
    app.dependency_overrides[get_retriever_factory] = lambda: FixtureRetriever
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def worlds(session_factory):
    session = session_factory()
    first = make_world(session)
    other = make_world(session, "Another bank (synthetic)")
    session.commit()
    session.close()
    return first, other


def run(client: TestClient, world, who=None, **body):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/projects/{world.project_id}/compliance-runs",
        json=body,
        headers=hdr(who or world.analyst),
    )


def test_a_compliance_run_over_http(client, worlds) -> None:
    first, _other = worlds
    response = run(client, first)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["advisory_notice"] == COMPLIANCE_ADVISORY_NOTICE
    assert body["compliance_mapping_ids"] and body["compliance_gap_ids"]
    assert body["security_finding_ids"] and body["gate_task_ids"]
    assert "approval-tasks" in body["notice"]


def test_only_an_analyst_starts_a_run(client, worlds) -> None:
    first, other = worlds
    for who in (first.compliance_officer, first.security_reviewer, first.auditor):
        assert run(client, first, who).status_code == 403
    assert run(client, first, other.analyst).status_code == 404


def test_every_compliance_view_carries_the_advisory_notice(client, worlds) -> None:
    first, _other = worlds
    run(client, first)
    base = f"/api/v1/projects/{first.project_id}"
    for path in ("/compliance-mappings", "/compliance-gaps", "/security-privacy-findings"):
        response = client.get(base + path, headers=hdr(first.auditor))
        assert response.status_code == 200, response.text
        assert response.json()["advisory_notice"] == COMPLIANCE_ADVISORY_NOTICE
    mappings = client.get(base + "/compliance-mappings", headers=hdr(first.auditor)).json()
    detail = client.get(
        f"/api/v1/compliance-mappings/{mappings['mappings'][0]['id']}", headers=hdr(first.auditor)
    ).json()
    assert detail["advisory_notice"] == COMPLIANCE_ADVISORY_NOTICE
    citation = detail["mapping"]["citations"][0]
    assert citation["resolves"] is True and citation["quote"]
    for field in (
        "source_title",
        "source_type",
        "binding",
        "issuing_body",
        "jurisdiction",
        "source_version",
        "curated_on",
        "kb_version",
    ):
        assert citation[field] is not None, field
    report = client.get(base + "/compliance-report", headers=hdr(first.auditor))
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/markdown")
    assert report.text.count(COMPLIANCE_ADVISORY_NOTICE) == 2
    assert find_prohibited(report.text) == []


def test_findings_show_proposed_beside_authoritative(client, worlds) -> None:
    first, _other = worlds
    run(client, first)
    body = client.get(
        f"/api/v1/projects/{first.project_id}/security-privacy-findings",
        headers=hdr(first.security_reviewer),
    ).json()
    auth = next(
        f
        for f in body["findings"]
        if f["family"] == "authentication" and f["proposed_risk_level"] == "low"
    )
    assert auth["risk_level"] == "high" and auth["catalogue_floor"] == "high"
    assert auth["status"] == "pending_review" and auth["approval_task_id"]
    assert "overrides the proposed low" in auth["escalation_reason"]


def test_g2_is_decided_only_through_the_approval_path(client, worlds) -> None:
    first, _other = worlds
    run(client, first)
    mappings = client.get(
        f"/api/v1/projects/{first.project_id}/compliance-mappings", headers=hdr(first.analyst)
    ).json()["mappings"]
    high = next(m for m in mappings if m["is_high_impact"])
    decide = f"/api/v1/approval-tasks/{high['approval_task_id']}/decide"
    wrong = client.post(
        decide,
        json={"decision": "APPROVE", "role_exercised": "security_reviewer"},
        headers=hdr(first.security_reviewer),
    )
    assert wrong.status_code == 403
    right = client.post(
        decide,
        json={"decision": "APPROVE", "role_exercised": "compliance_officer"},
        headers=hdr(first.compliance_officer),
    )
    assert right.status_code == 200, right.text
    after = client.get(
        f"/api/v1/compliance-mappings/{high['id']}", headers=hdr(first.analyst)
    ).json()["mapping"]
    assert after["status"] == "approved"


def test_nothing_crosses_projects(client, worlds) -> None:
    first, other = worlds
    run(client, first)
    mappings = client.get(
        f"/api/v1/projects/{first.project_id}/compliance-mappings", headers=hdr(first.analyst)
    ).json()["mappings"]
    for path in (
        f"/api/v1/projects/{first.project_id}/compliance-mappings",
        f"/api/v1/projects/{first.project_id}/compliance-gaps",
        f"/api/v1/projects/{first.project_id}/security-privacy-findings",
        f"/api/v1/projects/{first.project_id}/compliance-report",
        f"/api/v1/compliance-mappings/{mappings[0]['id']}",
    ):
        assert client.get(path, headers=hdr(other.analyst)).status_code == 404, path
    decide = client.post(
        "/api/v1/approval-tasks/"
        f"{next(m['approval_task_id'] for m in mappings if m['approval_task_id'])}/decide",
        json={"decision": "APPROVE", "role_exercised": "compliance_officer"},
        headers=hdr(other.compliance_officer),
    )
    assert decide.status_code == 404


def test_a_client_cannot_supply_mappings_or_risk(client, worlds) -> None:
    first, _other = worlds
    response = run(client, first, risk_level="low", mappings=[{"control_key": "X"}])
    assert response.status_code == 422


def test_the_compliance_page_renders_the_notice(client, worlds) -> None:
    first, _other = worlds
    run(client, first)
    page = client.get(f"/ui/projects/{first.project_id}/compliance", headers=hdr(first.auditor))
    assert page.status_code == 200
    assert "Advisory notice" in page.text and "does not provide legal advice" in page.text
    assert "G3 (Security Reviewer)" in page.text and "candidate" in page.text.lower()
    tasks = client.get(
        f"/ui/projects/{first.project_id}/tasks", headers=hdr(first.compliance_officer)
    )
    assert tasks.status_code == 200 and "compliance_mapping" in tasks.text
