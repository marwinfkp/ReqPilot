"""Central orchestrator: runs the agents in a fixed order on a shared context.

USER SUBMITS -> extraction -> classification -> quality/conflicts -> compliance (RAG)
-> security & privacy -> risk -> SDLC scoring -> workflow -> documentation
-> traceability -> validation -> summary -> results stored in PostgreSQL.

* Runs in a background thread; the frontend polls the Analysis row for
  progress (percentage + current stage). The user is never asked anything.
* Every agent is timed and recorded in `agent_runs` (status, mode, LLM calls).
* A failing non-critical agent is recorded as failed and the pipeline
  continues; nothing is fabricated for it. Extraction and SDLC selection are
  critical: without them there is nothing meaningful to report.
"""
import threading
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import clarification, llm
from app.agents import (classification, compliance, documentation, extraction, finding, quality, risk,
                        sdlc_selection, security_privacy, validation, workflow)
from app.agents.clarification_context import attach_to_requirements
from app.database import SessionLocal
from app.models import (AgentRun, Analysis, ComplianceMapping, Finding, Requirement, Risk, SdlcRecommendation,
                        Setting, TraceRecord, Workflow)
from app.security import audit, find_injection, mask_sensitive

# (agent name, stage shown to the user, function, critical?, required context key)
STAGES = [
    ("Requirement Extraction Agent", "Extracting and normalising requirements", extraction.run, True, None),
    ("Requirement Classification Agent", "Classifying requirements (multi-label)", classification.run, False, None),
    ("Requirement Quality & Conflict Agent", "Detecting ambiguity, duplicates and conflicts", quality.run, False, None),
    ("Compliance & Regulatory Agent", "Retrieving regulatory evidence and mapping controls", compliance.run, False, None),
    ("Security & Privacy Agent", "Analysing security and privacy coverage", security_privacy.run, False, None),
    ("Risk Analysis Agent", "Assessing project risks", risk.run, False, None),
    ("SDLC Selection Agent", "Scoring and ranking SDLC models", sdlc_selection.run, True, None),
    ("SDLC Workflow Generation Agent", "Generating the project-specific SDLC workflow", workflow.run, False, "sdlc"),
    ("Documentation Agent", "Writing user stories, acceptance criteria and use cases",
     documentation.run_artefacts, False, None),
    ("Traceability Builder", "Building the requirements traceability matrix", documentation.run_traceability, False, None),
    ("Validation Agent", "Validating consistency, evidence and completeness", validation.run, False, None),
    ("Summary Writer", "Writing the executive summary", documentation.run_summary, False, None),
]


def get_setting(db: Session, key: str, default):
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row:
        row.value, row.updated_at = value, datetime.now(timezone.utc)
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def start_in_background(analysis_id: int) -> None:
    threading.Thread(target=run_analysis, args=(analysis_id,), daemon=True).start()


def new_context(db: Session, project: dict, sources: list[dict], clarifications: list[dict] | None = None) -> dict:
    """The shared agent context. `clarifications` (Stage 1 questions + answers) is
    kept separate from `sources`, so the original submission is never rewritten."""
    return {"db": db, "project": project, "sources": sources, "requirements": [], "requirement_ids": set(),
            "findings": [], "mappings": [], "applicable_sources": [], "security_coverage": [], "risks": [],
            "use_cases": [], "traceability": [], "validation": [], "summary": "", "key_findings": [],
            "reference_checks": [], "agent_runs": [], "llm_log": [], "submitted_count": 0,
            "sdlc_weights": get_setting(db, "sdlc_factor_weights", {}), "metrics": {},
            "clarifications": clarifications or [], "project_clarifications": [], "stage": "stage1"}


def project_dict(project) -> dict:
    return {"name": project.name, "domain": project.domain, "description": project.description,
            "jurisdiction": project.jurisdiction, "scale": project.scale, "budget": project.budget,
            "schedule": project.schedule, "stakeholder_availability": project.stakeholder_availability}


