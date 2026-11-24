"""Two-stage analysis API.

Stage 1 (mandatory)  : clarification questions -> answers / deferrals -> continue -> full 12-agent pipeline
Stage 2 (optional)   : Refinement Centre - further questions, stakeholder interviews, requirement change
                       requests (human approval), dependency-aware selective regeneration, versions.
Every human decision is stored in the existing `approvals` table and the audit log.
"""
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import clarification, pipeline, regeneration
from app.api.review import record
from app.database import get_db
from app.models import (Analysis, ClarificationQuestion, ClarificationResponse, Finding, RegenerationRun, Requirement,
                        RequirementChange, Risk, StakeholderInteraction, User)
from app.security import audit, get_current_user, has_permission, rate_limit, require
from app.serializers import row_dict

router = APIRouter(prefix="/api", tags=["two-stage analysis"])
DONE = ("completed", "completed_with_warnings")


def _analysis(db: Session, analysis_id: int) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    return analysis


def _completed(db: Session, analysis_id: int) -> Analysis:
    analysis = _analysis(db, analysis_id)
    if analysis.status not in DONE:
        raise HTTPException(400, "Stage 2 refinement is available once Stage 1 has completed")
    return analysis


# ==========================================================================
# Stage 1 - clarification
# ==========================================================================
@router.get("/analyses/{analysis_id}/clarifications")
def get_clarifications(analysis_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    analysis = _analysis(db, analysis_id)
    questions = [clarification.question_dict(q) for q in clarification.family_questions(db, analysis)]
    stage1 = [q for q in questions if q["stage"] == 1]
    return {"analysis_id": analysis.id, "status": analysis.status, "stage1_status": analysis.stage1_status,
            "project_name": analysis.project.name, "questions": questions,
            "counts": clarification.counts(stage1), "preliminary": analysis.preliminary or {},
            "editable_stage1": analysis.status == "awaiting_clarification"}


class AnswerBody(BaseModel):
    status: Literal["answered", "deferred", "needs_stakeholder", "open"]
    answer: str = Field(default="", max_length=4000)


@router.put("/clarifications/{question_id}")
def answer_question(question_id: int, body: AnswerBody, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("clarification:answer"))):
    """Answer, edit, defer, flag for stakeholder input, or reopen a question.
    One current response per question (edits update it; the audit log keeps the history)."""
    question = db.get(ClarificationQuestion, question_id)
    if not question:
        raise HTTPException(404, "Question not found")
    analysis = _analysis(db, question.analysis_id)
    latest = regeneration.latest_version(db, analysis)
    if analysis.status == "awaiting_clarification":
        stage = 1
    elif latest.status in DONE and question.applied_in_version is None:
        stage = 2  # answered after Stage 1: applied by the next regeneration
    elif question.applied_in_version is not None:
        raise HTTPException(409, f"This answer was already applied in version {question.applied_in_version}; "
                                 "ask a follow-up question instead.")
    else:
        raise HTTPException(409, "Answers cannot be changed while the analysis is running")
    try:
        answer = clarification.validate_answer(question, body.status, body.answer)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = {"status": question.status, "answer": question.response.answer if question.response else ""}
    question.status = body.status
    if question.response is None:
        question.response = ClarificationResponse(answer="", responded_by=user.email, responder_role=user.role)
    response = question.response
    response.answer, response.stage = answer, stage
    response.status = {"answered": "accepted", "deferred": "deferred"}.get(body.status, "pending")
    response.affects_requirements = body.status == "answered" and bool(question.related_requirements or
                                                                       question.requirement_ids)
    response.responded_by, response.responder_role = user.email, user.role
    response.responded_at = datetime.now(timezone.utc)
    db.commit()
    audit(db, f"clarification.{body.status}", user, "clarification", question.code,
          {"analysis_id": question.analysis_id, "stage": stage, "previous": previous, "answer": answer[:500]}, request)
    return clarification.question_dict(question)


