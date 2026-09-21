"""Knowledge-base persistence, versioning, administration and audit (SQLite).

Everything here is dialect-independent. Retrieval itself - vector distance,
full-text rank, fusion over real candidates - needs PostgreSQL and is tested in
``test_p2_postgres_retrieval.py``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.kb_helpers import (
    CURATED_ON,
    actor,
    admin_service,
    chunks_of,
    make_project,
    synthetic_item,
    synthetic_source,
)

from reqpilot.domain.enums import (
    AuditEventType,
    ChunkStrategy,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    Role,
    SupersessionKind,
    TextOrigin,
)
from reqpilot.domain.errors import ImmutableRecordError, KnowledgeBaseError, LicenceViolationError
from reqpilot.domain.ids import ProjectId, chunk_id_for
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.knowledge import KnowledgeChunk, KnowledgeItem, NormativeSource
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.knowledge import (
    ItemSpec,
    KnowledgeScopeService,
    SourceSpec,
    seed_from_manifest,
)
from reqpilot.services.knowledge.admin import text_hash

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_MANIFEST = REPO_ROOT / "data" / "dev" / "kb_synthetic" / "manifest.yaml"

POLICY = """1. Access
1.1 Access to customer data is granted on a least-privilege basis.
1.2 Access rights are reviewed quarterly.
"""


@pytest.fixture
def kb(db_session: Session, retrieval_rules):
    project = make_project(db_session)
    kb_admin = actor(project.id, Role.KB_ADMIN)
    return {
        "session": db_session,
        "project": project,
        "admin_actor": kb_admin,
        "admin": admin_service(db_session, kb_admin, retrieval_rules),
        "repo": KnowledgeBaseRepository(db_session, kb_admin),
    }


def audit_types(session: Session, project_id=None) -> list[AuditEventType]:
    return [e.event_type for e in AuditService(session).list_for_project(project_id)]


# --- sources and items --------------------------------------------------------


def test_a_source_records_the_full_fr_rag_001_provenance(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy", jurisdiction="in")
    stored = kb["session"].get(NormativeSource, source.id)
    assert stored.source_type is NormativeSourceType.ORG_POLICY
    assert stored.jurisdiction == "IN", "jurisdiction codes are normalised to upper case"
    assert stored.version == "2026.1"
    assert stored.effective_date == dt.date(2025, 1, 1)
    assert stored.retrieved_at == CURATED_ON
    assert stored.licence_class is LicenceClass.SYNTHETIC
    assert stored.licence_note


def test_an_item_is_chunked_with_offsets_and_embedded(kb, hashing_embedder) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY, applicability=["access_control"])
    chunks = chunks_of(kb["session"], item)
    assert [c.structure_label for c in chunks] == ["1", "1.1", "1.2"]
    for chunk in chunks:
        assert item.text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.strategy is ChunkStrategy.CLAUSE
        assert chunk.embedding_model == hashing_embedder.model_id
        assert len(chunk.embedding) == 384
        assert chunk.id == chunk_id_for(
            item.id, chunk.ordinal, chunk.char_start, chunk.char_end, chunk.text_hash
        )
        assert chunk.text_hash == text_hash(chunk.text)
    assert item.applicability == ["access_control"]
    assert item.content_hash == text_hash(POLICY.strip())


def test_the_first_version_of_an_item_is_active_in_a_new_kb_version(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    first = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    second = synthetic_item(
        kb["admin"], source, "SYN-AC-2", "2. Logging\n2.1 Access is logged.\n2.2 Logs are kept."
    )
    assert (first.version_no, first.kb_version, first.status) == (1, 1, KnowledgeItemStatus.ACTIVE)
    assert second.kb_version == 2
    assert kb["repo"].current_kb_version() == 2


def test_versioning_supersedes_the_predecessor_in_the_next_kb_version(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    v1 = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    v2 = kb["admin"].version_item(
        v1.id,
        ItemSpec(text=POLICY.replace("quarterly", "monthly"), text_origin=TextOrigin.SYNTHETIC),
        reason="review cycle tightened",
    )
    assert (v2.item_key, v2.version_no, v2.kb_version) == ("SYN-AC-1", 2, 2)
    assert v1.status is KnowledgeItemStatus.SUPERSEDED
    assert v1.supersession_kind is SupersessionKind.VERSIONED
    assert v1.superseded_by_id == v2.id
    assert v1.superseded_in_kb_version == 2
    assert v1.active_at(1) and not v1.active_at(2)
    assert v2.active_at(2) and not v2.active_at(1)
    assert [i.version_no for i in kb["repo"].versions_of("SYN-AC-1")] == [1, 2]
    assert chunks_of(kb["session"], v1), "the predecessor's chunks are history and are kept"


def test_retiring_leaves_no_successor(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["admin"].retire_item(item.id, reason="policy withdrawn")
    assert item.status is KnowledgeItemStatus.SUPERSEDED
    assert item.supersession_kind is SupersessionKind.RETIRED
    assert item.superseded_by_id is None
    assert item.supersession_reason == "policy withdrawn"
    assert item.superseded_in_kb_version == 2


def test_supersession_by_a_different_item(kb) -> None:
    old_source = synthetic_source(kb["admin"], "Access policy", version="2025.1")
    new_source = synthetic_source(kb["admin"], "Access policy", version="2026.2")
    old = synthetic_item(kb["admin"], old_source, "SYN-AC-OLD", POLICY)
    new = synthetic_item(kb["admin"], new_source, "SYN-AC-NEW", POLICY)
    kb["admin"].supersede_item(old.id, successor_id=new.id, reason="new edition of the policy")
    assert old.supersession_kind is SupersessionKind.REPLACED
    assert old.superseded_by_id == new.id
    assert new.status is KnowledgeItemStatus.ACTIVE


def test_the_audit_trail_tells_retirement_from_replacement(kb) -> None:
    """One status and one O.2 event cover three operations; ``kind`` names which.

    An auditor reading ``KB_ITEM_SUPERSEDED`` must never have to guess whether an
    item was withdrawn or replaced, so each event names its operation and its
    successor, and the two always agree.
    """
    admin = kb["admin"]
    old_source = synthetic_source(admin, "Access policy", version="2025.1")
    new_source = synthetic_source(admin, "Access policy", version="2026.2")
    versioned = synthetic_item(admin, old_source, "SYN-AC-1", POLICY)
    successor = admin.version_item(
        versioned.id,
        ItemSpec(text=POLICY.replace("quarterly", "monthly"), text_origin=TextOrigin.SYNTHETIC),
        reason="review cycle tightened",
    )
    replaced = synthetic_item(admin, old_source, "SYN-AC-OLD", "9. Old\n9.1 Old rule.\n9.2 Old.")
    replacement = synthetic_item(admin, new_source, "SYN-AC-NEW", "9. New\n9.1 New rule.\n9.2 New.")
    admin.supersede_item(replaced.id, successor_id=replacement.id, reason="new edition")
    retired = synthetic_item(admin, old_source, "SYN-AC-GONE", "8. Gone\n8.1 A rule.\n8.2 Two.")
    admin.retire_item(retired.id, reason="policy withdrawn")

    events = {
        e.subject_id: e.payload
        for e in AuditService(kb["session"]).list_for_project(None)
        if e.event_type is AuditEventType.KB_ITEM_SUPERSEDED
    }
    assert set(events) == {str(versioned.id), str(replaced.id), str(retired.id)}

    assert events[str(versioned.id)]["kind"] == "versioned"
    assert events[str(versioned.id)]["successor_id"] == str(successor.id)
    assert successor.item_key == versioned.item_key, "a version keeps the item key"

    assert events[str(replaced.id)]["kind"] == "replaced"
    assert events[str(replaced.id)]["successor_id"] == str(replacement.id)
    assert replacement.item_key != replaced.item_key, "a replacement is a different item"

    assert events[str(retired.id)]["kind"] == "retired"
    assert events[str(retired.id)]["successor_id"] is None, "a retirement has no successor"

    for payload in events.values():
        assert payload["superseded_in_kb_version"] > 0
        assert (payload["kind"] == "retired") == (payload["successor_id"] is None)


@pytest.mark.parametrize(
    ("kind", "has_successor"),
    [
        (SupersessionKind.RETIRED, True),
        (SupersessionKind.VERSIONED, False),
        (SupersessionKind.REPLACED, False),
    ],
)
def test_the_schema_refuses_a_kind_that_contradicts_the_successor(
    kb, kind: SupersessionKind, has_successor: bool
) -> None:
    """The distinction holds in the data, not only in the service that writes it."""
    source = synthetic_source(kb["admin"], "Access policy")
    other = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["session"].add(
        KnowledgeItem(
            item_key="SYN-AC-FORGED",
            version_no=1,
            normative_source_id=source.id,
            text="forged",
            text_origin=TextOrigin.SYNTHETIC,
            content_hash=text_hash("forged"),
            applicability=[],
            kb_version=1,
            status=KnowledgeItemStatus.SUPERSEDED,
            supersession_kind=kind,
            superseded_in_kb_version=2,
            superseded_by_id=other.id if has_successor else None,
            supersession_reason="forged",
        )
    )
    with pytest.raises(IntegrityError, match="supersession_kind_matches_successor"):
        kb["session"].flush()
    kb["session"].rollback()


def test_a_superseded_item_cannot_be_versioned_retired_or_superseded_again(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    other = synthetic_item(kb["admin"], source, "SYN-AC-2", "2. Other\n2.1 A.\n2.2 B.")
    kb["admin"].retire_item(item.id, reason="withdrawn")
    with pytest.raises(KnowledgeBaseError, match="history"):
        kb["admin"].retire_item(item.id, reason="again")
    with pytest.raises(KnowledgeBaseError, match="history"):
        kb["admin"].version_item(
            item.id, ItemSpec(text="new", text_origin=TextOrigin.SYNTHETIC), reason="x"
        )
    with pytest.raises(KnowledgeBaseError, match="history"):
        kb["admin"].supersede_item(other.id, successor_id=item.id, reason="x")


def test_supersession_needs_a_reason(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    with pytest.raises(KnowledgeBaseError, match="reason"):
        kb["admin"].retire_item(item.id, reason="  ")


# --- curation rules ------------------------------------------------------------


def test_verbatim_text_is_refused_for_a_paraphrase_only_source(kb) -> None:
    """D.2: ISO/IEC-style standards store identifiers and paraphrases, never copied text."""
    standard = kb["admin"].add_source(
        SourceSpec(
            source_type=NormativeSourceType.INDUSTRY_STANDARD,
            issuing_body="A standards body",
            title="An information-security standard",
            jurisdiction="INTL",
            version="2022",
            retrieved_at=CURATED_ON,
            licence_class=LicenceClass.PARAPHRASE_ONLY,
            licence_note="Copyrighted: clause identifiers and team paraphrases only.",
        )
    )
    with pytest.raises(LicenceViolationError, match="paraphrase"):
        kb["admin"].add_item(
            standard.id,
            "STD-1",
            ItemSpec(text="copied clause", text_origin=TextOrigin.VERBATIM_EXTRACT),
        )
    paraphrase = kb["admin"].add_item(
        standard.id,
        "STD-1",
        ItemSpec(
            text="Team paraphrase: access is limited.", text_origin=TextOrigin.TEAM_PARAPHRASE
        ),
    )
    assert paraphrase.text_origin is TextOrigin.TEAM_PARAPHRASE


def test_synthetic_text_cannot_be_filed_under_a_real_source(kb) -> None:
    real = kb["admin"].add_source(
        SourceSpec(
            source_type=NormativeSourceType.CONTROL_FRAMEWORK,
            issuing_body="A public body",
            title="A public control framework",
            jurisdiction="INTL",
            version="2.0",
            retrieved_at=CURATED_ON,
            licence_class=LicenceClass.EXTRACT_PERMITTED,
            licence_note="Public domain.",
        )
    )
    with pytest.raises(LicenceViolationError):
        kb["admin"].add_item(
            real.id, "PUB-1", ItemSpec(text="invented", text_origin=TextOrigin.SYNTHETIC)
        )


@pytest.mark.parametrize(
    "source_type",
    [
        t
        for t in NormativeSourceType
        if t not in (NormativeSourceType.ORG_POLICY, NormativeSourceType.BEST_PRACTICE)
    ],
)
def test_no_synthetic_law_regulation_or_standard_can_be_created(kb, source_type) -> None:
    with pytest.raises(LicenceViolationError, match="does not invent"):
        synthetic_source(kb["admin"], "Invented instrument", source_type=source_type)


def test_the_same_text_is_never_active_twice_under_one_source(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    with pytest.raises(KnowledgeBaseError, match="dedupe"):
        synthetic_item(kb["admin"], source, "SYN-AC-9", POLICY)


def test_an_item_key_names_one_item(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    with pytest.raises(KnowledgeBaseError, match="already exists"):
        synthetic_item(kb["admin"], source, "syn-ac-1", "different text entirely")


@pytest.mark.parametrize("bad", ["x", "has space", "ÜBER-1", ""])
def test_item_keys_are_validated(kb, bad: str) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    with pytest.raises(KnowledgeBaseError, match="item key"):
        synthetic_item(kb["admin"], source, bad, POLICY)


def test_a_control_must_belong_to_the_items_source(kb) -> None:
    a = synthetic_source(kb["admin"], "Policy A")
    b = synthetic_source(kb["admin"], "Policy B")
    control = kb["admin"].add_control(
        a.id, control_ref="C-1", title="Access", paraphrase="Limit access."
    )
    with pytest.raises(KnowledgeBaseError, match="control"):
        kb["admin"].add_item(
            b.id,
            "SYN-B-1",
            ItemSpec(text="x y z", text_origin=TextOrigin.SYNTHETIC, control_id=control.id),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("jurisdiction", "India", "ISO 3166"),
        ("retrieved_at", dt.date(2999, 1, 1), "future"),
        ("licence_note", "   ", "licence_note"),
    ],
)
def test_source_metadata_is_validated(kb, field: str, value: object, message: str) -> None:
    spec: dict[str, object] = {
        "source_type": NormativeSourceType.ORG_POLICY,
        "issuing_body": "Acme Bank (fictional)",
        "title": "Policy (fictional)",
        "jurisdiction": "IN",
        "version": "1",
        "retrieved_at": CURATED_ON,
        "licence_class": LicenceClass.SYNTHETIC,
        "licence_note": "Fictional.",
    }
    spec[field] = value
    with pytest.raises(KnowledgeBaseError, match=message):
        kb["admin"].add_source(SourceSpec(**spec))


# --- audit ---------------------------------------------------------------------------


def test_every_curation_action_is_audited_with_references_only(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["admin"].version_item(
        item.id,
        ItemSpec(text=POLICY + "1.3 New.", text_origin=TextOrigin.SYNTHETIC),
        reason="added 1.3",
    )
    assert audit_types(kb["session"]) == [
        AuditEventType.KB_SOURCE_ADDED,
        AuditEventType.KB_ITEM_ADDED,
        AuditEventType.SOURCE_INGESTED,
        AuditEventType.KB_ITEM_SUPERSEDED,
        AuditEventType.KB_ITEM_ADDED,
        AuditEventType.SOURCE_INGESTED,
    ]
    events = AuditService(kb["session"]).list_for_project(None)
    for event in events:
        assert event.actor_ref == str(kb["admin_actor"].actor_id)
        rendered = str(event.payload)
        assert "least-privilege" not in rendered and "reviewed quarterly" not in rendered
    ingested = next(e for e in events if e.event_type is AuditEventType.SOURCE_INGESTED)
    assert ingested.payload["chunk_count"] == 3
    assert ingested.payload["ruleset_version"]


def test_the_curation_audit_chain_verifies(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    assert AuditService(kb["session"]).verify_project_chain(None) == (True, None)


# --- project scope --------------------------------------------------------------------


def test_scope_changes_are_audited_on_the_projects_chain(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    pid = ProjectId(kb["project"].id)
    scope = KnowledgeScopeService(kb["session"], kb["admin_actor"])
    scope.set_scope(pid, jurisdiction_scope=["in", "intl", "IN"], kb_version_pin=1)
    scope.allow(pid, source.id)
    scope.disallow(pid, source.id)
    view = scope.scope(pid)
    assert view.jurisdiction_scope == ("IN", "INTL")
    assert view.kb_version_pin == 1 and view.effective_kb_version == 1
    assert view.allowlisted_sources == ()
    events = AuditService(kb["session"]).list_for_project(kb["project"].id)
    assert [e.payload["change"] for e in events] == [
        "scope_set",
        "allowlist_added",
        "allowlist_removed",
    ]
    assert AuditService(kb["session"]).verify_project_chain(kb["project"].id) == (True, None)


@pytest.mark.parametrize("pin", [0, 5])
def test_a_pin_must_name_an_existing_kb_version(kb, pin: int) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    with pytest.raises(KnowledgeBaseError, match="existing KB version"):
        KnowledgeScopeService(kb["session"], kb["admin_actor"]).set_scope(
            ProjectId(kb["project"].id), jurisdiction_scope=["IN"], kb_version_pin=pin
        )


def test_an_unknown_or_duplicate_allowlist_entry_is_refused(kb) -> None:
    import uuid

    pid = ProjectId(kb["project"].id)
    scope = KnowledgeScopeService(kb["session"], kb["admin_actor"])
    with pytest.raises(KnowledgeBaseError, match="not found"):
        scope.allow(pid, uuid.uuid4())
    source = synthetic_source(kb["admin"], "Access policy")
    scope.allow(pid, source.id)
    with pytest.raises(KnowledgeBaseError, match="already"):
        scope.allow(pid, source.id)
    with pytest.raises(KnowledgeBaseError, match="not on"):
        scope.disallow(pid, uuid.uuid4())


# --- seeding --------------------------------------------------------------------------


def test_the_synthetic_dev_manifest_seeds_idempotently(kb) -> None:
    first = seed_from_manifest(DEV_MANIFEST, kb["admin"], kb["repo"])
    assert first.sources_added == 5 and first.items_added == 7
    again = seed_from_manifest(DEV_MANIFEST, kb["admin"], kb["repo"])
    assert (again.sources_added, again.items_added) == (0, 0)
    assert (again.sources_reused, again.items_skipped) == (5, 7)
    retention = kb["repo"].versions_of("SYN-ACME-RET-1")[0]
    assert "eight years" in retention.text, "text_file items are extracted through J.2"
    assert all(s.licence_class is LicenceClass.SYNTHETIC for s in kb["repo"].list_sources())


def test_a_manifest_cannot_read_outside_its_directory(kb, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        DEV_MANIFEST.read_text(encoding="utf-8").replace(
            "text_file: acme_retention_policy.md", "text_file: ../../etc/passwd"
        ),
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeBaseError, match="inside the manifest"):
        seed_from_manifest(manifest, kb["admin"], kb["repo"])


# --- ORM immutability (the first of two layers; PostgreSQL triggers are the second) ---


def test_a_normative_source_cannot_be_edited(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    source.title = "Rewritten history"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        kb["session"].flush()


def test_a_chunk_cannot_be_edited(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    chunk = chunks_of(kb["session"], item)[0]
    chunk.text = "something the model was never shown"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        kb["session"].flush()


def test_item_content_cannot_be_edited(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    item.text = "silently changed"
    with pytest.raises(ImmutableRecordError, match="content is immutable"):
        kb["session"].flush()


def test_items_and_chunks_are_never_deleted(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["session"].delete(item)
    with pytest.raises(ImmutableRecordError, match="never deleted"):
        kb["session"].flush()


def test_a_superseded_item_does_not_come_back(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["admin"].retire_item(item.id, reason="withdrawn")
    item.status = KnowledgeItemStatus.ACTIVE
    with pytest.raises(ImmutableRecordError, match="history"):
        kb["session"].flush()


def test_a_bulk_sql_edit_bypasses_the_orm_which_is_why_postgres_has_triggers(kb) -> None:
    """Documented limit of layer one: raw SQL skips ORM events on SQLite.

    PostgreSQL refuses the same statement at the database (see the PostgreSQL
    tests). This test exists so the limit is stated, not discovered.
    """
    source = synthetic_source(kb["admin"], "Access policy")
    kb["session"].execute(
        text("UPDATE normative_source SET title = 'x' WHERE id = :i"), {"i": source.id.hex}
    )
    title = kb["session"].scalar(
        select(NormativeSource.title).where(NormativeSource.id == source.id)
    )
    assert title == "x", "on SQLite the raw edit lands; only the ORM layer guards it"


def test_chunks_and_items_persist_across_sessions(kb) -> None:
    source = synthetic_source(kb["admin"], "Access policy")
    item = synthetic_item(kb["admin"], source, "SYN-AC-1", POLICY)
    kb["session"].expire_all()
    stored = kb["session"].get(KnowledgeItem, item.id)
    assert stored is not None and stored.text == POLICY.strip()
    count = kb["session"].scalar(
        select(text("count(*)"))
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.knowledge_item_id == item.id)
    )
    assert count == 3
    assert kb["session"].scalar(select(AuditEvent.id).limit(1)) is not None
