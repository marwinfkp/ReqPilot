"""Dashboard, reports/exports, audit logs, settings, users, SDLC configuration,
evaluation runs and health check."""
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import config, evaluation, llm
from app.agents import sdlc_selection as sdlc
from app.api.projects import load_analysis
from app.database import IS_POSTGRES, get_db
from app.models import (Analysis, AuditLog, ComplianceMapping, EvaluationRun, KnowledgeDocument, Project, Requirement,
                        Risk, SdlcRecommendation, User, Workflow)
from app.pipeline import get_setting, set_setting
from app.schemas import SettingsUpdate, UserCreate, UserUpdate
from app.security import audit, get_current_user, hash_password, require
from app.serializers import analysis_brief, analysis_payload, row_dict
from app.services import exports

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql" if IS_POSTGRES else "sqlite",
            "vector_store": "pgvector" if IS_POSTGRES else "json (python similarity)",
            "mode": "openai" if llm.enabled() else "demo",
            "model": config.OPENAI_MODEL if llm.enabled() else None,
            "knowledge_documents": db.scalar(select(func.count()).select_from(KnowledgeDocument))}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    count = lambda model, *where: db.scalar(select(func.count()).select_from(model).where(*where))  # noqa: E731
    done = Analysis.status.in_(["completed", "completed_with_warnings"])
    sdlc_counts = dict(db.execute(select(SdlcRecommendation.recommended_model, func.count())
                                  .group_by(SdlcRecommendation.recommended_model)).all())
    severity = dict(db.execute(select(Risk.severity, func.count()).group_by(Risk.severity)).all())
    domains = dict(db.execute(select(Project.domain, func.count()).group_by(Project.domain)).all())
    recent = db.scalars(select(Analysis).order_by(Analysis.id.desc()).limit(8)).all()
    return {
        "projects": count(Project), "analyses": count(Analysis), "completed_analyses": count(Analysis, done),
        "running_analyses": count(Analysis, Analysis.status.in_(["queued", "running"])),
        "requirements_analysed": count(Requirement),
        "pending": {
            "requirements": count(Requirement, Requirement.status == "pending_review"),
            "mappings": count(ComplianceMapping, ComplianceMapping.status == "pending"),
            "sdlc": count(SdlcRecommendation, SdlcRecommendation.status == "pending_approval"),
            "workflows": count(Workflow, Workflow.status != "approved"),
        },
        "sdlc_distribution": [{"model": k, "count": v} for k, v in sdlc_counts.items()],
        "risk_severity": [{"severity": s, "count": severity.get(s, 0)} for s in ("Critical", "High", "Medium", "Low")],
        "domains": [{"domain": k, "count": v} for k, v in domains.items()],
        "recent_analyses": [analysis_brief(a) for a in recent],
        "mode": "openai" if llm.enabled() else "demo",
    }


# --------------------------------------------------------------------------
# Reports and exports
# --------------------------------------------------------------------------
@router.get("/reports/types")
def report_types(_: User = Depends(get_current_user)):
    return [{"key": k, "title": v} for k, v in exports.REPORTS.items()]


@router.get("/analyses/{analysis_id}/export/{kind}")
def export_report(analysis_id: str, kind: str, request: Request, format: str = "pdf",
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if kind not in exports.REPORTS or format not in ("pdf", "docx"):
        raise HTTPException(400, "Unknown report type or format")
    analysis = load_analysis(db, analysis_id)
    if analysis.status not in ("completed", "completed_with_warnings"):
        raise HTTPException(400, "The analysis has not completed")
    content, media_type, filename = exports.export(kind, analysis_payload(db, analysis), format)
    audit(db, "report.exported", user, "analysis", analysis.id, {"report": kind, "format": format}, request)
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------
@router.get("/audit-logs")
def audit_logs(action: str = "", user_email: str = "", limit: int = 200, offset: int = 0,
               db: Session = Depends(get_db), _: User = Depends(require("audit:view"))):
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if action:
        query = query.where(AuditLog.action.contains(action))
    if user_email:
        query = query.where(AuditLog.user_email.contains(user_email))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.offset(max(0, offset)).limit(min(limit, 500))).all()
    return {"total": total, "items": [row_dict(r) for r in rows]}


