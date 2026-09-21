"""Knowledge-base security: every way P2 could be cheated, and why each fails.

Covers unauthorised curation, cross-project scope manipulation, repository-level
bypass attempts, forged identifiers, licence and taxonomy abuse, and audit
tampering. Retrieval-query exclusion of non-allowlisted sources is proved against
a live PostgreSQL in ``tests/integration/test_p2_postgres_retrieval.py``; the
tests here run anywhere.
"""

from __future__ import annotations

import datetime as dt
import inspect
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.kb_helpers import (
    CURATED_ON,
    actor,
    admin_service,
    allowlist,
    as_retrieved,
    chunks_of,
    make_project,
    success,
    synthetic_item,
    synthetic_source,
)

from reqpilot.domain.enums import (
    ActorKind,
    LicenceClass,
    NormativeSourceType,
    Role,
    TextOrigin,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    CitationError,
    EvidenceIntegrityError,
    LicenceViolationError,
    ProjectIsolationError,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.knowledge import Evidence, NormativeSource, SourceAllowlist
from reqpilot.domain.refs import EvidenceKind
from reqpilot.repositories.knowledge import (
    EvidenceRepository,
    KnowledgeBaseRepository,
    KnowledgeScopeRepository,
    RetrievalRepository,
    scoped_chunks,
)
from reqpilot.retrieval.contracts import RetrievalQuery
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.audit import AuditService
from reqpilot.services.knowledge import (
    EvidenceService,
    ItemSpec,
    KnowledgeScopeService,
    RetrievalService,
    SourceSpec,
)

pytestmark = pytest.mark.security

POLICY = (
    "1. Access\n1.1 Customer data access is least-privilege.\n1.2 Rights are reviewed quarterly."
)


@pytest.fixture
def world(db_session: Session, retrieval_rules):
    a = make_project(db_session, "Project A")
    b = make_project(db_session, "Project B")
    kb_admin = actor(a.id, Role.KB_ADMIN)
    admin = admin_service(db_session, kb_admin, retrieval_rules)
    source = synthetic_source(admin, "Access policy")
    item = synthetic_item(admin, source, "SYN-AC-1", POLICY)
    allowlist(db_session, kb_admin, a, source)
    return {
        "session": db_session,
        "rules": retrieval_rules,
        "a": a,
        "b": b,
        "kb_admin": kb_admin,
        "kb_admin_b": actor(b.id, Role.KB_ADMIN),
        "admin": admin,
        "source": source,
        "item": item,
        "analyst": actor(a.id, Role.ANALYST),
        "outsider": actor(b.id, Role.ANALYST),
    }


SPEC = SourceSpec(
    source_type=NormativeSourceType.ORG_POLICY,
    issuing_body="Acme Bank (fictional)",
    title="Another policy (fictional)",
    jurisdiction="IN",
    version="1",
    retrieved_at=CURATED_ON,
    licence_class=LicenceClass.SYNTHETIC,
    licence_note="Fictional.",
)


# --- unauthorised curation -------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        Role.ANALYST,
        Role.COMPLIANCE_OFFICER,
        Role.SECURITY_REVIEWER,
        Role.PROJECT_MANAGER,
        Role.AUDITOR,
        Role.STAKEHOLDER,
    ],
)
def test_only_a_kb_administrator_may_ingest(world, role: Role) -> None:
    intruder = admin_service(world["session"], actor(world["a"].id, role), world["rules"])
    with pytest.raises(AuthorizationError):
        intruder.add_source(SPEC)
    with pytest.raises(AuthorizationError):
        intruder.add_item(
            world["source"].id, "SYN-NEW-1", ItemSpec(text="x y", text_origin=TextOrigin.SYNTHETIC)
        )


def test_only_a_kb_administrator_may_version_or_retire(world) -> None:
    intruder = admin_service(world["session"], world["analyst"], world["rules"])
    with pytest.raises(AuthorizationError):
        intruder.retire_item(world["item"].id, reason="sabotage")
    with pytest.raises(AuthorizationError):
        intruder.version_item(
            world["item"].id,
            ItemSpec(text="rewritten", text_origin=TextOrigin.SYNTHETIC),
            reason="x",
        )


