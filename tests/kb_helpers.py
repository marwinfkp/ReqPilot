"""Shared builders for knowledge-base tests.

Every source these helpers create is **synthetic and labelled fictional**: an
organisational policy of a bank that does not exist, or a team practice note.
No test invents a law, a regulation or a standard - the admin service refuses a
synthetic source of those types, and these helpers never try.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ActorKind,
    LicenceClass,
    NormativeSourceType,
    Role,
    TextOrigin,
)
from reqpilot.domain.ids import ActorId, ProjectId, new_retrieval_id
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.knowledge import KnowledgeChunk, KnowledgeItem, NormativeSource
from reqpilot.domain.policy import Actor
from reqpilot.retrieval.contracts import (
    QueryClassification,
    RetrievalOutcome,
    RetrievalResult,
    RetrievedChunk,
)
from reqpilot.retrieval.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from reqpilot.retrieval.rules import RetrievalRules
from reqpilot.services.knowledge import (
    ItemSpec,
    KnowledgeAdminService,
    KnowledgeScopeService,
    SourceSpec,
)

CURATED_ON = dt.date(2026, 1, 15)
IN_FORCE = dt.date(2025, 1, 1)


def actor(project_id: uuid.UUID, *roles: Role, kind: ActorKind = ActorKind.HUMAN) -> Actor:
    return Actor(
        actor_id=ActorId(uuid.uuid4()),
        kind=kind,
        roles_by_project={ProjectId(project_id): frozenset(roles)},
    )


def make_project(
    session: Session, name: str = "Loan Origination", jurisdictions: Sequence[str] = ()
) -> Project:
    project = Project(name=name, domain="loan_origination", jurisdiction_scope=list(jurisdictions))
    session.add(project)
    session.flush()
    return project


def admin_service(
    session: Session,
    kb_admin: Actor,
    rules: RetrievalRules,
    embedder: EmbeddingProvider | None = None,
) -> KnowledgeAdminService:
    return KnowledgeAdminService(
        session, kb_admin, embedder=embedder or HashingEmbeddingProvider(), rules=rules
    )


def synthetic_source(
    admin: KnowledgeAdminService,
    title: str,
    *,
    jurisdiction: str = "IN",
    source_type: NormativeSourceType = NormativeSourceType.ORG_POLICY,
    effective_date: dt.date | None = IN_FORCE,
    version: str = "2026.1",
) -> NormativeSource:
    return admin.add_source(
        SourceSpec(
            source_type=source_type,
            issuing_body="Acme Bank (fictional)",
            title=f"{title} (fictional)",
            jurisdiction=jurisdiction,
            version=version,
            retrieved_at=CURATED_ON,
            licence_class=LicenceClass.SYNTHETIC,
            licence_note="Fictional policy written for ReqPilot tests.",
            effective_date=effective_date,
        )
    )


def synthetic_item(
    admin: KnowledgeAdminService,
    source: NormativeSource,
    key: str,
    text: str,
    *,
    applicability: Sequence[str] = (),
    clause_ref: str | None = None,
) -> KnowledgeItem:
    return admin.add_item(
        source.id,
        key,
        ItemSpec(
            text=text,
            text_origin=TextOrigin.SYNTHETIC,
            applicability=tuple(applicability),
            clause_ref=clause_ref,
        ),
    )


def allowlist(
    session: Session,
    kb_admin: Actor,
    project: Project,
    *sources: NormativeSource,
    jurisdictions: Sequence[str] = ("IN",),
    pin: int | None = None,
) -> None:
    scope = KnowledgeScopeService(session, kb_admin)
    scope.set_scope(ProjectId(project.id), jurisdiction_scope=jurisdictions, kb_version_pin=pin)
    for source in sources:
        scope.allow(ProjectId(project.id), source.id)


def chunks_of(session: Session, item: KnowledgeItem) -> list[KnowledgeChunk]:
    stmt = (
        select(KnowledgeChunk)
        .where(KnowledgeChunk.knowledge_item_id == item.id)
        .order_by(KnowledgeChunk.ordinal)
    )
    return list(session.scalars(stmt))


def as_retrieved(session: Session, chunks: Sequence[KnowledgeChunk]) -> tuple[RetrievedChunk, ...]:
    """Describe stored chunks exactly as the retrieval service would.

    Lets the evidence path be tested on SQLite, where vector search itself
    cannot run: the evidence service must accept these only if the database
    agrees they are in the project's scope.
    """
    out = []
    for rank, chunk in enumerate(chunks, start=1):
        item = session.get(KnowledgeItem, chunk.knowledge_item_id)
        assert item is not None
        source = session.get(NormativeSource, item.normative_source_id)
        assert source is not None
        out.append(
            RetrievedChunk(
                rank=rank,
                chunk_id=chunk.id,
                knowledge_item_id=item.id,
                item_key=item.item_key,
                item_version_no=item.version_no,
                item_title=item.title,
                clause_ref=item.clause_ref,
                kb_version=item.kb_version,
                normative_source_id=source.id,
                source_title=source.title,
                source_type=source.source_type,
                issuing_body=source.issuing_body,
                jurisdiction=source.jurisdiction,
                source_version=source.version,
                effective_date=source.effective_date,
                retrieved_at=source.retrieved_at,
                source_url=source.source_url,
                licence_class=source.licence_class,
                text_origin=item.text_origin,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                text=chunk.text,
                strategy=chunk.strategy,
                structure_label=chunk.structure_label,
                fused_score=1.0 / (60 + rank),
                vector_similarity=0.9,
                vector_rank=rank,
                keyword_rank=None,
            )
        )
    return tuple(out)


def success(
    project: Project, chunks: tuple[RetrievedChunk, ...], *, kb_version: int = 1
) -> RetrievalResult:
    return RetrievalResult(
        retrieval_id=new_retrieval_id(),
        project_id=project.id,
        outcome=RetrievalOutcome.SUCCESS,
        requires_human_review=False,
        query_hash="0" * 64,
        as_of=dt.date(2026, 6, 1),
        kb_version=kb_version,
        kb_version_pinned=False,
        embedding_model="reqpilot/hashing-384-v1",
        ruleset_version="1.0.0",
        classification=QueryClassification(),
        chunks=chunks,
    )