def input_safety_checks(ctx: dict) -> None:
    """Flag (but still analyse, as data) sensitive values and injection attempts."""
    text = "\n".join(s["text"] for s in ctx["sources"])
    _, masked = mask_sensitive(text)
    ctx["masked_values"] = masked
    if masked:
        ctx["findings"].append(finding(
            "input_warning", "sensitive_data", "Medium", f"{masked} sensitive-looking value(s) masked before AI processing",
            [], explanation="E-mail addresses, card/account/Aadhaar/PAN/phone numbers were replaced by placeholders "
                            "before any text was sent to the LLM. Use synthetic data in requirement documents."))
    injections = find_injection(text)
    if injections:
        ctx["findings"].append(finding(
            "input_warning", "prompt_injection", "High", "Instruction-like text detected in the submission", [],
            explanation="The submission contains text that tries to instruct the AI. It was treated strictly as "
                        "data and did not change the analysis.", evidence="; ".join(injections[:5])))


def run_pipeline(ctx: dict, progress=None) -> dict:
    """Run all agents on `ctx`. `progress(percent, stage)` reports progress."""
    started = time.perf_counter()
    input_safety_checks(ctx)
    mode = "llm+rules" if llm.enabled() else "rules"
    for index, (agent, stage, function, critical, needs) in enumerate(STAGES):
        if progress:
            progress(int(100 * index / len(STAGES)), stage)
        if function is documentation.run_summary:  # the summary writer needs the metrics
            ctx["metrics"] = compute_metrics(ctx, time.perf_counter() - started)
        ctx["llm_log"] = []
        t0 = time.perf_counter()
        if needs and needs not in ctx:
            status, message = "skipped", f"Skipped: required input '{needs}' is missing because an earlier agent failed."
        else:
            try:
                status, message = function(ctx)
            except Exception as exc:  # recorded honestly; never replaced by fake output
                status, message = "failed", f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
        ctx["agent_runs"].append({"order": index + 1, "agent": agent, "status": status, "mode": mode,
                                  "duration_ms": int((time.perf_counter() - t0) * 1000), "message": message,
                                  "llm_calls": ctx["llm_log"], "stage": ctx.get("stage", "stage1")})
        if agent == "Requirement Extraction Agent":
            ctx["requirement_ids"] = {r["req_id"] for r in ctx["requirements"]}
            # Link clarification answers to the extracted requirements (as supplementary
            # information - the extracted statements themselves are not changed).
            attach_to_requirements(ctx)
            if status == "failed" or not ctx["requirements"]:
                ctx["fatal"] = "No requirements could be extracted from the submission."
                break
        if critical and status == "failed":
            ctx["fatal"] = f"{agent} failed: {message}"
    ctx["metrics"] = compute_metrics(ctx, time.perf_counter() - started)
    return ctx