def test_an_agent_cannot_curate_even_holding_the_kb_admin_role(world) -> None:
    """E.1: agent roles write proposals only. The policy refuses by actor kind."""
    agent = actor(world["a"].id, Role.KB_ADMIN, kind=ActorKind.AGENT_ROLE)
    with pytest.raises(AuthorizationError, match="human decision"):
        admin_service(world["session"], agent, world["rules"]).add_source(SPEC)
    with pytest.raises(AuthorizationError, match="human decision"):
        KnowledgeScopeService(world["session"], agent).allow(
            ProjectId(world["a"].id), world["source"].id
        )


# --- cross-project scope manipulation ----------------------------------------------


def test_another_projects_kb_administrator_cannot_touch_this_scope(world) -> None:
    other = KnowledgeScopeService(world["session"], world["kb_admin_b"])
    pid = ProjectId(world["a"].id)
    with pytest.raises(ProjectIsolationError):
        other.allow(pid, world["source"].id)
    with pytest.raises(ProjectIsolationError):
        other.disallow(pid, world["source"].id)
    with pytest.raises(ProjectIsolationError):
        other.set_scope(pid, jurisdiction_scope=["US"], kb_version_pin=None)
    with pytest.raises(ProjectIsolationError):
        other.scope(pid)


def test_the_analyst_cannot_widen_their_own_grounding(world) -> None:
    scope = KnowledgeScopeService(world["session"], world["analyst"])
    with pytest.raises(AuthorizationError):
        scope.set_scope(
            ProjectId(world["a"].id), jurisdiction_scope=["IN", "US"], kb_version_pin=None
        )
    with pytest.raises(AuthorizationError):
        scope.disallow(ProjectId(world["a"].id), world["source"].id)


# --- direct repository bypass -----------------------------------------------------


def test_the_corpus_repository_authorises_every_write(world) -> None:
    repo = KnowledgeBaseRepository(world["session"], world["analyst"])
    with pytest.raises(AuthorizationError):
        repo.add(NormativeSource())
    with pytest.raises(AuthorizationError):
        repo.list_sources()
    with pytest.raises(AuthorizationError):
        repo.mark_superseded(world["item"], supersession_reason="x")


def test_the_repository_writes_only_supersession_columns(world) -> None:
    repo = KnowledgeBaseRepository(world["session"], world["kb_admin"])
    with pytest.raises(ValueError, match="not supersession fields"):
        repo.mark_superseded(world["item"], text="rewritten by the back door")


def test_scope_and_retrieval_repositories_are_project_scoped(world) -> None:
    with pytest.raises(ProjectIsolationError):
        RetrievalRepository(world["session"], world["outsider"]).load_scope(
            ProjectId(world["a"].id), dt.date.today()
        )
    with pytest.raises(AuthorizationError):
        KnowledgeScopeRepository(world["session"], world["analyst"]).add(
            SourceAllowlist(project_id=world["a"].id, normative_source_id=world["source"].id)
        )


def test_evidence_rows_cannot_be_smuggled_into_another_project(world) -> None:
    repo = EvidenceRepository(world["session"], world["analyst"])
    foreign = Evidence(
        project_id=world["b"].id,
        kind=EvidenceKind.KNOWLEDGE_ITEM,
        target_id=world["item"].id,
        char_start=0,
        char_end=1,
        quote="x",
        quote_hash="0" * 64,
        retrieval_id=uuid.uuid4(),
        rank=1,
        retrieval_score=1.0,
        kb_version=1,
        embedding_model="m",
        ruleset_version="1",
        query_hash="0" * 64,
    )
    with pytest.raises(ValueError, match="must belong"):
        repo.append(ProjectId(world["a"].id), [foreign])


def test_the_scope_function_offers_no_way_to_skip_the_allowlist() -> None:
    """There is no flag, keyword or default that removes the allowlist join."""
    parameters = set(inspect.signature(scoped_chunks).parameters)
    assert parameters == {"stmt", "scope", "source_types", "applicability", "embedding_model"}