@router.post("/analyses/{analysis_id}/continue", dependencies=[Depends(rate_limit("analysis", 10, 60))])
def continue_to_full_analysis(analysis_id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("analysis:run"))):
    """Save the clarification state and start the complete 12-agent pipeline."""
    started = time.perf_counter()
    # Atomic state change: a double click or a second tab cannot start the pipeline twice.
    result = db.execute(update(Analysis).where(Analysis.id == analysis_id, Analysis.status == "awaiting_clarification")
                        .values(status="queued", stage1_status="analysing", current_stage="Queued", progress=0))
    db.commit()
    if result.rowcount != 1:
        analysis = _analysis(db, analysis_id)
        raise HTTPException(409, f"The analysis is not waiting for clarification (status: {analysis.status})")
    analysis = _analysis(db, analysis_id)
    c = clarification.counts([clarification.question_dict(q) for q in clarification.load_questions(db, analysis_id, 1)])
    clarification.log_run(db, analysis_id, "Clarification Responses", "completed", "human", started,
                          f"{c['answered']} answered, {c['deferred']} deferred, {c['needs_stakeholder']} need "
                          f"stakeholder input, {c['open']} unanswered (of {c['total']}). Unanswered questions stay "
                          f"unresolved - nothing is assumed. Submitted by {user.email}.")
    db.commit()
    audit(db, "clarification.completed", user, "analysis", analysis_id, c, request)
    pipeline.start_in_background(analysis_id)
    return {"analysis_id": analysis_id, "status": "queued", "counts": c}


@router.post("/analyses/{analysis_id}/retry")
def retry_analysis(analysis_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("analysis:run"))):
    """Re-run a failed Stage 1 pipeline with the saved clarification answers."""
    analysis = _analysis(db, analysis_id)
    if analysis.status != "failed" or analysis.version > 1:
        raise HTTPException(400, "Only a failed Stage 1 analysis can be retried")
    pipeline.clear_results(db, analysis_id)
    analysis.status, analysis.error, analysis.progress = "queued", "", 0
    analysis.stage1_status = "preliminary" if not clarification.load_questions(db, analysis_id) else "analysing"
    db.commit()
    audit(db, "analysis.retried", user, "analysis", analysis_id, request=request)
    pipeline.start_in_background(analysis_id)
    return {"analysis_id": analysis_id, "status": "queued"}