def compute_metrics(ctx: dict, seconds: float) -> dict:
    reqs, findings = ctx["requirements"], ctx["findings"]
    count = lambda **kw: sum(1 for f in findings if all(f[k] in v for k, v in kw.items()))  # noqa: E731
    calls = [c for run in ctx["agent_runs"] for c in run["llm_calls"]]
    records = ctx.get("traceability", [])
    mappings = ctx["mappings"]
    sdlc = ctx.get("sdlc") or {}
    return {
        "submitted": ctx["submitted_count"], "extracted": len(reqs),
        "functional": sum(1 for r in reqs if r["kind"] in ("functional", "business_rule")),
        "non_functional": sum(1 for r in reqs if r["kind"] in ("non_functional", "constraint")),
        "business_rules": sum(1 for r in reqs if r["kind"] == "business_rule"),
        "constraints": sum(1 for r in reqs if r["kind"] == "constraint"),
        "quality_issues": count(kind=("quality",)),
        "ambiguities": count(issue_type=("ambiguity",)),
        "conflicts": count(issue_type=("conflict", "stakeholder_conflict")),
        "duplicates": count(issue_type=("duplicate",)),
        "compliance_mappings": len(mappings),
        "mapped_requirements": len({m["requirement_id"] for m in mappings}),
        "compliance_gaps": count(kind=("compliance_gap",)),
        "security_gaps": count(kind=("security_gap",)),
        "threats": count(kind=("threat",)),
        "risks": len(ctx.get("risks", [])),
        "high_risks": sum(1 for r in ctx.get("risks", []) if r["severity"] in ("High", "Critical")),
        "high_risk_findings": sum(1 for f in findings if f["severity"] in ("High", "Critical")),
        "recommended_sdlc": sdlc.get("recommended_model", ""),
        "sdlc_score": sdlc["ranking"][0]["score"] if sdlc else None,
        "traceability_coverage": round(sum(r["coverage"] for r in records) / len(records), 3) if records else 0,
        "citations_verified": sum(1 for m in mappings if m["citation_verified"]),
        "retrieval_method": ctx.get("retrieval_method", "none"),
        "llm_calls": len(calls),
        "llm_input_tokens": sum(c["input_tokens"] or 0 for c in calls),
        "llm_output_tokens": sum(c["output_tokens"] or 0 for c in calls),
        "llm_references_checked": len(ctx["reference_checks"]),
        "llm_invalid_references": sum(1 for c in ctx["reference_checks"] if not c["valid"]),
        "masked_values": ctx.get("masked_values", 0),
        "duration_seconds": round(seconds, 2),
        # Clarification provenance (Stage 1)
        "clarification_questions": len(ctx.get("clarifications", [])),
        "clarification_answered": sum(1 for q in ctx.get("clarifications", []) if q["status"] == "answered"),
        "clarification_deferred": sum(1 for q in ctx.get("clarifications", []) if q["status"] == "deferred"),
        "clarification_unresolved": sum(1 for q in ctx.get("clarifications", [])
                                        if q["status"] in ("open", "needs_stakeholder")),
        "issues_resolved": sum(1 for f in findings if f.get("resolution") == "resolved"),
        "issues_open": sum(1 for f in findings if f["kind"] == "quality" and f.get("resolution") != "resolved"),
    }


# --------------------------------------------------------------------------
# Background job: load the submission, run, store
# --------------------------------------------------------------------------
def run_analysis(analysis_id: int) -> None:
    """Background job for Stage 1. Depending on the analysis state it runs
    1. the preliminary analysis + clarification questions (status "preliminary"), then
       - interactive submissions stop at "awaiting_clarification" until the user continues;
       - other API submissions continue immediately with every question left unresolved;
    2. the complete, unchanged 12-agent pipeline with the clarification context."""
    db = SessionLocal()
    try:
        analysis = db.get(Analysis, analysis_id)
        project = project_dict(analysis.project)
        if analysis.stage1_status == "preliminary":
            analysis.status, analysis.current_stage, analysis.progress = "preliminary", "Preliminary requirements analysis", 0
            db.commit()
            clarification.run_preliminary(db, analysis, project)
            if (analysis.preliminary or {}).get("interactive"):
                analysis.status = analysis.stage1_status = "awaiting_clarification"
                analysis.current_stage = "Awaiting clarification"
                db.commit()
                return
            clarification.log_run(db, analysis.id, "Clarification Responses", "skipped", "human", time.perf_counter(),
                                  "Non-interactive submission: the full analysis started immediately. All "
                                  f"{analysis.preliminary.get('questions', 0)} question(s) remain unresolved; nothing "
                                  "was assumed.")
        analysis.status, analysis.stage1_status, analysis.error = "running", "analysing", ""
        db.commit()

        def progress(percent: int, stage: str) -> None:
            analysis.progress, analysis.current_stage = percent, stage
            db.commit()

        ctx = new_context(db, project, analysis.input_sources, clarification.context_for_pipeline(db, analysis.id))
        run_pipeline(ctx, progress)
        save_results(db, analysis, ctx)
        audit(db, "analysis.completed", entity_type="analysis", entity_id=analysis.id,
              details={"status": analysis.status, "mode": analysis.mode, "requirements": len(ctx["requirements"]),
                       "recommended_sdlc": ctx["metrics"].get("recommended_sdlc"), "stage": "stage1",
                       "clarifications_answered": ctx["metrics"].get("clarification_answered")})
    except Exception as exc:
        traceback.print_exc()
        db.rollback()
        analysis = db.get(Analysis, analysis_id)
        if analysis:
            analysis.status, analysis.error = "failed", f"{type(exc).__name__}: {exc}"
            analysis.stage1_status = "failed"
            analysis.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def clear_results(db: Session, analysis_id: int) -> None:
    """Remove the outputs of a failed pipeline run before a retry (the preliminary
    analysis log, questions and answers are kept)."""
    for model in (Requirement, Finding, ComplianceMapping, Risk, SdlcRecommendation, Workflow, TraceRecord):
        db.query(model).filter(model.analysis_id == analysis_id).delete()
    db.query(AgentRun).filter(AgentRun.analysis_id == analysis_id, AgentRun.order > 0).delete()
    db.commit()


