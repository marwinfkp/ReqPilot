"""Knowledge base management (RAG sources)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.database import get_db
from app.models import DocumentChunk, KnowledgeDocument, User
from app.schemas import DocumentUpdate
from app.security import audit, get_current_user, has_permission, require
from app.serializers import row_dict
from app.services import rag
from app.services.documents import read_upload_with_pages

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
DOC_TYPES = set(rag.DOC_TYPES)  # + engineering_guideline and domain_glossary (used by the engineering agents)
AUTHENTICITY = {"official", "illustrative", "reference"}


def doc_dict(doc: KnowledgeDocument) -> dict:
    data = row_dict(doc)
    data["chunk_count"] = len(doc.chunks)
    data["embedded_chunks"] = sum(1 for c in doc.chunks if c.embedding is not None)
    data["suspicious_chunks"] = sum(1 for c in doc.chunks if c.suspicious)
    return data


@router.get("/documents")
def list_documents(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    docs = db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.authenticity, KnowledgeDocument.name)).all()
    return [doc_dict(d) for d in docs]


@router.get("/documents/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    doc = db.get(KnowledgeDocument, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    chunks = [{"id": c.id, "section_ref": c.section_ref, "title": c.title, "text": c.text, "topics": c.topics,
               "suspicious": c.suspicious, "embedded": c.embedding is not None, "page": c.page} for c in doc.chunks]
    return {**doc_dict(doc), "chunks": chunks}


@router.post("/documents")
async def upload_document(
    request: Request, file: UploadFile = File(...), name: str = Form(""),
    source_organisation: str = Form(...), jurisdiction: str = Form("India"), version: str = Form(""),
    effective_date: str = Form(""), doc_type: str = Form("organisational_policy"),
    authenticity: str = Form("illustrative"), applicability: str = Form(""),
    db: Session = Depends(get_db), user: User = Depends(require("kb:upload")),
):
    if doc_type not in DOC_TYPES or authenticity not in AUTHENTICITY:
        raise HTTPException(400, "Invalid document type or authenticity")
    filename, text, pages = await read_upload_with_pages(file)
    meta, sections = (rag.parse_kb_markdown(text) if filename.lower().endswith(".md") else ({}, []))
    if not sections:  # PDFs are chunked page by page so citations can show the page number
        sections = rag.chunk_pdf_pages(pages) if pages else rag.chunk_plain_text(text)
    meta.update({k: v for k, v in {"title": name.strip()[:300] or filename, "source_organisation": source_organisation[:200],
                                   "jurisdiction": jurisdiction[:60], "version": version[:40],
                                   "effective_date": effective_date[:40], "doc_type": doc_type,
                                   "authenticity": authenticity, "applicability": applicability[:2000]}.items() if v})
    # Retrieval allowlisting: documents from users without kb:manage need approval first.
    allowlisted = has_permission(user, "kb:manage")
    doc = rag.add_document(db, meta, sections, user.email, allowlisted, filename)
    audit(db, "knowledge.uploaded", user, "document", doc.id,
          {"name": doc.name, "chunks": len(sections), "allowlisted": allowlisted}, request)
    return doc_dict(doc)


@router.patch("/documents/{document_id}")
def update_document(document_id: int, body: DocumentUpdate, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("kb:manage"))):
    doc = db.get(KnowledgeDocument, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.allowlisted = body.allowlisted
    db.commit()
    audit(db, "knowledge.allowlist_changed", user, "document", doc.id, {"allowlisted": body.allowlisted}, request)
    return doc_dict(doc)


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("kb:manage"))):
    doc = db.get(KnowledgeDocument, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    audit(db, "knowledge.deleted", user, "document", document_id, {"name": doc.name}, request)
    return {"ok": True}


@router.get("/search")
def search(q: str, jurisdiction: str = "India", k: int = 5, agent: str = "", db: Session = Depends(get_db),
           _: User = Depends(get_current_user)):
    """Search the knowledge base. With `agent` (e.g. security, quality) only the document
    types that agent may retrieve are searched - the same filter the agent uses."""
    if not q.strip():
        raise HTTPException(400, "Query is required")
    if agent and agent not in rag.AGENT_PROFILES:
        raise HTTPException(400, f"Unknown agent. Use one of: {', '.join(rag.AGENT_PROFILES)}")
    doc_types = rag.AGENT_PROFILES[agent]["doc_types"] if agent else None
    results, method = rag.retrieve_many(db, [q[:1000]], jurisdiction, k=min(k, 10), doc_types=doc_types)
    return {"method": method, "results": results[0], "agent": agent or None,
            "doc_types": list(doc_types) if doc_types else None}


@router.post("/reindex")
def reindex(request: Request, db: Session = Depends(get_db), user: User = Depends(require("kb:manage"))):
    if not llm.enabled():
        raise HTTPException(400, "Embeddings need an OpenAI API key (demonstration mode uses lexical retrieval)")
    count = rag.reindex(db)
    audit(db, "knowledge.reindexed", user, details={"chunks": count}, request=request)
    total = db.scalar(select(DocumentChunk.id).limit(1))
    return {"embedded": count, "has_chunks": total is not None}