def test_retrieval_for_a_project_the_actor_does_not_belong_to_is_refused(world) -> None:
    service = RetrievalService(
        world["session"],
        world["analyst"],
        embedder=HashingEmbeddingProvider(),
        rules=world["rules"],
    )
    for project_id in (world["b"].id, uuid.uuid4()):
        with pytest.raises(ProjectIsolationError):
            service.retrieve(RetrievalQuery(project_id=project_id, text="customer data"))


# --- forged identifiers and leaked history --------------------------------------------


def test_forged_evidence_ids_resolve_to_nothing(world) -> None:
    service = EvidenceService(world["session"], world["analyst"])
    forged = [uuid.uuid4(), uuid.uuid4()]
    check = service.check_citations(
        ProjectId(world["a"].id), [str(f) for f in forged], allowed_evidence_ids=set(forged)
    )
    assert check.resolved == () and len(check.rejected) == 2
    with pytest.raises(CitationError):
        service.describe(ProjectId(world["a"].id), forged[0])


def test_a_retired_item_cannot_leak_back_as_new_evidence(world) -> None:
    chunks = chunks_of(world["session"], world["item"])
    result = success(world["a"], as_retrieved(world["session"], chunks))
    world["admin"].retire_item(world["item"].id, reason="withdrawn")
    with pytest.raises(EvidenceIntegrityError):
        EvidenceService(world["session"], world["analyst"]).record(result)


def test_a_source_removed_from_the_allowlist_cannot_become_new_evidence(world) -> None:
    chunks = chunks_of(world["session"], world["item"])
    result = success(world["a"], as_retrieved(world["session"], chunks))
    KnowledgeScopeService(world["session"], world["kb_admin"]).disallow(
        ProjectId(world["a"].id), world["source"].id
    )
    with pytest.raises(EvidenceIntegrityError):
        EvidenceService(world["session"], world["analyst"]).record(result)


# --- licence and taxonomy abuse ----------------------------------------------------


def test_copied_standard_text_is_refused(world) -> None:
    standard = world["admin"].add_source(
        SourceSpec(
            source_type=NormativeSourceType.INDUSTRY_STANDARD,
            issuing_body="A standards body",
            title="A copyrighted standard",
            jurisdiction="INTL",
            version="2022",
            retrieved_at=CURATED_ON,
            licence_class=LicenceClass.PARAPHRASE_ONLY,
            licence_note="Identifiers and paraphrases only.",
        )
    )
    with pytest.raises(LicenceViolationError):
        world["admin"].add_item(
            standard.id, "STD-A-5", ItemSpec(text="copied", text_origin=TextOrigin.VERBATIM_EXTRACT)
        )


def test_a_fake_regulation_cannot_be_created(world) -> None:
    for fake in (NormativeSourceType.STATUTE, NormativeSourceType.REGULATORY_DIRECTION):
        with pytest.raises(LicenceViolationError, match="does not invent"):
            synthetic_source(world["admin"], "Invented rule", source_type=fake)


# --- audit ---------------------------------------------------------------------------------


def test_tampering_with_a_curation_audit_event_is_detected(world) -> None:
    """ADR-010: the hash chain exposes an edited payload (SQLite has no trigger to stop it)."""
    audit = AuditService(world["session"])
    assert audit.verify_project_chain(None) == (True, None)
    target = audit.list_for_project(None)[1]
    world["session"].execute(
        text("UPDATE audit_event SET payload = :p WHERE id = :i"),
        {"p": '{"item_key": "SOMETHING-ELSE"}', "i": target.id.hex},
    )
    world["session"].expire_all()
    ok, first_bad = audit.verify_project_chain(None)
    assert not ok and first_bad is not None


def test_scope_changes_leave_an_audit_trail_on_the_project(world) -> None:
    events = AuditService(world["session"]).list_for_project(world["a"].id)
    assert {e.payload["change"] for e in events} == {"scope_set", "allowlist_added"}
    assert all(e.actor_ref == str(world["kb_admin"].actor_id) for e in events)


def test_no_audit_payload_carries_knowledge_text(world) -> None:
    session = world["session"]
    payloads = [str(e.payload) for e in AuditService(session).list_for_project(None)]
    payloads += [str(e.payload) for e in AuditService(session).list_for_project(world["a"].id)]
    for fragment in ("least-privilege", "reviewed quarterly", "Customer data access"):
        assert not any(fragment in p for p in payloads)
