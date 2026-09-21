"""The knowledge-base, retrieval and evidence API and UI, driven over HTTP.

The same services as the Python-level tests, reached through the endpoints and
the demonstration UI, because a control that only holds when called from Python
is not a control. Retrieval over HTTP needs PostgreSQL and is in the second half
of this file, which skips without one.
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
from tests.kb_helpers import as_retrieved, chunks_of, success

from reqpilot.api.dependencies import get_db, get_embedding_provider
from reqpilot.domain.enums import Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.policy import Actor
from reqpilot.main import create_app
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.knowledge import EvidenceService

pytestmark = pytest.mark.integration

SOURCE = {
    "source_type": "org_policy",
    "issuing_body": "Acme Bank (fictional)",
    "title": "Access Control Policy (fictional)",
    "jurisdiction": "IN",
    "version": "2026.1",
    "effective_date": "2025-01-01",
    "retrieved_at": "2026-01-15",
    "licence_class": "synthetic",
    "licence_note": "Fictional policy written for ReqPilot tests.",
}
POLICY = (
    "1. Access\n1.1 Access to customer data is granted on a least-privilege basis.\n"
    "1.2 Access rights are reviewed quarterly."
)


def hdr(user_id) -> dict[str, str]:
    return {"X-ReqPilot-Actor": str(user_id)}


def build_world(session: Session) -> dict:
    a = Project(name="Loan Origination", domain="loan_origination")
    b = Project(name="Payments", domain="payments")
    session.add_all([a, b])
    session.flush()

    def member(project: Project, role: Role, email: str) -> uuid.UUID:
        user = User(email=f"{uuid.uuid4().hex[:6]}-{email}", display_name=email.split("@")[0])
        session.add(user)
        session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        session.flush()
        return user.id

    return {
        "a": a.id,
        "b": b.id,
        "kb_admin": member(a, Role.KB_ADMIN, "kb@example.test"),
        "analyst": member(a, Role.ANALYST, "analyst@example.test"),
        "auditor": member(a, Role.AUDITOR, "auditor@example.test"),
        "outsider": member(b, Role.ANALYST, "outsider@example.test"),
        "kb_admin_b": member(b, Role.KB_ADMIN, "kb-b@example.test"),
    }


def make_client(session_provider) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = session_provider
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def seed_item(
    client: TestClient, world: dict, key: str = "SYN-AC-1", text: str = POLICY, **source
) -> dict:
    created = client.post(
        "/api/v1/kb/sources", json={**SOURCE, **source}, headers=hdr(world["kb_admin"])
    )
    assert created.status_code == 201, created.text
    item = client.post(
        "/api/v1/kb/items",
        json={
            "normative_source_id": created.json()["id"],
            "item_key": key,
            "text": text,
            "text_origin": "synthetic",
        },
        headers=hdr(world["kb_admin"]),
    )
    assert item.status_code == 201, item.text
    return {"source": created.json(), "item": item.json()}


def scope_project_a(client: TestClient, world: dict, source_id: str) -> None:
    assert (
        client.put(
            f"/api/v1/projects/{world['a']}/kb-scope",
            json={"jurisdiction_scope": ["IN"]},
            headers=hdr(world["kb_admin"]),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/projects/{world['a']}/kb-allowlist",
            json={"normative_source_id": source_id},
            headers=hdr(world["kb_admin"]),
        ).status_code
        == 200
    )


# ===========================================================================
# SQLite: curation, scope, evidence, pages
# ===========================================================================


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

    yield from make_client(provide)


@pytest.fixture
def world(session_factory) -> dict:
    session = session_factory()
    data = build_world(session)
    session.commit()
    session.close()
    return data


def test_the_kb_administrator_adds_and_ingests_an_item(client, world) -> None:
    created = seed_item(client, world)
    item = created["item"]
    assert (item["item_key"], item["version_no"], item["status"]) == ("SYN-AC-1", 1, "active")
    assert [c["structure_label"] for c in item["chunks"]] == ["1", "1.1", "1.2"]
    for chunk in item["chunks"]:
        assert item["text"][chunk["char_start"] : chunk["char_end"]] == chunk["text"]
    assert all("embedding" not in chunk for chunk in item["chunks"]), "vectors are never returned"
    assert "embedding" not in item
    assert created["source"]["binding"] == "Binding inside one organisation"


def test_versioning_retiring_and_superseding_over_http(client, world) -> None:
    first = seed_item(client, world)["item"]
    v2 = client.post(
        f"/api/v1/kb/items/{first['id']}/versions",
        json={
            "text": POLICY.replace("quarterly", "monthly"),
            "text_origin": "synthetic",
            "reason": "tightened",
        },
        headers=hdr(world["kb_admin"]),
    ).json()
    assert v2["version_no"] == 2 and v2["kb_version"] == 2
    assert [v["status"] for v in v2["versions"]] == ["superseded", "active"]
    retired = client.post(
        f"/api/v1/kb/items/{v2['id']}/retire",
        json={"reason": "withdrawn"},
        headers=hdr(world["kb_admin"]),
    ).json()
    assert (retired["status"], retired["supersession_kind"]) == ("superseded", "retired")
    assert client.get("/api/v1/kb/version", headers=hdr(world["kb_admin"])).json() == {
        "current_kb_version": 3
    }


def test_supersede_by_another_item_over_http(client, world) -> None:
    old = seed_item(client, world, key="SYN-OLD-1")["item"]
    new = seed_item(
        client, world, key="SYN-NEW-1", text="2. Other\n2.1 A.\n2.2 B.", version="2026.2"
    )["item"]
    response = client.post(
        f"/api/v1/kb/items/{old['id']}/supersede",
        json={"superseded_by_id": new["id"], "reason": "new edition"},
        headers=hdr(world["kb_admin"]),
    )
    assert response.json()["superseded_by_id"] == new["id"]
    assert response.json()["supersession_kind"] == "replaced"


@pytest.mark.parametrize("who", ["analyst", "auditor", "outsider"])
def test_only_the_kb_administrator_reaches_the_shared_corpus(client, world, who: str) -> None:
    assert client.get("/api/v1/kb/sources", headers=hdr(world[who])).status_code == 403
    assert (
        client.post("/api/v1/kb/sources", json=SOURCE, headers=hdr(world[who])).status_code == 403
    )
    assert client.get("/ui/kb", headers=hdr(world[who])).status_code == 403


def test_licence_and_taxonomy_refusals_are_409(client, world) -> None:
    statute = client.post(
        "/api/v1/kb/sources",
        json={**SOURCE, "source_type": "statute"},
        headers=hdr(world["kb_admin"]),
    )
    assert statute.status_code == 409, "a synthetic statute would be a fake law"
    source = client.post("/api/v1/kb/sources", json=SOURCE, headers=hdr(world["kb_admin"])).json()
    verbatim = client.post(
        "/api/v1/kb/items",
        json={
            "normative_source_id": source["id"],
            "item_key": "SYN-V-1",
            "text": "x",
            "text_origin": "verbatim_extract",
        },
        headers=hdr(world["kb_admin"]),
    )
    assert verbatim.status_code == 409


def test_the_project_kb_administrator_sets_scope_and_nobody_else_can(client, world) -> None:
    source = seed_item(client, world)["source"]
    scope_project_a(client, world, source["id"])
    scope = client.get(
        f"/api/v1/projects/{world['a']}/kb-scope", headers=hdr(world["analyst"])
    ).json()
    assert scope["jurisdiction_scope"] == ["IN"]
    assert [s["id"] for s in scope["allowlisted_sources"]] == [source["id"]]

    analyst_put = client.put(
        f"/api/v1/projects/{world['a']}/kb-scope",
        json={"jurisdiction_scope": ["IN", "US"]},
        headers=hdr(world["analyst"]),
    )
    assert analyst_put.status_code == 403
    analyst_remove = client.delete(
        f"/api/v1/projects/{world['a']}/kb-allowlist/{source['id']}", headers=hdr(world["analyst"])
    )
    assert analyst_remove.status_code == 403


def test_another_projects_kb_administrator_cannot_touch_this_allowlist(client, world) -> None:
    source = seed_item(client, world)["source"]
    response = client.post(
        f"/api/v1/projects/{world['a']}/kb-allowlist",
        json={"normative_source_id": source["id"]},
        headers=hdr(world["kb_admin_b"]),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "not found", "no existence disclosure"


def test_an_outsider_sees_no_trace_of_the_project(client, world) -> None:
    for path in (
        f"/api/v1/projects/{world['a']}/kb-scope",
        f"/api/v1/projects/{world['a']}/evidence",
    ):
        response = client.get(path, headers=hdr(world["outsider"]))
        assert (response.status_code, response.json()["detail"]) == (404, "not found")


@pytest.fixture
def recorded(client, world, session_factory) -> list[uuid.UUID]:
    """Evidence recorded through the service (vector search itself needs PostgreSQL)."""
    created = seed_item(client, world)
    scope_project_a(client, world, created["source"]["id"])
    session = session_factory()
    project = session.get(Project, world["a"])
    from reqpilot.domain.models.knowledge import KnowledgeItem

    item = session.get(KnowledgeItem, uuid.UUID(created["item"]["id"]))
    analyst = Actor(
        actor_id=ActorId(world["analyst"]),
        roles_by_project={ProjectId(world["a"]): frozenset({Role.ANALYST})},
    )
    rows = EvidenceService(session, analyst).record(
        success(project, as_retrieved(session, chunks_of(session, item)))
    )
    ids = [r.id for r in rows]
    session.commit()
    session.close()
    return ids


def test_evidence_resolves_to_its_exact_span_over_http(client, world, recorded) -> None:
    citation = client.get(f"/api/v1/evidence/{recorded[1]}", headers=hdr(world["auditor"])).json()
    assert citation["quote"] == "1.1 Access to customer data is granted on a least-privilege basis."
    assert citation["binding"] == "Binding inside one organisation"
    assert citation["source_title"] == "Access Control Policy (fictional)"
    listed = client.get(
        f"/api/v1/projects/{world['a']}/evidence", headers=hdr(world["analyst"])
    ).json()
    assert [e["id"] for e in listed] == [str(i) for i in recorded]


def test_evidence_is_invisible_outside_its_project(client, world, recorded) -> None:
    response = client.get(f"/api/v1/evidence/{recorded[0]}", headers=hdr(world["outsider"]))
    assert (response.status_code, response.json()["detail"]) == (404, "not found")
    assert (
        client.get(f"/api/v1/evidence/{uuid.uuid4()}", headers=hdr(world["analyst"])).status_code
        == 404
    )


def test_citation_check_over_http(client, world, recorded) -> None:
    response = client.post(
        f"/api/v1/projects/{world['a']}/citations/check",
        json={
            "cited_evidence_ids": [str(recorded[0]), str(recorded[2]), "fabricated"],
            "allowed_evidence_ids": [str(recorded[0])],
        },
        headers=hdr(world["analyst"]),
    ).json()
    assert response["all_resolved"] is False
    assert [c["evidence_id"] for c in response["resolved"]] == [str(recorded[0])]
    assert [raw for raw, _ in response["rejected"]] == [str(recorded[2]), "fabricated"]


def test_the_knowledge_pages_render(client, world, recorded) -> None:
    kb_page = client.get("/ui/kb", headers=hdr(world["kb_admin"]))
    assert kb_page.status_code == 200 and "Access Control Policy (fictional)" in kb_page.text
    assert "not a substitute for professional or legal advice" in kb_page.text
    project_page = client.get(f"/ui/projects/{world['a']}/kb", headers=hdr(world["analyst"]))
    assert project_page.status_code == 200 and "Allowlisted sources" in project_page.text
    assert "Allowlist a source" not in project_page.text, (
        "the analyst is not shown the scope controls"
    )
    evidence_page = client.get(f"/ui/evidence/{recorded[0]}", headers=hdr(world["auditor"]))
    assert evidence_page.status_code == 200 and "not a determination" in evidence_page.text
    assert (
        client.get(f"/ui/projects/{world['a']}/kb", headers=hdr(world["outsider"])).status_code
        == 404
    )


def test_curation_through_the_ui_forms(client, world) -> None:
    posted = client.post(
        "/ui/kb/sources",
        data=dict(SOURCE),
        headers=hdr(world["kb_admin"]),
        follow_redirects=False,
    )
    assert posted.status_code == 303
    source_id = client.get("/api/v1/kb/sources", headers=hdr(world["kb_admin"])).json()[0]["id"]
    item = client.post(
        "/ui/kb/items",
        data={
            "normative_source_id": source_id,
            "item_key": "SYN-UI-1",
            "text": POLICY,
            "text_origin": "synthetic",
        },
        headers=hdr(world["kb_admin"]),
        follow_redirects=False,
    )
    assert item.status_code == 303
    page = client.get(item.headers["location"], headers=hdr(world["kb_admin"]))
    assert "SYN-UI-1" in page.text and "clause" in page.text


# ===========================================================================
# PostgreSQL: retrieval over HTTP
# ===========================================================================


@pytest.fixture
def pg_client(pg_session: Session) -> Iterator[TestClient]:
    def provide() -> Iterator[Session]:
        yield pg_session
        pg_session.flush()

    yield from make_client(provide)


@pytest.fixture
def pg_world(pg_session: Session) -> dict:
    return build_world(pg_session)


def test_retrieval_over_http_respects_the_allowlist(pg_client, pg_world) -> None:
    client, world = pg_client, pg_world
    allowed = seed_item(
        client,
        world,
        key="SYN-X-1",
        text="Customer data is encrypted at rest.",
        title="X (fictional)",
    )
    seed_item(
        client,
        world,
        key="SYN-Y-1",
        text="Customer data access is reviewed quarterly under least privilege approval.",
        title="Y (fictional)",
    )
    scope_project_a(client, world, allowed["source"]["id"])
    response = client.post(
        f"/api/v1/projects/{world['a']}/retrievals",
        json={
            "query": "customer data access reviewed quarterly least privilege approval",
            "top_k": 1,
            "record_evidence": True,
            # Extra fields are ignored: a client cannot name sources or jurisdictions.
            "normative_source_ids": [str(uuid.uuid4())],
            "jurisdiction_scope": ["US"],
        },
        headers=hdr(world["analyst"]),
    ).json()
    assert response["outcome"] == "RETRIEVAL_SUCCESS"
    assert [c["item_key"] for c in response["chunks"]] == ["SYN-X-1"]
    assert len(response["evidence_ids"]) == 1
    citation = client.get(
        f"/api/v1/evidence/{response['evidence_ids'][0]}", headers=hdr(world["analyst"])
    ).json()
    assert citation["quote"] == "Customer data is encrypted at rest."


def test_an_empty_retrieval_is_explicit_over_http(pg_client, pg_world) -> None:
    client, world = pg_client, pg_world
    response = client.post(
        f"/api/v1/projects/{world['a']}/retrievals",
        json={"query": "anything at all"},
        headers=hdr(world["analyst"]),
    ).json()
    assert response["outcome"] == "RETRIEVAL_EMPTY"
    assert response["requires_human_review"] is True
    assert response["empty_reason"] == "no_jurisdiction_scope"
    assert response["chunks"] == []


def test_recording_an_empty_retrieval_is_refused_over_http(pg_client, pg_world) -> None:
    client, world = pg_client, pg_world
    response = client.post(
        f"/api/v1/projects/{world['a']}/retrievals",
        json={"query": "anything", "record_evidence": True},
        headers=hdr(world["analyst"]),
    )
    assert response.status_code == 409, "FR-RAG-005: escalate, do not record an ungrounded result"


def test_retrieval_in_a_foreign_project_is_404(pg_client, pg_world) -> None:
    response = pg_client.post(
        f"/api/v1/projects/{pg_world['a']}/retrievals",
        json={"query": "x"},
        headers=hdr(pg_world["outsider"]),
    )
    assert (response.status_code, response.json()["detail"]) == (404, "not found")


def test_the_retrieval_page_shows_ranked_provenance(pg_client, pg_world) -> None:
    client, world = pg_client, pg_world
    allowed = seed_item(client, world, key="SYN-X-1", text="Customer data is encrypted at rest.")
    scope_project_a(client, world, allowed["source"]["id"])
    page = client.post(
        f"/ui/projects/{world['a']}/kb/retrieve",
        data={"query": "customer data encrypted", "record_evidence": "1"},
        headers=hdr(world["analyst"]),
    )
    assert page.status_code == 200
    assert "RETRIEVAL_SUCCESS" in page.text and "SYN-X-1" in page.text
    assert "Recorded 1 evidence row" in page.text
