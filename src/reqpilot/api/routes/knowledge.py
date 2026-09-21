"""Knowledge-base, scope, retrieval and evidence endpoints (architecture S, J).

Every handler goes through a service, and every service authorises through
``policy.can`` in the repository layer. Nothing here touches a model directly.

Two surfaces, with different scoping:

* ``/kb/...`` is the **shared corpus**. Only a Knowledge-Base Administrator may
  read or curate it (``FR-RAG-006``); anyone else gets 403.
* ``/projects/{id}/...`` is a **project's view**: its allowlist and scope, its
  retrievals, its evidence. A caller outside the project gets 404, exactly as
  for a project that does not exist (no existence disclosure).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from reqpilot.api.dependencies import AppSettings, CurrentActor, DbSession, Embedder, Rules
from reqpilot.api.knowledge_schemas import (
    AllowlistIn,
    CitationCheckIn,
    CitationCheckOut,
    ControlIn,
    ControlOut,
    EvidenceOut,
    ItemDetailOut,
    ItemIn,
    ItemOut,
    KbVersionOut,
    RetireIn,
    RetrievalIn,
    RetrievalOut,
    RetrievedChunkOut,
    ScopeIn,
    ScopeOut,
    SourceIn,
    SourceOut,
    SupersedeIn,
    VersionIn,
)
from reqpilot.api.lookup import require_found
from reqpilot.domain.enums import Action, KnowledgeItemStatus, ResourceType
from reqpilot.domain.errors import CitationError, EvidenceIntegrityError, KnowledgeBaseError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.contracts import Citation, QueryClassification, RetrievalQuery
from reqpilot.retrieval.embeddings import EmbeddingProvider
from reqpilot.retrieval.rules import RetrievalRules
from reqpilot.services.knowledge import (
    EvidenceService,
    ItemSpec,
    KnowledgeAdminService,
    KnowledgeScopeService,
    ProjectKnowledgeScope,
    RetrievalService,
    SourceSpec,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def _admin(
    session: DbSession, actor: Actor, embedder: EmbeddingProvider, rules: RetrievalRules
) -> KnowledgeAdminService:
    return KnowledgeAdminService(session, actor, embedder=embedder, rules=rules)


def _item_detail(repo: KnowledgeBaseRepository, item_id: uuid.UUID) -> ItemDetailOut:
    item = repo.get_item(item_id)
    if item is None:
        raise KnowledgeBaseError(f"knowledge item {item_id} not found")
    return ItemDetailOut.of(item, repo.chunks_for_item(item.id), repo.versions_of(item.item_key))


def _scope_out(scope: ProjectKnowledgeScope) -> ScopeOut:
    return ScopeOut(
        project_id=scope.project_id,
        jurisdiction_scope=list(scope.jurisdiction_scope),
        kb_version_pin=scope.kb_version_pin,
        current_kb_version=scope.current_kb_version,
        effective_kb_version=scope.effective_kb_version,
        allowlisted_sources=[SourceOut.of(s) for s in scope.allowlisted_sources],
    )


# ---------------------------------------------------------------------------
# The shared corpus (KB administrator only)
# ---------------------------------------------------------------------------


@router.get("/kb/version", response_model=KbVersionOut)
def kb_version(session: DbSession, actor: CurrentActor) -> KbVersionOut:
    return KbVersionOut(
        current_kb_version=KnowledgeBaseRepository(session, actor).current_kb_version()
    )


@router.get("/kb/sources", response_model=list[SourceOut])
def list_sources(session: DbSession, actor: CurrentActor) -> list[SourceOut]:
    return [SourceOut.of(s) for s in KnowledgeBaseRepository(session, actor).list_sources()]


@router.post("/kb/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def add_source(
    payload: SourceIn, session: DbSession, actor: CurrentActor, embedder: Embedder, rules: Rules
) -> SourceOut:
    source = _admin(session, actor, embedder, rules).add_source(SourceSpec(**payload.model_dump()))
    return SourceOut.of(source)


@router.post("/kb/controls", response_model=ControlOut, status_code=status.HTTP_201_CREATED)
def add_control(
    payload: ControlIn, session: DbSession, actor: CurrentActor, embedder: Embedder, rules: Rules
) -> ControlOut:
    control = _admin(session, actor, embedder, rules).add_control(
        payload.normative_source_id,
        control_ref=payload.control_ref,
        title=payload.title,
        paraphrase=payload.paraphrase,
        applicability=payload.applicability,
    )
    return ControlOut.model_validate(control)


@router.get("/kb/items", response_model=list[ItemOut])
def list_items(
    session: DbSession, actor: CurrentActor, status: KnowledgeItemStatus | None = None
) -> list[ItemOut]:
    items = KnowledgeBaseRepository(session, actor).list_items(status)
    return [ItemOut.model_validate(i) for i in items]


@router.get("/kb/items/{item_id}", response_model=ItemDetailOut)
def get_item(item_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> ItemDetailOut:
    return _item_detail(KnowledgeBaseRepository(session, actor), item_id)


@router.post("/kb/items", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
def add_item(
    payload: ItemIn, session: DbSession, actor: CurrentActor, embedder: Embedder, rules: Rules
) -> ItemDetailOut:
    """Add version 1 of an item and ingest it (chunk, embed, persist, audit)."""
    item = _admin(session, actor, embedder, rules).add_item(
        payload.normative_source_id,
        payload.item_key,
        ItemSpec(
            text=payload.text,
            text_origin=payload.text_origin,
            title=payload.title,
            clause_ref=payload.clause_ref,
            applicability=tuple(payload.applicability),
            control_id=payload.control_id,
        ),
    )
    return _item_detail(KnowledgeBaseRepository(session, actor), item.id)


@router.post(
    "/kb/items/{item_id}/versions",
    response_model=ItemDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def version_item(
    item_id: uuid.UUID,
    payload: VersionIn,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
) -> ItemDetailOut:
    """A new version; the current one becomes superseded (``FR-RAG-006`` version)."""
    item = _admin(session, actor, embedder, rules).version_item(
        item_id,
        ItemSpec(
            text=payload.text,
            text_origin=payload.text_origin,
            title=payload.title,
            clause_ref=payload.clause_ref,
            applicability=tuple(payload.applicability),
            control_id=payload.control_id,
        ),
        reason=payload.reason,
        normative_source_id=payload.normative_source_id,
    )
    return _item_detail(KnowledgeBaseRepository(session, actor), item.id)


@router.post("/kb/items/{item_id}/retire", response_model=ItemOut)
def retire_item(
    item_id: uuid.UUID,
    payload: RetireIn,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
) -> ItemOut:
    """Withdraw an item with no successor (``FR-RAG-006`` retire)."""
    item = _admin(session, actor, embedder, rules).retire_item(item_id, reason=payload.reason)
    return ItemOut.model_validate(item)


@router.post("/kb/items/{item_id}/supersede", response_model=ItemOut)
def supersede_item(
    item_id: uuid.UUID,
    payload: SupersedeIn,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
) -> ItemOut:
    """Supersede an item by a different existing item (architecture S)."""
    item = _admin(session, actor, embedder, rules).supersede_item(
        item_id, successor_id=payload.superseded_by_id, reason=payload.reason
    )
    return ItemOut.model_validate(item)


# ---------------------------------------------------------------------------
# A project's scope, retrievals and evidence
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/kb-scope", response_model=ScopeOut)
def get_scope(project_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> ScopeOut:
    return _scope_out(KnowledgeScopeService(session, actor).scope(ProjectId(project_id)))


@router.put("/projects/{project_id}/kb-scope", response_model=ScopeOut)
def set_scope(
    project_id: uuid.UUID, payload: ScopeIn, session: DbSession, actor: CurrentActor
) -> ScopeOut:
    scope = KnowledgeScopeService(session, actor).set_scope(
        ProjectId(project_id),
        jurisdiction_scope=payload.jurisdiction_scope,
        kb_version_pin=payload.kb_version_pin,
    )
    return _scope_out(scope)


@router.post("/projects/{project_id}/kb-allowlist", response_model=ScopeOut)
def allow_source(
    project_id: uuid.UUID, payload: AllowlistIn, session: DbSession, actor: CurrentActor
) -> ScopeOut:
    service = KnowledgeScopeService(session, actor)
    service.allow(ProjectId(project_id), payload.normative_source_id)
    return _scope_out(service.scope(ProjectId(project_id)))


@router.delete("/projects/{project_id}/kb-allowlist/{source_id}", response_model=ScopeOut)
def disallow_source(
    project_id: uuid.UUID, source_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ScopeOut:
    service = KnowledgeScopeService(session, actor)
    service.disallow(ProjectId(project_id), source_id)
    return _scope_out(service.scope(ProjectId(project_id)))


@router.post("/projects/{project_id}/retrievals", response_model=RetrievalOut)
def retrieve(
    project_id: uuid.UUID,
    payload: RetrievalIn,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    settings: AppSettings,
) -> RetrievalOut:
    """Run a retrieval; optionally record its result as evidence in the same transaction."""
    result = RetrievalService(
        session, actor, embedder=embedder, rules=rules, default_top_k=settings.retrieval_top_k
    ).retrieve(
        RetrievalQuery(
            project_id=project_id,
            text=payload.query,
            classification=QueryClassification(
                source_types=frozenset(payload.source_types),
                applicability=frozenset(payload.applicability),
                requirement_category=payload.requirement_category,
            ),
            as_of=payload.as_of,
            top_k=payload.top_k,
        )
    )
    evidence_ids: list[uuid.UUID] = []
    if payload.record_evidence:
        rows = EvidenceService(session, actor).record(result, graph_run_id=payload.graph_run_id)
        evidence_ids = [row.id for row in rows]
    return RetrievalOut(
        retrieval_id=result.retrieval_id,
        outcome=result.outcome,
        empty_reason=result.empty_reason,
        requires_human_review=result.requires_human_review,
        as_of=result.as_of,
        kb_version=result.kb_version,
        kb_version_pinned=result.kb_version_pinned,
        embedding_model=result.embedding_model,
        ruleset_version=result.ruleset_version,
        chunks=[RetrievedChunkOut.of(c) for c in result.chunks],
        evidence_ids=evidence_ids,
    )


@router.get("/projects/{project_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    retrieval_id: uuid.UUID | None = None,
    graph_run_id: uuid.UUID | None = None,
) -> list[EvidenceOut]:
    rows = EvidenceService(session, actor).list(
        ProjectId(project_id), retrieval_id=retrieval_id, graph_run_id=graph_run_id
    )
    return [EvidenceOut.of(e) for e in rows]


@router.get("/evidence/{evidence_id}", response_model=Citation)
def get_evidence(evidence_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> Citation:
    """Resolve one evidence row to its exact source span, among the actor's projects."""
    service = EvidenceService(session, actor)

    def load(pid: ProjectId) -> Citation | None:
        try:
            return service.describe(pid, evidence_id)
        except EvidenceIntegrityError:
            raise  # tampering is reported, never disguised as "not found"
        except CitationError:
            return None

    _pid, citation = require_found(actor, Action.EVIDENCE_READ, ResourceType.EVIDENCE, load)
    return citation


@router.post("/projects/{project_id}/citations/check", response_model=CitationCheckOut)
def check_citations(
    project_id: uuid.UUID, payload: CitationCheckIn, session: DbSession, actor: CurrentActor
) -> CitationCheckOut:
    """Validate cited evidence ids against the run's allowed set (``FR-RAG-003``)."""
    check = EvidenceService(session, actor).check_citations(
        ProjectId(project_id),
        payload.cited_evidence_ids,
        allowed_evidence_ids=frozenset(payload.allowed_evidence_ids),
    )
    return CitationCheckOut(
        all_resolved=check.all_resolved,
        resolved=list(check.resolved),
        rejected=list(check.rejected),
    )
