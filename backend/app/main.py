"""ReqPilot FastAPI application.

Start:  uvicorn app.main:app --reload --port 8000   (from the backend folder)
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import config, pipeline
from app.api import admin, auth, knowledge, projects, refinement, review
from app.database import SessionLocal, init_db
from app.models import Analysis, RegenerationRun
from app.security import check_rate_limit
from app.seed import seed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed(db)
        recover_interrupted(db)
    yield


def recover_interrupted(db) -> None:
    """Background work cannot survive a server restart. Nothing is marked as successful:
    * a Stage 1 pipeline that had already been clarified goes back to the Clarification Centre
      (answers are kept) so the user can simply continue again;
    * other interrupted runs (preliminary analysis, legacy runs, regenerations) are marked failed
      and can be retried."""
    now = datetime.now(timezone.utc)
    for analysis in db.scalars(select(Analysis).where(
            Analysis.status.in_(["preliminary", "queued", "running"]))).all():
        if analysis.version == 1 and analysis.stage1_status == "analysing" and (analysis.preliminary or {}).get("interactive"):
            analysis.status = analysis.stage1_status = "awaiting_clarification"
            analysis.error = "The full analysis was interrupted by a server restart. Your clarification answers are " \
                             "saved - click Continue to Full Analysis to run it again."
            analysis.current_stage, analysis.progress = "Awaiting clarification", 0
            pipeline.clear_results(db, analysis.id)
        else:
            analysis.status, analysis.error = "failed", "Interrupted by a server restart; please run it again."
            analysis.stage1_status = "failed" if analysis.version == 1 else analysis.stage1_status
            analysis.finished_at = now
    for run in db.scalars(select(RegenerationRun).where(RegenerationRun.status == "running")).all():
        run.status, run.error, run.finished_at = "failed", "Interrupted by a server restart", now
        source = db.get(Analysis, run.source_analysis_id)
        if source:
            source.stage2_status = "failed"
    db.commit()


app = FastAPI(title="ReqPilot API", version="1.0.0", lifespan=lifespan,
              description="Agentic AI financial requirement analysis and SDLC recommendation")

app.add_middleware(CORSMiddleware, allow_origins=[config.FRONTEND_ORIGIN], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Global per-IP rate limit (generous; stricter limits exist on login and analysis).
    client = request.client.host if request.client else "unknown"
    if request.url.path.startswith("/api") and not check_rate_limit(f"global:{client}", 600, 60):
        return JSONResponse({"detail": "Too many requests"}, status_code=429)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception):
    # Never leak stack traces or secrets to the client.
    return JSONResponse({"detail": f"Internal error ({type(exc).__name__}). See server logs."}, status_code=500)


for router in (auth.router, projects.router, review.router, refinement.router, knowledge.router, admin.router):
    app.include_router(router)
