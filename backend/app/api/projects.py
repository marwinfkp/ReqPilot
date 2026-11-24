"""Projects, example requirement sets and analysis runs (the main workflow).

POST /api/analyses is the single "Analyse Requirements" action: it validates
the input, stores the project and the original submission, and starts the
agent pipeline in the background. The frontend then polls /status.
"""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, llm, pipeline
from app.database import get_db
from app.models import Analysis, Project, User
from app.rules import DOMAINS
from app.schemas import ProjectCreate
from app.security import audit, get_current_user, rate_limit, require
from app.serializers import analysis_brief, analysis_payload, project_dict
from app.services.documents import read_upload

router = APIRouter(prefix="/api", tags=["projects"])


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------
@router.get("/domains")
def domains(_: User = Depends(get_current_user)):
    return [{"key": key, "label": label} for key, (label, _code) in DOMAINS.items()]


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    result = []
    for project in projects:
        data = project_dict(project)
        latest = project.analyses[0] if project.analyses else None
        data["analysis_count"] = len(project.analyses)
        data["latest_analysis"] = analysis_brief(latest) if latest else None
        result.append(data)
    return result


@router.post("/projects")
def create_project(body: ProjectCreate, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("project:write"))):
    if body.domain not in DOMAINS:
        raise HTTPException(400, f"Unknown domain '{body.domain}'")
    project = Project(**body.model_dump(), owner_email=user.email)
    db.add(project)
    db.commit()
    audit(db, "project.created", user, "project", project.id, {"name": project.name}, request)
    return project_dict(project)


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {**project_dict(project), "analyses": [analysis_brief(a) for a in project.analyses]}


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("project:delete"))):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    db.delete(project)
    db.commit()
    audit(db, "project.deleted", user, "project", project_id, {"name": project.name}, request)
    return {"ok": True}


# --------------------------------------------------------------------------
# Example requirement sets (the synthetic benchmark cases)
# --------------------------------------------------------------------------
def _benchmark() -> list[dict]:
    path = config.EVALUATION_DIR / "benchmark.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"] if path.exists() else []


@router.get("/examples")
def examples(_: User = Depends(get_current_user)):
    return [{"key": c["key"], "title": c["title"], "project": c["project"],
             "requirements_text": c["requirements_text"],
             "requirement_lines": c["requirements_text"].count("\n") + 1} for c in _benchmark()]


# --------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------
@router.post("/analyses", dependencies=[Depends(rate_limit("analysis", 10, 60))])
async def start_analysis(
    request: Request,
    project_id: int | None = Form(None),
    name: str = Form(""), domain: str = Form(""), description: str = Form(""),
    jurisdiction: str = Form("India"), scale: str = Form("medium"), budget: str = Form(""),
    schedule: str = Form(""), stakeholder_availability: str = Form(""),
    requirements_text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("analysis:run")),
):
    # 1. Validate the submission (pasted text and/or uploaded documents).
    sources = []
    if requirements_text.strip():
        sources.append({"name": "Pasted requirements", "text": requirements_text})
    if len(files) > 10:
        raise HTTPException(400, "At most 10 files per analysis")
    for upload in files:
        filename, text = await read_upload(upload)
        sources.append({"name": filename, "text": text})
    if not sources:
        raise HTTPException(400, "Provide requirements as text or upload at least one document")
    if sum(len(s["text"]) for s in sources) > config.MAX_REQUIREMENTS_CHARS:
        raise HTTPException(413, f"Submission exceeds {config.MAX_REQUIREMENTS_CHARS:,} characters")

    # 2. Create or reuse the project.
    if project_id:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    else:
        try:
            details = ProjectCreate(name=name, domain=domain, description=description,
                                    jurisdiction=jurisdiction or "India", scale=scale or "medium", budget=budget,
                                    schedule=schedule, stakeholder_availability=stakeholder_availability)
        except ValidationError as exc:  # invalid form values are the client's error (422), not a server error
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise HTTPException(422, f"Invalid project details - {problems}") from exc
        if details.domain not in DOMAINS:
            raise HTTPException(400, f"Unknown domain '{details.domain}'")
        project = Project(**details.model_dump(), owner_email=user.email)
        db.add(project)
        db.flush()

    # 3. Store the original submission and start the agent pipeline.
    analysis = Analysis(project_id=project.id, created_by=user.email, status="queued",
                        mode="openai" if llm.enabled() else "demo",
                        model_name=config.OPENAI_MODEL if llm.enabled() else "rule-based agents (demonstration)",
                        input_text=requirements_text, input_sources=sources, current_stage="Queued")
    db.add(analysis)
    db.commit()
    audit(db, "analysis.started", user, "analysis", analysis.id,
          {"project": project.name, "sources": [s["name"] for s in sources], "mode": analysis.mode}, request)
    pipeline.start_in_background(analysis.id)
    return {"analysis_id": analysis.id, "project_id": project.id, "mode": analysis.mode}


@router.get("/analyses")
def list_analyses(project_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(Analysis).order_by(Analysis.id.desc()).limit(200)
    if project_id:
        query = query.where(Analysis.project_id == project_id)
    return [analysis_brief(a) for a in db.scalars(query).all()]


def load_analysis(db: Session, analysis_id: str) -> Analysis:
    """`analysis_id` may be a number or 'latest' (latest finished analysis)."""
    if analysis_id == "latest":
        analysis = db.scalar(select(Analysis).where(Analysis.status.in_(["completed", "completed_with_warnings"]))
                             .order_by(Analysis.id.desc()).limit(1))
    else:
        analysis = db.get(Analysis, int(analysis_id)) if analysis_id.isdigit() else None
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    return analysis


@router.get("/analyses/{analysis_id}/status")
def analysis_status(analysis_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    analysis = load_analysis(db, analysis_id)
    db.refresh(analysis)
    return analysis_brief(analysis)


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return analysis_payload(db, load_analysis(db, analysis_id))
