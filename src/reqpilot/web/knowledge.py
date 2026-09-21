"""Module M1 - the knowledge-base demonstration pages (architecture ADR-008).

Deliberately plain, like the rest of the UI: it exists to *demonstrate* curation,
allowlisting, retrieval, evidence and citation resolution, not to be a product.

As everywhere in the UI, templates hide actions the actor may not take, but that
is a convenience. Every action goes through the same services as the API, which
authorise through ``policy.can``; a hidden button is never the control.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import AppSettings, CurrentActor, DbSession, Embedder, Rules
from reqpilot.api.lookup import require_found
from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    Action,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    ResourceType,
    TextOrigin,
)
from reqpilot.domain.errors import CitationError, EvidenceIntegrityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.contracts import Citation, QueryClassification, RetrievalQuery
from reqpilot.services.knowledge import (
    EvidenceService,
    ItemSpec,
    KnowledgeAdminService,
    KnowledgeScopeService,
    RetrievalService,
    SourceSpec,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


def _may(actor: Actor, action: Action, project_id: ProjectId | None = None) -> bool:
    resource_type = ResourceType.KNOWLEDGE_ITEM if project_id is None else ResourceType.PROJECT
    return can(
        actor, action, ResourceRef(resource_type=resource_type, project_id=project_id)
    ).allowed


def _tags(raw: str) -> tuple[str, ...]:
    return tuple(t for t in (p.strip() for p in raw.split(",")) if t)


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# The shared corpus
# ---------------------------------------------------------------------------


@router.get("/kb", response_class=HTMLResponse)
def kb_home(request: Request, session: DbSession, actor: CurrentActor) -> HTMLResponse:
    """Sources and items. Only a Knowledge-Base Administrator may see the corpus."""
    repo = KnowledgeBaseRepository(session, actor)
    sources = repo.list_sources()
    return TEMPLATES.TemplateResponse(
        request,
        "kb.html",
        {
            "actor": actor,
            "sources": sources,
            "source_titles": {s.id: s.title for s in sources},
            "items": repo.list_items(),
            "kb_version": repo.current_kb_version(),
            "source_types": list(NormativeSourceType),
            "binding": SOURCE_TYPE_BINDING,
            "licences": list(LicenceClass),
            "origins": list(TextOrigin),
            "today": dt.date.today().isoformat(),
        },
    )


@router.post("/kb/sources")
def kb_add_source(
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    source_type: str = Form(...),
    issuing_body: str = Form(...),
    title: str = Form(...),
    jurisdiction: str = Form(...),
    version: str = Form(...),
    retrieved_at: str = Form(...),
    licence_class: str = Form(...),
    licence_note: str = Form(...),
    effective_date: str = Form(""),
    source_url: str = Form(""),
) -> RedirectResponse:
    KnowledgeAdminService(session, actor, embedder=embedder, rules=rules).add_source(
        SourceSpec(
            source_type=NormativeSourceType(source_type),
            issuing_body=issuing_body,
            title=title,
            jurisdiction=jurisdiction,
            version=version,
            retrieved_at=dt.date.fromisoformat(retrieved_at),
            licence_class=LicenceClass(licence_class),
            licence_note=licence_note,
            effective_date=dt.date.fromisoformat(effective_date) if effective_date else None,
            source_url=source_url or None,
        )
    )
    return _see_other("/ui/kb")


@router.post("/kb/items")
def kb_add_item(
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    normative_source_id: uuid.UUID = Form(...),
    item_key: str = Form(...),
    text: str = Form(...),
    text_origin: str = Form(...),
    title: str = Form(""),
    clause_ref: str = Form(""),
    applicability: str = Form(""),
) -> RedirectResponse:
    item = KnowledgeAdminService(session, actor, embedder=embedder, rules=rules).add_item(
        normative_source_id,
        item_key,
        ItemSpec(
            text=text,
            text_origin=TextOrigin(text_origin),
            title=title or None,
            clause_ref=clause_ref or None,
            applicability=_tags(applicability),
        ),
    )
    return _see_other(f"/ui/kb/items/{item.id}")


@router.get("/kb/items/{item_id}", response_class=HTMLResponse)
def kb_item(
    item_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    repo = KnowledgeBaseRepository(session, actor)
    item = repo.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return TEMPLATES.TemplateResponse(
        request,
        "kb_item.html",
        {
            "actor": actor,
            "item": item,
            "source": repo.get_source(item.normative_source_id),
            "chunks": repo.chunks_for_item(item.id),
            "versions": repo.versions_of(item.item_key),
            "origins": list(TextOrigin),
            "active": item.status is KnowledgeItemStatus.ACTIVE,
            "binding": SOURCE_TYPE_BINDING,
        },
    )


@router.post("/kb/items/{item_id}/versions")
def kb_version_item(
    item_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    text: str = Form(...),
    text_origin: str = Form(...),
    reason: str = Form(...),
    title: str = Form(""),
    clause_ref: str = Form(""),
    applicability: str = Form(""),
) -> RedirectResponse:
    item = KnowledgeAdminService(session, actor, embedder=embedder, rules=rules).version_item(
        item_id,
        ItemSpec(
            text=text,
            text_origin=TextOrigin(text_origin),
            title=title or None,
            clause_ref=clause_ref or None,
            applicability=_tags(applicability),
        ),
        reason=reason,
    )
    return _see_other(f"/ui/kb/items/{item.id}")


@router.post("/kb/items/{item_id}/retire")
def kb_retire_item(
    item_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    reason: str = Form(...),
) -> RedirectResponse:
    KnowledgeAdminService(session, actor, embedder=embedder, rules=rules).retire_item(
        item_id, reason=reason
    )
    return _see_other(f"/ui/kb/items/{item_id}")


# ---------------------------------------------------------------------------
# A project's scope, retrieval and evidence
# ---------------------------------------------------------------------------


def _project_kb_page(
    request: Request,
    session: DbSession,
    actor: Actor,
    project_id: uuid.UUID,
    extra: dict[str, Any] | None = None,
) -> HTMLResponse:
    pid = ProjectId(project_id)
    project = session.get(Project, project_id)
    if project is None or pid not in actor.roles_by_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    scope = KnowledgeScopeService(session, actor).scope(pid)
    may_manage = _may(actor, Action.KB_SCOPE_MANAGE, pid)
    may_retrieve = _may(actor, Action.KB_RETRIEVE, pid)
    may_read_evidence = _may(actor, Action.EVIDENCE_READ, pid)
    all_sources = (
        KnowledgeBaseRepository(session, actor).list_sources()
        if _may(actor, Action.KB_READ)
        else []
    )
    allowlisted = {s.id for s in scope.allowlisted_sources}
    context: dict[str, Any] = {
        "actor": actor,
        "project": project,
        "scope": scope,
        "binding": SOURCE_TYPE_BINDING,
        "may_manage": may_manage,
        "may_retrieve": may_retrieve,
        "addable_sources": [s for s in all_sources if s.id not in allowlisted],
        "evidence": EvidenceService(session, actor).list(pid) if may_read_evidence else [],
        "result": None,
        "query": "",
    }
    context.update(extra or {})
    return TEMPLATES.TemplateResponse(request, "project_kb.html", context)


@router.get("/projects/{project_id}/kb", response_class=HTMLResponse)
def project_kb(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    return _project_kb_page(request, session, actor, project_id)


@router.post("/projects/{project_id}/kb/scope")
def project_kb_scope(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    jurisdiction_scope: str = Form(""),
    kb_version_pin: str = Form(""),
) -> RedirectResponse:
    KnowledgeScopeService(session, actor).set_scope(
        ProjectId(project_id),
        jurisdiction_scope=_tags(jurisdiction_scope),
        kb_version_pin=int(kb_version_pin) if kb_version_pin.strip() else None,
    )
    return _see_other(f"/ui/projects/{project_id}/kb")


@router.post("/projects/{project_id}/kb/allowlist")
def project_kb_allow(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    normative_source_id: uuid.UUID = Form(...),
) -> RedirectResponse:
    KnowledgeScopeService(session, actor).allow(ProjectId(project_id), normative_source_id)
    return _see_other(f"/ui/projects/{project_id}/kb")


@router.post("/projects/{project_id}/kb/allowlist/{source_id}/remove")
def project_kb_disallow(
    project_id: uuid.UUID, source_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RedirectResponse:
    KnowledgeScopeService(session, actor).disallow(ProjectId(project_id), source_id)
    return _see_other(f"/ui/projects/{project_id}/kb")


@router.post("/projects/{project_id}/kb/retrieve", response_class=HTMLResponse)
def project_kb_retrieve(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    embedder: Embedder,
    rules: Rules,
    settings: AppSettings,
    query: str = Form(...),
    applicability: str = Form(""),
    record_evidence: str = Form(""),
) -> HTMLResponse:
    result = RetrievalService(
        session, actor, embedder=embedder, rules=rules, default_top_k=settings.retrieval_top_k
    ).retrieve(
        RetrievalQuery(
            project_id=project_id,
            text=query,
            classification=QueryClassification(applicability=frozenset(_tags(applicability))),
        )
    )
    recorded = []
    if record_evidence and result.chunks:
        recorded = EvidenceService(session, actor).record(result)
    return _project_kb_page(
        request,
        session,
        actor,
        project_id,
        {"result": result, "query": query, "recorded": recorded},
    )


@router.get("/evidence/{evidence_id}", response_class=HTMLResponse)
def evidence_view(
    evidence_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    """A resolved citation: the exact span, and everything J.5 says it must carry."""
    service = EvidenceService(session, actor)

    def load(pid: ProjectId) -> Citation | None:
        try:
            return service.describe(pid, evidence_id)
        except EvidenceIntegrityError:
            raise
        except CitationError:
            return None

    _pid, citation = require_found(actor, Action.EVIDENCE_READ, ResourceType.EVIDENCE, load)
    return TEMPLATES.TemplateResponse(
        request, "evidence.html", {"actor": actor, "citation": citation}
    )
