"""Human-in-the-loop approvals (always AFTER the automatic analysis).

Every decision is stored in the `approvals` table with reviewer, role, action,
comment, timestamp and the previous/new value, and also written to the audit
log. AI agents never call these endpoints - only authenticated humans can.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import workflow as workflow_agent
from app.agents.sdlc_selection import MODELS
from app.database import get_db
from app.models import (Analysis, Approval, ComplianceMapping, Finding, Requirement, RequirementChange, Risk,
                        SdlcRecommendation, StakeholderInteraction, User, Workflow)
from app.schemas import CommentBody, MappingReview, RequirementReview, SdlcReview, WorkflowReview
from app.security import audit, get_current_user, has_permission, require
from app.serializers import row_dict

router = APIRouter(prefix="/api", tags=["review"])
SECURITY_CATEGORIES = {"security", "privacy"}


def record(db: Session, request: Request, user: User, analysis_id: int, target_type: str, target_id,
           action: str, comment: str, previous: dict, new: dict) -> None:
    db.add(Approval(analysis_id=analysis_id, target_type=target_type, target_id=str(target_id), action=action,
                    reviewer_email=user.email, reviewer_role=user.role, comment=comment,
                    previous_value=previous, new_value=new))
    db.commit()
    audit(db, f"approval.{target_type}.{action}", user, target_type, target_id,
          {"analysis_id": analysis_id, "comment": comment[:300]}, request)


def _analysis(db: Session, analysis_id: int) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    return analysis


# --------------------------------------------------------------------------
# Requirements: accept / reject / modify
# --------------------------------------------------------------------------
def can_review_requirement(user: User, requirement: Requirement) -> bool:
    if has_permission(user, "requirement:review"):
        return True
    # Security and compliance officers review security/privacy requirements.
    return has_permission(user, "requirement:review_security") and \
        bool(SECURITY_CATEGORIES & set(requirement.categories))


@router.post("/requirements/{requirement_pk}/review")
def review_requirement(requirement_pk: int, body: RequirementReview, request: Request,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    req = db.get(Requirement, requirement_pk)
    if not req:
        raise HTTPException(404, "Requirement not found")
    if not can_review_requirement(user, req):
        raise HTTPException(403, f"Role '{user.role}' cannot review requirement {req.req_id}")
    previous = {"statement": req.statement, "status": req.status}
    if body.action == "modify":
        if not body.statement or not body.statement.strip():
            raise HTTPException(400, "A modified statement is required")
        req.statement, req.status = body.statement.strip(), "modified"
    else:
        req.status = "accepted" if body.action == "accept" else "rejected"
    analysis = _analysis(db, req.analysis_id)
    note = ""
    if analysis.baseline_status == "approved":  # change control: an approved baseline must be re-approved
        analysis.baseline_status = "draft"
        note = " Baseline reopened for re-approval (change to an approved requirement)."
    db.commit()
    record(db, request, user, req.analysis_id, "requirement", req.req_id, body.action, body.comment + note,
           previous, {"statement": req.statement, "status": req.status})
    return row_dict(req)


@router.post("/analyses/{analysis_id}/requirements/accept-pending")
def accept_pending(analysis_id: int, body: CommentBody, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("requirement:review"))):
    """A human reviewer accepts all still-pending requirements in one action."""
    _analysis(db, analysis_id)  # 404 for an unknown analysis
    pending = db.scalars(select(Requirement).where(Requirement.analysis_id == analysis_id,
                                                   Requirement.status == "pending_review")).all()
    for req in pending:
        req.status = "accepted"
    db.commit()
    record(db, request, user, analysis_id, "requirement", "bulk", "accept",
           body.comment or "Bulk acceptance of pending requirements", {"pending": [r.req_id for r in pending]},
           {"status": "accepted", "count": len(pending)})
    return {"accepted": len(pending)}


@router.post("/analyses/{analysis_id}/baseline")
def approve_baseline(analysis_id: int, body: CommentBody, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("baseline:approve"))):
    analysis = _analysis(db, analysis_id)
    if analysis.status not in ("completed", "completed_with_warnings"):
        raise HTTPException(400, f"Only a completed analysis can be baselined (status: {analysis.status})")
    pending = db.scalar(select(func.count()).select_from(Requirement).where(
        Requirement.analysis_id == analysis_id, Requirement.status == "pending_review"))
    if pending:
        raise HTTPException(400, f"{pending} requirement(s) are still pending review")
    counts = dict(db.execute(select(Requirement.status, func.count()).where(Requirement.analysis_id == analysis_id)
                             .group_by(Requirement.status)).all())
    analysis.baseline_status = "approved"
    db.commit()
    record(db, request, user, analysis_id, "baseline", analysis_id, "approve", body.comment,
           {"baseline_status": "draft"}, {"baseline_status": "approved", "requirements": counts})
    return {"baseline_status": "approved", "requirements": counts}


# --------------------------------------------------------------------------
# Compliance mappings: approve / flag incorrect interpretation
# --------------------------------------------------------------------------
@router.post("/mappings/{mapping_id}/review")
def review_mapping(mapping_id: int, body: MappingReview, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("mapping:review"))):
    mapping = db.get(ComplianceMapping, mapping_id)
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    if body.action == "flag" and not body.comment.strip():
        raise HTTPException(400, "Explain why the interpretation is incorrect")
    previous = {"status": mapping.status}
    mapping.status = "approved" if body.action == "approve" else "flagged"
    db.commit()
    record(db, request, user, mapping.analysis_id, "mapping", mapping.id, body.action, body.comment, previous,
           {"status": mapping.status, "requirement": mapping.requirement_id, "control": mapping.control_ref})
    return row_dict(mapping)


# --------------------------------------------------------------------------
# SDLC recommendation: approve / override
# --------------------------------------------------------------------------
def _context_from_db(db: Session, analysis: Analysis, sdlc: SdlcRecommendation) -> dict:
    """Rebuild the agent context from stored results (used to regenerate a workflow)."""
    rows = lambda model: [row_dict(r) for r in db.scalars(select(model).where(model.analysis_id == analysis.id))]  # noqa: E731
    return {"project": {"domain": analysis.project.domain, "name": analysis.project.name,
                        "description": analysis.project.description},
            "requirements": rows(Requirement), "findings": rows(Finding), "risks": rows(Risk),
            "sdlc_factors": sdlc.factors}


@router.post("/analyses/{analysis_id}/sdlc/review")
def review_sdlc(analysis_id: int, body: SdlcReview, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("sdlc:approve"))):
    analysis = _analysis(db, analysis_id)
    sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == analysis_id))
    if not sdlc:
        raise HTTPException(404, "No SDLC recommendation for this analysis")
    previous = {"status": sdlc.status, "approved_model": sdlc.approved_model}
    if body.action == "approve":
        sdlc.status, sdlc.approved_model = "approved", sdlc.recommended_model
    else:
        if body.model not in MODELS:
            raise HTTPException(400, f"Model must be one of {MODELS}")
        if not body.comment.strip():
            raise HTTPException(400, "A justification is required to override the recommendation")
        sdlc.status, sdlc.approved_model = "overridden", body.model
    # The workflow must always describe the adopted model: regenerate it (rule-based) when it does not,
    # e.g. after an override, or when the recommendation is approved again after an earlier override.
    wf = db.scalar(select(Workflow).where(Workflow.analysis_id == analysis_id))
    if not wf or wf.sdlc_model != sdlc.approved_model:
        phases, applied = workflow_agent.build_workflow(_context_from_db(db, analysis, sdlc), sdlc.approved_model)
        note = "rules (regenerated for the human-approved SDLC model)"
        if wf:
            wf.sdlc_model, wf.phases, wf.customisations, wf.status = sdlc.approved_model, phases, applied, "draft"
            wf.generated_by = note
        else:
            db.add(Workflow(analysis_id=analysis_id, sdlc_model=sdlc.approved_model, phases=phases,
                            customisations=applied, generated_by=note))
    db.commit()
    record(db, request, user, analysis_id, "sdlc", sdlc.id, body.action, body.comment, previous,
           {"status": sdlc.status, "approved_model": sdlc.approved_model})
    return row_dict(sdlc)


@router.post("/analyses/{analysis_id}/workflow/review")
def review_workflow(analysis_id: int, body: WorkflowReview, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("workflow:approve"))):
    wf = db.scalar(select(Workflow).where(Workflow.analysis_id == analysis_id))
    sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == analysis_id))
    if not wf:
        raise HTTPException(404, "No workflow for this analysis")
    if body.action == "approve" and (not sdlc or sdlc.status == "pending_approval"):
        raise HTTPException(400, "Approve or override the SDLC recommendation before approving the workflow")
    previous = {"status": wf.status}
    wf.status = "approved" if body.action == "approve" else "changes_requested"
    db.commit()
    record(db, request, user, analysis_id, "workflow", wf.id, body.action, body.comment, previous,
           {"status": wf.status, "sdlc_model": wf.sdlc_model})
    return {"status": wf.status}


# --------------------------------------------------------------------------
# Approval centre
# --------------------------------------------------------------------------
@router.get("/approvals")
def approval_history(analysis_id: int | None = None, limit: int = 200, db: Session = Depends(get_db),
                     _: User = Depends(get_current_user)):
    query = select(Approval).order_by(Approval.timestamp.desc()).limit(min(limit, 1000))
    if analysis_id:
        query = query.where(Approval.analysis_id == analysis_id)
    return [row_dict(a) for a in db.scalars(query).all()]


@router.get("/approvals/pending")
def pending_approvals(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    analyses = db.scalars(select(Analysis).where(Analysis.status.in_(["completed", "completed_with_warnings"]))
                          .order_by(Analysis.id.desc()).limit(50)).all()
    items = []
    for a in analyses:
        base = {"analysis_id": a.id, "project_name": a.project.name, "version": a.version,
                "finished_at": a.finished_at.isoformat() if a.finished_at else None}
        pending_reqs = db.scalar(select(func.count()).select_from(Requirement).where(
            Requirement.analysis_id == a.id, Requirement.status == "pending_review"))
        mappings = db.scalars(select(ComplianceMapping).where(ComplianceMapping.analysis_id == a.id,
                                                              ComplianceMapping.status == "pending")
                              .order_by(ComplianceMapping.needs_verification.desc())).all()
        sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == a.id))
        wf = db.scalar(select(Workflow).where(Workflow.analysis_id == a.id))
        if pending_reqs:
            items.append({**base, "type": "requirements", "count": pending_reqs, "role": "Business Analyst / PM",
                          "description": f"{pending_reqs} requirement(s) awaiting review"})
        if a.baseline_status != "approved":
            items.append({**base, "type": "baseline", "count": 1, "role": "Business Analyst / PM",
                          "description": "Requirement baseline awaiting approval"})
        if mappings:
            items.append({**base, "type": "mappings", "count": len(mappings), "role": "Compliance Officer",
                          "description": f"{len(mappings)} compliance mapping(s) awaiting review "
                                         f"({sum(1 for m in mappings if m.needs_verification)} need verification)",
                          "mappings": [row_dict(m) for m in mappings[:25]]})
        if sdlc and sdlc.status == "pending_approval":
            items.append({**base, "type": "sdlc", "count": 1, "role": "Project Manager",
                          "description": f"SDLC recommendation '{sdlc.recommended_model}' awaiting approval",
                          "recommended_model": sdlc.recommended_model})
        if wf and wf.status != "approved":
            items.append({**base, "type": "workflow", "count": 1, "role": "Project Manager",
                          "description": f"{wf.sdlc_model} workflow ({wf.status}) awaiting approval",
                          "sdlc_status": sdlc.status if sdlc else None})
        # Stage 2 (refinement) decisions
        changes = db.scalars(select(RequirementChange).where(RequirementChange.analysis_id == a.id,
                                                             RequirementChange.status == "pending_review")).all()
        if changes:
            items.append({**base, "type": "requirement_changes", "count": len(changes),
                          "role": "Business Analyst / PM (not the proposer)",
                          "description": f"{len(changes)} requirement change request(s) awaiting review",
                          "changes": [row_dict(c) for c in changes]})
        interviews = db.scalars(select(StakeholderInteraction).where(
            StakeholderInteraction.analysis_id == a.id, StakeholderInteraction.status == "pending_review")).all()
        if interviews:
            items.append({**base, "type": "stakeholder_interactions", "count": len(interviews),
                          "role": "Business Analyst / PM",
                          "description": f"{len(interviews)} stakeholder interview(s) awaiting review",
                          "interviews": [row_dict(i) for i in interviews]})
        risks = db.scalars(select(Risk).where(Risk.analysis_id == a.id, Risk.status == "open",
                                              Risk.severity.in_(["High", "Critical"]))).all()
        if risks:
            items.append({**base, "type": "risks", "count": len(risks), "role": "Project Manager / Security Officer",
                          "description": f"{len(risks)} high/critical risk(s) awaiting acceptance or mitigation decision",
                          "risks": [row_dict(r) for r in risks]})
    return {"items": items, "generated_at": datetime.now(timezone.utc).isoformat()}