# ==========================================================================
# Stage 2 - Refinement Centre
# ==========================================================================
@router.post("/analyses/{analysis_id}/refinement/open")
def open_refinement(analysis_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Explicit user action that starts Stage 2 (nothing is executed automatically)."""
    analysis = _completed(db, analysis_id)
    if analysis.stage2_status in ("not_started", "regenerated", "failed"):
        previous = analysis.stage2_status
        analysis.stage2_status = "in_progress"
        clarification.log_run(db, analysis.id, "Refinement Centre Opened", "completed", "human", time.perf_counter(),
                              f"Stage 2 started by {user.email} (previous stage 2 status: {previous}).",
                              stage="stage2")
        db.commit()
        audit(db, "refinement.opened", user, "analysis", analysis.id, request=request)
    return {"stage2_status": analysis.stage2_status}


@router.get("/analyses/{analysis_id}/refinement")
def refinement_overview(analysis_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    analysis = _completed(db, analysis_id)
    latest = regeneration.latest_version(db, analysis)
    findings = db.scalars(select(Finding).where(Finding.analysis_id == analysis.id)).all()
    open_findings = [row_dict(f) for f in findings if f.resolution != "resolved"
                     and f.kind in ("quality", "compliance_gap", "security_gap", "threat")]
    questions = [clarification.question_dict(q) for q in clarification.family_questions(db, analysis)]
    ids = clarification.family_ids(db, analysis)
    changes = db.scalars(select(RequirementChange).where(RequirementChange.analysis_id.in_(ids))
                         .order_by(RequirementChange.id.desc())).all()
    interviews = db.scalars(select(StakeholderInteraction).where(StakeholderInteraction.analysis_id.in_(ids))
                            .order_by(StakeholderInteraction.id.desc())).all()
    runs = db.scalars(select(RegenerationRun).where(RegenerationRun.source_analysis_id.in_(ids))
                      .order_by(RegenerationRun.id.desc())).all()
    risks = db.scalars(select(Risk).where(Risk.analysis_id == analysis.id, Risk.severity.in_(["High", "Critical"]))
                       .order_by(Risk.score.desc())).all()
    groups = {
        "conflicts": [f for f in open_findings if f["issue_type"] in ("conflict", "stakeholder_conflict")],
        "ambiguities": [f for f in open_findings if f["issue_type"] in ("ambiguity", "untestable")],
        "incomplete": [f for f in open_findings if f["issue_type"] in ("incompleteness", "missing_acceptance_criteria",
                                                                       "undefined_term", "infeasibility")],
        "compliance_gaps": [f for f in open_findings if f["kind"] == "compliance_gap"],
        "security": [f for f in open_findings if f["kind"] in ("security_gap", "threat")
                     and f["severity"] in ("High", "Critical")],
    }
    pending = regeneration.pending_inputs(db, latest)
    return {
        "analysis": {"id": analysis.id, "version": analysis.version, "status": analysis.status,
                     "stage1_status": analysis.stage1_status, "stage2_status": analysis.stage2_status,
                     "is_latest": latest.id == analysis.id, "latest_id": latest.id, "latest_version": latest.version,
                     "project_name": analysis.project.name},
        "outstanding": groups, "high_risks": [row_dict(r) for r in risks],
        "questions": questions,
        "question_counts": clarification.counts(questions),
        "changes": [row_dict(c) for c in changes], "interviews": [row_dict(i) for i in interviews],
        "regenerations": [row_dict(r) for r in runs],
        "pending_inputs": {"changes": len(pending["changes"]), "questions": len(pending["questions"]),
                           "interviews": len(pending["interviews"])},
        "suggested_areas": [{"area": name.replace("_", " ").title(), "count": len(items)}
                            for name, items in groups.items() if items],
        "log": regeneration.run_logs(db, analysis.id, "stage2"),
    }


@router.post("/analyses/{analysis_id}/refinement/questions")
def generate_more_questions(analysis_id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("clarification:answer"))):
    analysis = _completed(db, analysis_id)
    rows, message = clarification.generate_followups(db, analysis, user.email)
    db.commit()
    audit(db, "refinement.questions_generated", user, "analysis", analysis.id, {"count": len(rows)}, request)
    return {"message": message, "questions": [clarification.question_dict(q) for q in rows]}


# ---------------------------------------------------------------- stakeholders
@router.get("/analyses/{analysis_id}/stakeholders/suggestions")
def stakeholder_suggestions(analysis_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return clarification.stakeholder_suggestions(db, _completed(db, analysis_id))


class InterviewEntry(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    answer: str = Field(default="", max_length=4000)


class InterviewBody(BaseModel):
    stakeholder_role: str = Field(min_length=2, max_length=60)
    interviewee: str = Field(default="", max_length=200)
    topic: str = Field(default="", max_length=300)
    requirement_ids: list[str] = Field(default_factory=list, max_length=50)
    entries: list[InterviewEntry] = Field(min_length=1, max_length=30)


@router.post("/analyses/{analysis_id}/stakeholder-interactions")
def record_interview(analysis_id: int, body: InterviewBody, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("stakeholder:record"))):
    """Record a stakeholder interview conducted by the user (ReqPilot does not contact anyone)."""
    started = time.perf_counter()
    analysis = _completed(db, analysis_id)
    known = set(db.scalars(select(Requirement.req_id).where(Requirement.analysis_id == analysis.id)).all())
    unknown = [i for i in body.requirement_ids if i not in known]
    if unknown:
        raise HTTPException(422, f"Unknown requirement IDs: {', '.join(unknown)}")
    if not any(e.answer.strip() for e in body.entries):
        raise HTTPException(422, "Record at least one answer")
    interview = StakeholderInteraction(analysis_id=analysis.id, stakeholder_role=body.stakeholder_role,
                                       interviewee=body.interviewee, topic=body.topic,
                                       requirement_ids=body.requirement_ids, entries=[e.model_dump() for e in body.entries],
                                       recorded_by=user.email, recorder_role=user.role)
    db.add(interview)
    db.flush()
    clarification.log_run(db, analysis.id, "Stakeholder Interview Recorded", "completed", "human", started,
                          f"SI-{interview.id:02d}: {body.stakeholder_role} ({body.interviewee or 'name not given'}) - "
                          f"{len(body.entries)} question(s) on {', '.join(body.requirement_ids) or 'project level'}; "
                          f"recorded by {user.email}; awaiting review.", stage="stage2")
    db.commit()
    audit(db, "stakeholder.recorded", user, "stakeholder_interaction", interview.id,
          {"analysis_id": analysis.id, "role": body.stakeholder_role}, request)
    return row_dict(interview)


class ReviewBody(BaseModel):
    action: Literal["accept", "reject", "request_changes"]
    comment: str = Field(default="", max_length=2000)


@router.post("/stakeholder-interactions/{interaction_id}/review")
def review_interview(interaction_id: int, body: ReviewBody, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("stakeholder:review"))):
    interview = db.get(StakeholderInteraction, interaction_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    if interview.applied_in_version is not None:
        raise HTTPException(409, "Already applied in a later version")
    if body.action != "accept" and not body.comment.strip():
        raise HTTPException(400, "A comment is required")
    previous = {"status": interview.status}
    interview.status = {"accept": "accepted", "reject": "rejected", "request_changes": "changes_requested"}[body.action]
    interview.reviewed_by, interview.review_comment = user.email, body.comment
    interview.updated_at = datetime.now(timezone.utc)
    db.commit()
    record(db, request, user, interview.analysis_id, "stakeholder_interaction", f"SI-{interview.id:02d}",
           body.action, body.comment, previous, {"status": interview.status})
    return row_dict(interview)


# ---------------------------------------------------------------- requirement changes
ChangeType = Literal["edit", "acceptance_criteria", "new", "resolve_conflict", "business_rule", "constraint", "obsolete"]


class ChangeBody(BaseModel):
    change_type: ChangeType
    requirement_id: str = Field(default="", max_length=30)
    proposed_text: str = Field(default="", max_length=3000)
    new_kind: Literal["", "functional", "non_functional", "business_rule", "constraint"] = ""
    reason: str = Field(min_length=3, max_length=2000)
    source: str = Field(default="user", max_length=200)
    submit: bool = True  # False keeps it as a draft


@router.post("/analyses/{analysis_id}/requirement-changes")
def propose_change(analysis_id: int, body: ChangeBody, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("change:propose"))):
    started = time.perf_counter()
    analysis = _completed(db, analysis_id)
    original = ""
    if body.change_type != "new":
        req = db.scalar(select(Requirement).where(Requirement.analysis_id == analysis.id,
                                                  Requirement.req_id == body.requirement_id))
        if not req:
            raise HTTPException(422, f"Requirement {body.requirement_id or '(none)'} does not exist in this version")
        if req.status == "obsolete":
            raise HTTPException(422, f"{req.req_id} is already obsolete")
        original = req.statement
    if body.change_type != "obsolete" and not body.proposed_text.strip():
        raise HTTPException(422, "Enter the proposed text")
    ids = clarification.family_ids(db, analysis)
    number = 1 + (db.scalar(select(func.count()).select_from(RequirementChange)
                            .where(RequirementChange.analysis_id.in_(ids))) or 0)
    change = RequirementChange(analysis_id=analysis.id, code=f"RC-{number:02d}", requirement_id=body.requirement_id
                               if body.change_type != "new" else "", change_type=body.change_type,
                               new_kind=body.new_kind, original_text=original, proposed_text=body.proposed_text.strip(),
                               reason=body.reason, source=body.source, proposed_by=user.email,
                               proposer_role=user.role, status="pending_review" if body.submit else "draft")
    db.add(change)
    db.flush()
    clarification.log_run(db, analysis.id, "Requirement Modification Proposed", "completed", "human", started,
                          f"{change.code} ({body.change_type.replace('_', ' ')}) on "
                          f"{change.requirement_id or 'a new requirement'} by {user.email}; status {change.status}. "
                          "The original requirement is unchanged.", stage="stage2")
    db.commit()
    audit(db, "change.proposed", user, "requirement_change", change.code,
          {"analysis_id": analysis.id, "type": body.change_type, "requirement": change.requirement_id}, request)
    return row_dict(change)


class ChangeUpdate(BaseModel):
    proposed_text: str = Field(default="", max_length=3000)
    reason: str = Field(default="", max_length=2000)


@router.post("/requirement-changes/{change_id}/submit")
def submit_change(change_id: int, body: ChangeUpdate, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("change:propose"))):
    """The proposer (re)submits a draft or a change that needed changes."""
    change = db.get(RequirementChange, change_id)
    if not change:
        raise HTTPException(404, "Change not found")
    if change.proposed_by != user.email:
        raise HTTPException(403, "Only the proposer can edit and resubmit this change")
    if change.status not in ("draft", "changes_requested"):
        raise HTTPException(409, f"A change with status '{change.status}' cannot be resubmitted")
    if body.proposed_text.strip():
        change.proposed_text = body.proposed_text.strip()
    if body.reason.strip():
        change.reason = body.reason.strip()
    change.status = "pending_review"
    db.commit()
    audit(db, "change.submitted", user, "requirement_change", change.code, request=request)
    return row_dict(change)


class ChangeReview(BaseModel):
    action: Literal["approve", "reject", "request_changes"]
    comment: str = Field(default="", max_length=2000)
    approved_text: str = Field(default="", max_length=3000)


@router.post("/requirement-changes/{change_id}/review")
def review_change(change_id: int, body: ChangeReview, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("change:approve"))):
    change = db.get(RequirementChange, change_id)
    if not change:
        raise HTTPException(404, "Change not found")
    if change.status != "pending_review":
        raise HTTPException(409, f"Only changes pending review can be decided (status: {change.status})")
    if change.proposed_by == user.email:
        raise HTTPException(403, "Four-eyes principle: you cannot review a change you proposed")
    if body.action != "approve" and not body.comment.strip():
        raise HTTPException(400, "A comment is required to reject or request changes")
    previous = {"status": change.status, "proposed_text": change.proposed_text}
    change.status = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}[body.action]
    if body.action == "approve":
        change.approved_text = body.approved_text.strip() or change.proposed_text
    change.reviewed_by, change.review_comment = user.email, body.comment
    change.reviewed_at = datetime.now(timezone.utc)
    clarification.log_run(db, change.analysis_id, "Requirement Change Review", "completed", "human", time.perf_counter(),
                          f"{change.code} {change.status} by {user.email} ({user.role}).", stage="stage2")
    db.commit()
    record(db, request, user, change.analysis_id, "requirement_change", change.code, body.action, body.comment,
           previous, {"status": change.status, "approved_text": change.approved_text})
    return row_dict(change)


# ---------------------------------------------------------------- regeneration and versions
@router.get("/analyses/{analysis_id}/regeneration/plan")
def regeneration_plan(analysis_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return regeneration.plan(db, _completed(db, analysis_id))


class RegenerateBody(BaseModel):
    confirm: bool = False
    comment: str = Field(default="", max_length=2000)


@router.post("/analyses/{analysis_id}/regeneration")
def run_regeneration(analysis_id: int, body: RegenerateBody, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("regeneration:run"))):
    if not body.confirm:
        raise HTTPException(400, "Review the regeneration plan and confirm it first")
    analysis = _completed(db, analysis_id)
    try:
        run = regeneration.start(db, analysis, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, "regeneration.started", user, "analysis", analysis.id,
          {"new_analysis_id": run.new_analysis_id, "new_version": run.new_version, "comment": body.comment}, request)
    return {"run_id": run.id, "new_analysis_id": run.new_analysis_id, "new_version": run.new_version}


@router.get("/analyses/{analysis_id}/versions")
def versions(analysis_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from app.serializers import version_list
    return version_list(db, _analysis(db, analysis_id))


@router.get("/analysis-versions/compare")
def compare_versions(base: int, target: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    a, b = _analysis(db, base), _analysis(db, target)
    if (a.root_analysis_id or a.id) != (b.root_analysis_id or b.id):
        raise HTTPException(400, "Both analyses must be versions of the same analysis")
    if a.status not in DONE or b.status not in DONE:
        raise HTTPException(400, "Both versions must be completed")
    return regeneration.compare(db, a, b)


@router.get("/analyses/{analysis_id}/logs")
def stage_logs(analysis_id: int, stage: Literal["stage1", "stage2"] | None = None, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)):
    _analysis(db, analysis_id)
    return regeneration.run_logs(db, analysis_id, stage)


# ---------------------------------------------------------------- risk acceptance
class RiskReview(BaseModel):
    action: Literal["accept", "reject"]
    comment: str = Field(min_length=3, max_length=2000)


@router.post("/risks/{risk_pk}/review")
def review_risk(risk_pk: int, body: RiskReview, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("risk:accept"))):
    """Human acceptance of a high or critical risk (never automatic)."""
    risk = db.get(Risk, risk_pk)
    if not risk:
        raise HTTPException(404, "Risk not found")
    if risk.severity not in ("High", "Critical"):
        raise HTTPException(400, "Formal acceptance is required only for high or critical risks")
    if risk.category == "security" and not (has_permission(user, "*") or user.role == "security_officer"):
        raise HTTPException(403, "Security risks must be accepted by a Security Officer or Administrator")
    previous = {"status": risk.status}
    risk.status = "accepted" if body.action == "accept" else "rejected"
    db.commit()
    record(db, request, user, risk.analysis_id, "risk", risk.risk_id, body.action, body.comment, previous,
           {"status": risk.status, "severity": risk.severity, "score": risk.score})
    return row_dict(risk)