# --------------------------------------------------------------------------
# Settings (SDLC weights, retention) and SDLC configuration
# --------------------------------------------------------------------------
def current_settings(db: Session) -> dict:
    return {"mode": "openai" if llm.enabled() else "demo", "openai_api_key_configured": llm.enabled(),
            "openai_model": config.OPENAI_MODEL, "embedding_model": config.OPENAI_EMBEDDING_MODEL,
            "retention_days": get_setting(db, "retention_days", config.DEFAULT_RETENTION_DAYS),
            "sdlc_weights": {**sdlc.DEFAULT_WEIGHTS, **get_setting(db, "sdlc_factor_weights", {})},
            "max_upload_mb": config.MAX_UPLOAD_MB, "session_hours": config.SESSION_HOURS,
            "database": "postgresql" if IS_POSTGRES else "sqlite"}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return current_settings(db)


@router.put("/settings")
def update_settings(body: SettingsUpdate, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("settings:manage"))):
    # Validate everything first, so a rejected request changes nothing.
    if body.sdlc_weights is not None:
        unknown = set(body.sdlc_weights) - set(sdlc.FACTOR_IDS)
        if unknown or any(not 0 <= w <= 5 for w in body.sdlc_weights.values()):
            raise HTTPException(400, "Weights must be known factor IDs with values between 0 and 5")
    if body.retention_days is not None:
        set_setting(db, "retention_days", body.retention_days)
    if body.sdlc_weights is not None:
        set_setting(db, "sdlc_factor_weights", body.sdlc_weights)
    audit(db, "settings.updated", user, details=body.model_dump(exclude_none=True), request=request)
    return current_settings(db)


@router.post("/settings/retention/apply")
def apply_retention(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings:manage"))):
    days = get_setting(db, "retention_days", config.DEFAULT_RETENTION_DAYS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old = db.scalars(select(Analysis).where(Analysis.started_at < cutoff)).all()
    for analysis in old:
        db.delete(analysis)
    db.commit()
    audit(db, "retention.applied", user, details={"retention_days": days, "analyses_deleted": len(old)},
          request=request)
    return {"retention_days": days, "analyses_deleted": len(old)}


@router.get("/sdlc/config")
def sdlc_config(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    weights = {**sdlc.DEFAULT_WEIGHTS, **get_setting(db, "sdlc_factor_weights", {})}
    return {
        "models": sdlc.MODELS, "suitable_threshold": sdlc.SUITABLE_THRESHOLD,
        "factors": [{"id": f, "name": n, "default_weight": w, "weight": weights[f], "low": lo, "high": hi}
                    for f, n, w, lo, hi in sdlc.FACTORS],
        "affinity": sdlc.AFFINITY,
        "rules": [{"id": r, "description": d, "penalties": p} for r, d, _c, p in sdlc.DECISION_RULES],
        "profiles": sdlc.MODEL_PROFILES,
        "formula": "score = 100 x sum(weight x |affinity| x fit) / sum(weight x |affinity|), "
                   "fit = x if affinity > 0 else 1 - x, x = (rating - 1) / 4; then rule penalties.",
    }


# --------------------------------------------------------------------------
# Users (administrator only)
# --------------------------------------------------------------------------
def _user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
            "created_at": u.created_at.isoformat()}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require("users:manage"))):
    return [_user(u) for u in db.scalars(select(User).order_by(User.id)).all()]


@router.post("/users")
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require("users:manage"))):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(400, "A user with this e-mail already exists")
    user = User(email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    audit(db, "user.created", admin, "user", user.id, {"email": user.email, "role": user.role}, request)
    return _user(user)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require("users:manage"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and (body.is_active is False or (body.role and body.role != "admin")):
        raise HTTPException(400, "You cannot deactivate or demote your own account")
    changes = body.model_dump(exclude_none=True)
    if body.role:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)
        changes["password"] = "reset"
    db.commit()
    audit(db, "user.updated", admin, "user", user.id, changes, request)
    return _user(user)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
@router.post("/evaluation/run")
def start_evaluation(request: Request, db: Session = Depends(get_db), user: User = Depends(require("evaluation:run"))):
    if db.scalar(select(EvaluationRun).where(EvaluationRun.status == "running")):
        raise HTTPException(409, "An evaluation is already running")
    run = EvaluationRun(mode="openai" if llm.enabled() else "demo", run_by=user.email,
                        model_name=config.OPENAI_MODEL if llm.enabled() else "rule-based agents")
    db.add(run)
    db.commit()
    audit(db, "evaluation.started", user, "evaluation", run.id, request=request)
    threading.Thread(target=evaluation.run_and_store, args=(run.id,), daemon=True).start()
    return row_dict(run)


@router.get("/evaluation/runs")
def evaluation_runs(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    runs = db.scalars(select(EvaluationRun).order_by(EvaluationRun.id.desc()).limit(20)).all()
    return [row_dict(r) for r in runs]
