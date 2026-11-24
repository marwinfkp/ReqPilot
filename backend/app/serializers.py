"""Convert ORM rows into plain dicts for the API and the document exports."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (AgentRun, Analysis, Approval, ComplianceMapping, Finding, Project, Requirement, Risk,
                        SdlcRecommendation, TraceRecord, Workflow)
from app import clarification
from app.rules import domain_label


def row_dict(row, exclude: tuple = ()) -> dict:
    data = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in exclude}
    for key, value in data.items():
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def project_dict(project: Project) -> dict:
    data = row_dict(project)
    data["domain_label"] = domain_label(project.domain)
    return data


def analysis_brief(analysis: Analysis) -> dict:
    """Small version used for lists and progress polling."""
    return {"id": analysis.id, "project_id": analysis.project_id, "project_name": analysis.project.name,
            "domain": analysis.project.domain, "status": analysis.status, "mode": analysis.mode,
            "model_name": analysis.model_name, "progress": analysis.progress,
            "current_stage": analysis.current_stage, "error": analysis.error,
            "recommended_sdlc": (analysis.metrics or {}).get("recommended_sdlc", ""),
            "started_at": analysis.started_at.isoformat() if analysis.started_at else None,
            "finished_at": analysis.finished_at.isoformat() if analysis.finished_at else None,
            "created_by": analysis.created_by,
            # Two-stage analysis and versions
            "stage1_status": analysis.stage1_status, "stage2_status": analysis.stage2_status,
            "version": analysis.version, "parent_analysis_id": analysis.parent_analysis_id,
            "root_analysis_id": analysis.root_analysis_id or analysis.id}


def _rows(db: Session, model, analysis_id: int, order=None) -> list[dict]:
    query = select(model).where(model.analysis_id == analysis_id).order_by(
        *([order] if order is not None else []), model.id)
    return [row_dict(r, exclude=("analysis_id",)) for r in db.scalars(query).all()]


def analysis_payload(db: Session, analysis: Analysis) -> dict:
    """Everything the results dashboard and the reports need, in one dict."""
    sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == analysis.id))
    workflow = db.scalar(select(Workflow).where(Workflow.analysis_id == analysis.id))
    data = analysis_brief(analysis)
    data.update({
        "project": project_dict(analysis.project),
        "input_text": analysis.input_text,
        "input_sources": [{"name": s["name"], "chars": len(s["text"])} for s in analysis.input_sources or []],
        "submitted_count": analysis.submitted_count, "summary": analysis.summary,
        "key_findings": analysis.key_findings, "metrics": analysis.metrics,
        "security_coverage": analysis.security_coverage, "applicable_sources": analysis.applicable_sources,
        "use_cases": analysis.use_cases, "validation_report": analysis.validation_report,
        "baseline_status": analysis.baseline_status,
        "agent_runs": _rows(db, AgentRun, analysis.id, AgentRun.order),
        "requirements": _rows(db, Requirement, analysis.id),
        "findings": _rows(db, Finding, analysis.id),
        "mappings": _rows(db, ComplianceMapping, analysis.id),
        "risks": _rows(db, Risk, analysis.id, Risk.score.desc()),
        "traceability": _rows(db, TraceRecord, analysis.id),
        "approvals": _rows(db, Approval, analysis.id, Approval.timestamp.desc()),
        "sdlc": row_dict(sdlc, exclude=("analysis_id",)) if sdlc else None,
        "workflow": row_dict(workflow, exclude=("analysis_id",)) if workflow else None,
        # Two-stage analysis: clarification indicators and the version history of this analysis
        "clarification": clarification.clarification_summary(db, analysis),
        "versions": version_list(db, analysis),
    })
    return data


def version_list(db: Session, analysis: Analysis) -> list[dict]:
    root = analysis.root_analysis_id or analysis.id
    rows = db.scalars(select(Analysis).where((Analysis.id == root) | (Analysis.root_analysis_id == root))
                      .order_by(Analysis.version)).all()
    return [{"id": a.id, "version": a.version, "status": a.status, "stage2_status": a.stage2_status,
             "baseline_status": a.baseline_status, "parent_analysis_id": a.parent_analysis_id,
             "recommended_sdlc": (a.metrics or {}).get("recommended_sdlc", ""),
             "created_by": a.created_by, "started_at": a.started_at.isoformat() if a.started_at else None,
             "finished_at": a.finished_at.isoformat() if a.finished_at else None} for a in rows]