def save_results(db: Session, analysis: Analysis, ctx: dict) -> None:
    aid = analysis.id
    for run in ctx["agent_runs"]:
        db.add(AgentRun(analysis_id=aid, **run))
    for r in ctx["requirements"]:
        db.add(Requirement(analysis_id=aid, req_id=r["req_id"], statement=r["statement"],
                           original_text=r["original_text"], source=r["source"], kind=r["kind"],
                           categories=r["categories"], priority=r["priority"], risk_level=r["risk_level"],
                           confidence=r["confidence"], dependencies=r["dependencies"], user_story=r["user_story"],
                           acceptance_criteria=r["acceptance_criteria"],
                           test_recommendations=r["test_recommendations"], generated_by=r["generated_by"],
                           status=r.get("status", "pending_review"), supplementary=r.get("supplementary", [])))
    for f in ctx["findings"]:
        db.add(Finding(analysis_id=aid, **f))
    for m in ctx["mappings"]:
        db.add(ComplianceMapping(analysis_id=aid, **m))
    for r in ctx.get("risks", []):
        db.add(Risk(analysis_id=aid, **r))
    sdlc = ctx.get("sdlc")
    if sdlc:
        db.add(SdlcRecommendation(analysis_id=aid, recommended_model=sdlc["recommended_model"],
                                  is_hybrid=sdlc["is_hybrid"], factors=sdlc["factors"], ranking=sdlc["ranking"],
                                  rules_applied=sdlc["rules_applied"], justification=sdlc["justification"],
                                  trade_offs=sdlc["trade_offs"], hybrid_explanation=sdlc["hybrid_explanation"],
                                  generated_by=sdlc["generated_by"]))
    wf = ctx.get("workflow")
    if wf:
        db.add(Workflow(analysis_id=aid, **wf))
    for t in ctx.get("traceability", []):
        db.add(TraceRecord(analysis_id=aid, **t))

    analysis.submitted_count = ctx["submitted_count"]
    analysis.summary, analysis.key_findings = ctx["summary"], ctx["key_findings"]
    analysis.metrics, analysis.security_coverage = ctx["metrics"], ctx["security_coverage"]
    analysis.applicable_sources, analysis.use_cases = ctx["applicable_sources"], ctx["use_cases"]
    analysis.validation_report = ctx["validation"]
    analysis.progress, analysis.finished_at = 100, datetime.now(timezone.utc)
    clarification.save_resolutions(db, aid, ctx)
    if ctx.get("fatal"):
        analysis.status, analysis.error, analysis.current_stage = "failed", ctx["fatal"], "Failed"
        analysis.stage1_status = "failed" if ctx.get("stage", "stage1") == "stage1" else analysis.stage1_status
    else:
        # "skipped" here means "not re-run, output retained" (Stage 2 regeneration) - not a degradation.
        retained = ctx.get("retained_agents", set())
        degraded = any(r["status"] != "completed" and r["agent"] not in retained for r in ctx["agent_runs"]) or \
            any(c["status"] == "fail" for c in ctx["validation"])
        analysis.status = "completed_with_warnings" if degraded else "completed"
        analysis.current_stage = "Completed"
        if ctx.get("stage", "stage1") == "stage1":
            analysis.stage1_status = "completed"
    db.commit()
