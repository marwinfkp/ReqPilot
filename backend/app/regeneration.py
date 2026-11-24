"""Stage 2 - dependency-aware selective regeneration.

A regeneration NEVER modifies the analysis it starts from. It creates a new
analysis VERSION (a new `analyses` row linked through parent/root IDs), copies
the previous outputs, applies the approved inputs and re-runs only the agents
that are affected. Earlier versions stay readable and comparable.

Inputs that trigger a regeneration (not yet applied to any version):
  * approved requirement changes (edit, new, obsolete, acceptance criteria, ...)
  * answers given in Stage 2 to clarification questions
  * accepted stakeholder interviews

How agents are selected:
1. Direct triggers - each input is classified by what it touches. An agent is
   triggered when the input changes information that agent reads, e.g. a
   wording change triggers Classification, Quality and Documentation; it
   triggers Compliance only if the regulatory topics of the text changed, and
   Risk/SDLC only if a measurable target or an SDLC-relevant keyword changed.
2. Propagation - after an agent re-runs, its output is compared with the
   previous version ("fingerprint"). Downstream agents re-run only if an output
   they read actually changed. Validation and Summary always re-run.
Agents that do not re-run keep the previous output and are logged as
"skipped (retained)". The Extraction Agent never re-runs in Stage 2: the
requirement set is changed only through approved changes.

Knowledge-base evidence: a re-run agent retrieves its passages again from the
updated requirements (nothing retrieved for an earlier version is reused), and
its new citations are stored with the new version. Retained outputs keep the
citations they had; the earlier version and its evidence are never changed.
"""
import json
import threading
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clarification, llm
from app.agents import (classification, compliance, documentation, quality, risk, sdlc_selection,
                        security_privacy, validation, workflow)
from app.agents.clarification_context import AREA_TOPIC, attach_to_requirements
from app.agents.extraction import detect_kind, normalise
from app.database import SessionLocal
from app.models import (AgentRun, Analysis, ClarificationQuestion, ComplianceMapping, Finding, RegenerationRun,
                        Requirement, RequirementChange, Risk, SdlcRecommendation, StakeholderInteraction, TraceRecord,
                        Workflow)
from app.rules import SECURITY_AREAS, TOPICS, detect_topics, domain_code, find_keywords
from app.security import audit
from app.serializers import row_dict

# Execution order, display names and the outputs ("artefacts") each agent reads.
# Quality output is split into the aspects other agents actually read, so a change to
# one finding only propagates to the agents that depend on that aspect.
AGENTS = [
    ("extraction", "Requirement Extraction Agent", set()),
    ("classification", "Requirement Classification Agent", set()),
    ("quality", "Requirement Quality & Conflict Agent", {"categories"}),
    ("compliance", "Compliance & Regulatory Agent", set()),
    ("security", "Security & Privacy Agent", {"open_ambiguity"}),
    ("risk", "Risk Analysis Agent", {"categories", "open_issue_counts", "compliance", "security"}),
    ("sdlc", "SDLC Selection Agent", {"categories", "open_issue_counts"}),
    ("workflow", "SDLC Workflow Generation Agent", {"categories", "open_issue_counts", "compliance", "security",
                                                    "risk", "sdlc"}),
    ("documentation", "Documentation Agent", {"categories"}),
    ("traceability", "Traceability Builder", {"categories", "compliance", "security", "workflow", "documentation"}),
    ("validation", "Validation Agent", None),  # always re-run
    ("summary", "Summary Writer", None),  # always re-run
]
PRODUCES = {"classification": {"categories"}, "quality": {"quality_issues", "open_ambiguity", "open_issue_counts"},
            "compliance": {"compliance"}, "security": {"security"}, "risk": {"risk"}, "sdlc": {"sdlc"},
            "workflow": {"workflow"}, "documentation": {"documentation"}, "traceability": {"traceability"}}
ARTEFACT_LABELS = {"categories": "requirement categories", "quality_issues": "quality findings",
                   "open_ambiguity": "open ambiguity findings", "open_issue_counts": "open conflicts/ambiguities",
                   "compliance": "compliance results", "security": "security results", "risk": "risk register",
                   "sdlc": "SDLC recommendation", "workflow": "workflow", "documentation": "documentation",
                   "traceability": "traceability"}
NAMES = {key: name for key, name, _ in AGENTS}
INPUTS_OF = {key: inputs or set() for key, _, inputs in AGENTS}
RUNNERS = {"classification": classification.run, "quality": quality.run, "compliance": compliance.run,
           "security": security_privacy.run, "risk": risk.run, "sdlc": sdlc_selection.run, "workflow": workflow.run,
           "documentation": documentation.run_artefacts, "traceability": documentation.run_traceability,
           "validation": validation.run, "summary": documentation.run_summary}
OUTPUTS = {"classification": "requirement categories and priorities", "quality": "quality findings and resolutions",
           "compliance": "compliance mappings and gaps", "security": "security coverage, gaps, threats, privacy",
           "risk": "risk register", "sdlc": "SDLC factors, ranking and recommendation",
           "workflow": "SDLC workflow", "documentation": "user stories, acceptance criteria, use cases",
           "traceability": "traceability matrix", "validation": "validation report", "summary": "executive summary",
           "extraction": "extracted requirement set"}

# Keywords the rule-based parts of each agent read from requirement text.
RISK_WORDS = ["legacy", "core banking", "cbs", "mainframe", "batch", "model", "machine learning", "risk scor",
              "anomal", "99.9"]
WORKFLOW_WORDS = ["legacy", "core banking", "cbs", "mainframe", "migrat", "transfer", "payment", "upi", "imps",
                  "neft", "loan", "credit score", "emi", "machine learning", "ml model", "model", "anomal", "kyc",
                  "aadhaar", "onboard"]
SDLC_WORDS = (sdlc_selection._CHANGE_WORDS + sdlc_selection._CD_WORDS + sdlc_selection._LEGACY_WORDS
              + sdlc_selection._FORMAL_WORDS + sdlc_selection._UNCERTAINTY_WORDS + ["99.9"])
SECURITY_WORDS = [k for area in SECURITY_AREAS.values() for k in area["keywords"]]
SDLC_TOPICS = {"stakeholder_availability", "budget_schedule", "release_cadence"}
TARGET_TOPICS = {"performance_targets", "availability_sla", "business_continuity", "integration_behaviour",
                 "transaction_limits"}

_lock = threading.Lock()


# --------------------------------------------------------------------------
# Version family helpers
# --------------------------------------------------------------------------
def family(db: Session, analysis: Analysis) -> list[Analysis]:
    root = analysis.root_analysis_id or analysis.id
    rows = db.scalars(select(Analysis).where((Analysis.id == root) | (Analysis.root_analysis_id == root))
                      .order_by(Analysis.version)).all()
    return list(rows)


def latest_version(db: Session, analysis: Analysis) -> Analysis:
    return family(db, analysis)[-1]


def pending_inputs(db: Session, analysis: Analysis) -> dict:
    """Approved / answered inputs of this version family not yet applied to a version."""
    ids = [a.id for a in family(db, analysis)]
    changes = db.scalars(select(RequirementChange).where(RequirementChange.analysis_id.in_(ids),
                                                         RequirementChange.status == "approved",
                                                         RequirementChange.applied_in_version.is_(None))
                         .order_by(RequirementChange.id)).all()
    questions = [q for q in db.scalars(select(ClarificationQuestion).where(
        ClarificationQuestion.analysis_id.in_(ids), ClarificationQuestion.applied_in_version.is_(None))).all()
        if q.response and q.response.stage == 2 and q.status == "answered"]
    interviews = db.scalars(select(StakeholderInteraction).where(
        StakeholderInteraction.analysis_id.in_(ids), StakeholderInteraction.status == "accepted",
        StakeholderInteraction.applied_in_version.is_(None))).all()
    return {"changes": list(changes), "questions": questions, "interviews": list(interviews)}


# --------------------------------------------------------------------------
# Rebuild the agent context of an existing version from the database
# --------------------------------------------------------------------------
def _clean(row) -> dict:
    data = row_dict(row)
    data.pop("id", None)
    data.pop("analysis_id", None)
    return data


def context_from_version(db: Session, analysis: Analysis) -> dict:
    from app.pipeline import new_context, project_dict

    source_names = {s["name"] for s in analysis.input_sources or []}
    ctx = new_context(db, project_dict(analysis.project), analysis.input_sources)
    ctx["stage"] = "stage2"
    reqs = []
    for row in db.scalars(select(Requirement).where(Requirement.analysis_id == analysis.id)
                          .order_by(Requirement.id)).all():
        r = _clean(row)
        r["has_stakeholder_source"] = r["source"] not in source_names
        r["supplementary"] = list(r.get("supplementary") or [])
        reqs.append(r)
    ctx["all_requirements"] = reqs
    ctx["requirements"] = [r for r in reqs if r["status"] != "obsolete"]
    ctx["requirement_ids"] = {r["req_id"] for r in ctx["requirements"]}
    ctx["findings"] = [_clean(f) for f in db.scalars(select(Finding).where(Finding.analysis_id == analysis.id)
                                                     .order_by(Finding.id)).all()]
    ctx["mappings"] = [_clean(m) for m in db.scalars(select(ComplianceMapping).where(
        ComplianceMapping.analysis_id == analysis.id).order_by(ComplianceMapping.id)).all()]
    ctx["risks"] = [_clean(r) for r in db.scalars(select(Risk).where(Risk.analysis_id == analysis.id)
                                                  .order_by(Risk.id)).all()]
    sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == analysis.id))
    if sdlc:
        ctx["sdlc"] = _clean(sdlc)
        ctx["sdlc_factors"] = sdlc.factors
    wf = db.scalar(select(Workflow).where(Workflow.analysis_id == analysis.id))
    if wf:
        ctx["workflow"] = _clean(wf)
    ctx["traceability"] = [_clean(t) for t in db.scalars(select(TraceRecord).where(
        TraceRecord.analysis_id == analysis.id).order_by(TraceRecord.id)).all()]
    ctx["security_coverage"] = analysis.security_coverage or []
    ctx["applicable_sources"] = analysis.applicable_sources or []
    ctx["use_cases"] = analysis.use_cases or []
    ctx["validation"] = analysis.validation_report or []
    ctx["summary"], ctx["key_findings"] = analysis.summary, analysis.key_findings or []
    ctx["submitted_count"] = analysis.submitted_count
    ctx["retrieval_method"] = (analysis.metrics or {}).get("retrieval_method", "none")
    ctx["masked_values"] = (analysis.metrics or {}).get("masked_values", 0)
    ctx["clarifications"] = family_clarifications(db, analysis)
    return ctx


def family_clarifications(db: Session, analysis: Analysis) -> list[dict]:
    """All questions of the version family, linked by final requirement IDs."""
    result = []
    for member in family(db, analysis):
        if member.version > analysis.version:
            continue
        for item in clarification.context_for_pipeline(db, member.id):
            item["lines"] = []  # IDs are final now; line numbers only mattered before extraction
            result.append(item)
    return result


# --------------------------------------------------------------------------
# Fingerprints: has an agent's output changed?
# --------------------------------------------------------------------------
def _findings_of(ctx: dict, producer: str) -> list[dict]:
    return [f for f in ctx["findings"] if finding_producer(f) == producer]


def finding_producer(f: dict) -> str:
    kind = f["kind"]
    if kind == "quality" or (kind == "assumption" and f["issue_type"] != "sdlc_factor"):
        return "quality"
    if kind in ("compliance_gap", "expert_review"):
        return "compliance"
    if kind in ("security_gap", "threat", "privacy"):
        return "security"
    if kind == "assumption":
        return "sdlc"
    return "pipeline"


def fingerprint(ctx: dict, artefact: str) -> str:
    """A stable text form of one artefact, to detect whether a re-run changed it."""
    from app.agents.clarification_context import is_open

    reqs = ctx["requirements"]
    open_quality = [f for f in _findings_of(ctx, "quality") if is_open(f)]
    pick = {
        "categories": lambda: [(r["req_id"], r["categories"], r["priority"]) for r in reqs],
        "quality_issues": lambda: sorted((f["issue_type"], f["requirement_ids"], f["severity"],
                                          f.get("resolution", "")) for f in _findings_of(ctx, "quality")),
        "open_ambiguity": lambda: sorted(i for f in open_quality if f["issue_type"] == "ambiguity"
                                         for i in f["requirement_ids"]),
        "open_issue_counts": lambda: [sum(f["issue_type"] == t for f in open_quality) for t in
                                      ("ambiguity", "conflict", "stakeholder_conflict", "incompleteness", "infeasibility")]
        + [sorted(f["requirement_ids"] for f in open_quality if f["issue_type"] in ("conflict", "stakeholder_conflict"))],
        "compliance": lambda: [sorted((m["requirement_id"], m["chunk_id"] or 0) for m in ctx["mappings"]),
                               sorted((f["issue_type"], f.get("resolution", "")) for f in _findings_of(ctx, "compliance"))],
        "security": lambda: [[(a["area"], a["status"]) for a in ctx["security_coverage"]],
                             sorted((f["kind"], f["issue_type"], f["title"], f.get("resolution", ""))
                                    for f in _findings_of(ctx, "security"))],
        "risk": lambda: sorted((r["description"], r["score"]) for r in ctx.get("risks", [])),
        "sdlc": lambda: ([ctx["sdlc"]["recommended_model"], [(r["model"], r["score"]) for r in ctx["sdlc"]["ranking"]],
                          [(f["id"], f["value"]) for f in ctx["sdlc"]["factors"]]] if ctx.get("sdlc") else None),
        "workflow": lambda: (ctx.get("workflow") or {}).get("phases"),
        "documentation": lambda: [[(r["req_id"], r["user_story"], r["acceptance_criteria"]) for r in reqs],
                                  ctx.get("use_cases")],
        "traceability": lambda: ctx.get("traceability"),
    }.get(artefact)
    return json.dumps(pick(), sort_keys=True, default=str) if pick else ""


def _remove_previous_outputs(ctx: dict, agent: str) -> None:
    """Agents append findings; drop the ones this agent produced last time before re-running it."""
    if agent in ("quality", "compliance", "security", "sdlc"):
        ctx["findings"] = [f for f in ctx["findings"] if finding_producer(f) != agent]


# --------------------------------------------------------------------------
# 1. Apply inputs and work out which agents are triggered directly
# --------------------------------------------------------------------------
def _text_triggers(old: str, new: str, categories: list[str]) -> dict[str, str]:
    """Which agents read something that differs between two texts."""
    triggers = {}
    if set(detect_topics(old)) != set(detect_topics(new)) or {"regulatory", "privacy"} & set(categories):
        triggers["compliance"] = "regulatory/privacy topics are involved or changed"
    if set(find_keywords(old, SECURITY_WORDS)) != set(find_keywords(new, SECURITY_WORDS)) or \
            {"security", "privacy"} & set(categories):
        triggers["security"] = "security/privacy content is involved or changed"
    quantities_changed = quality.quantities(old) != quality.quantities(new)
    if quantities_changed or set(find_keywords(old, RISK_WORDS)) != set(find_keywords(new, RISK_WORDS)):
        triggers["risk"] = "a measurable target or risk-relevant wording changed"
    if (quantities_changed and {"performance", "availability", "reliability"} & set(categories)) or \
            set(find_keywords(old, SDLC_WORDS)) != set(find_keywords(new, SDLC_WORDS)):
        triggers["sdlc"] = "an SDLC decision factor's evidence changed"
    if set(find_keywords(old, WORKFLOW_WORDS)) != set(find_keywords(new, WORKFLOW_WORDS)):
        triggers["workflow"] = "wording used for workflow customisation changed"
    return triggers


def _add(direct: dict, agent: str, reason: str) -> None:
    direct.setdefault(agent, [])
    if reason not in direct[agent]:
        direct[agent].append(reason)


def apply_inputs(ctx: dict, inputs: dict) -> tuple[dict[str, list[str]], set[str], list[str]]:
    """Apply approved changes / new answers to ctx. Returns (direct triggers, changed IDs, notes)."""
    direct: dict[str, list[str]] = {}
    changed: set[str] = set()
    notes: list[str] = []
    by_id = {r["req_id"]: r for r in ctx["all_requirements"]}
    domain = ctx["project"]["domain"]

    for ch in inputs["changes"]:
        text = normalise(ch.approved_text or ch.proposed_text)
        label = f"{ch.code} ({ch.change_type.replace('_', ' ')})"
        if ch.change_type == "new":
            kind = ch.new_kind or detect_kind(text)
            req = {"req_id": "", "statement": text, "original_text": ch.approved_text or ch.proposed_text,
                   "source": f"Requirement change {ch.code}", "has_stakeholder_source": True, "kind": kind,
                   "categories": [], "priority": "Medium", "risk_level": "Low", "confidence": 0.6,
                   "dependencies": [], "user_story": "", "acceptance_criteria": [], "test_recommendations": [],
                   "generated_by": "human change", "status": "modified",
                   "supplementary": [{"kind": "change", "ref": ch.code, "text": f"Added by approved change: {ch.reason}"}]}
            _assign_new_id(req, ctx["all_requirements"], domain)
            ctx["all_requirements"].append(req)
            by_id[req["req_id"]] = req
            ch_id = req["req_id"]
            for agent, reason in (("classification", "new requirement must be classified"),
                                  ("quality", "new requirement must be checked for conflicts and duplicates"),
                                  ("compliance", "new requirement must be mapped to controls"),
                                  ("security", "security coverage may change"),
                                  ("risk", "the requirement set changed"),
                                  ("sdlc", "requirement count and categories are SDLC factors"),
                                  ("documentation", "new requirement needs a user story and acceptance criteria")):
                _add(direct, agent, f"{label}: {reason}")
            changed.add(ch_id)
            notes.append(f"{label} added {ch_id}")
            continue
        req = by_id.get(ch.requirement_id)
        if not req:
            notes.append(f"{label} skipped: {ch.requirement_id} does not exist in this version")
            continue
        old_text = req["statement"]
        if ch.change_type == "obsolete":
            req["status"] = "obsolete"
            for agent, reason in (("quality", "conflicts/duplicates involving it disappear"),
                                  ("compliance", "its mappings are dropped"), ("security", "coverage may change"),
                                  ("risk", "the requirement set changed"),
                                  ("sdlc", "requirement count and categories are SDLC factors")):
                _add(direct, agent, f"{label} on {req['req_id']}: {reason}")
            notes.append(f"{label} marked {req['req_id']} obsolete")
        elif ch.change_type == "acceptance_criteria":
            req["supplementary"].append({"kind": "acceptance_criterion", "ref": ch.code, "text": text})
            _add(direct, "quality", f"{label} on {req['req_id']}: may resolve a missing-acceptance-criteria finding")
            _add(direct, "documentation", f"{label} on {req['req_id']}: acceptance criteria changed")
            changed.add(req["req_id"])
            notes.append(f"{label} added an acceptance criterion to {req['req_id']}")
        else:  # edit | resolve_conflict | business_rule | constraint
            req["statement"] = text
            req["status"] = "modified"
            if ch.change_type in ("business_rule", "constraint"):
                req["kind"] = ch.change_type
            req["supplementary"].append({"kind": "change", "ref": ch.code,
                                         "text": f"Statement changed by approved change: {ch.reason}"})
            for agent, reason in (("classification", "wording changed"),
                                  ("quality", "conflicts, ambiguity and duplicates must be re-checked"),
                                  ("documentation", "user story and acceptance criteria must follow the new text")):
                _add(direct, agent, f"{label} on {req['req_id']}: {reason}")
            for agent, reason in _text_triggers(old_text, text, req["categories"]).items():
                _add(direct, agent, f"{label} on {req['req_id']}: {reason}")
            if ch.change_type == "constraint":
                _add(direct, "sdlc", f"{label}: constraints influence SDLC factors")
            changed.add(req["req_id"])
            notes.append(f"{label} changed {req['req_id']}")

    for q in inputs["questions"]:
        _answer_triggers(direct, changed, ctx, q.code, q.question, q.response.answer, q.topic, q.requirement_ids,
                         "clarification")
        notes.append(f"answer to {q.code} applied")
    for i in inputs["interviews"]:
        text = " ".join(f"{e.get('question', '')} {e.get('answer', '')}" for e in i.entries)
        answers = " | ".join(e.get("answer", "") for e in i.entries if e.get("answer"))
        ref = f"SI-{i.id:02d}"
        for rid in i.requirement_ids:
            if rid in by_id:
                by_id[rid]["supplementary"].append({"kind": "stakeholder", "ref": ref,
                                                    "text": f"{i.stakeholder_role}: {answers}"})
        _answer_triggers(direct, changed, ctx, ref, i.topic, text, "", i.requirement_ids, "stakeholder")
        notes.append(f"stakeholder interview {ref} ({i.stakeholder_role}) applied")

    ctx["requirements"] = [r for r in ctx["all_requirements"] if r["status"] != "obsolete"]
    ctx["requirement_ids"] = {r["req_id"] for r in ctx["requirements"]}
    return direct, changed, notes


def _answer_triggers(direct: dict, changed: set, ctx: dict, ref: str, question: str, answer: str, topic: str,
                     requirement_ids: list[str], kind: str) -> None:
    label = f"{kind} {ref}"
    linked = [r for r in ctx["requirements"] if r["req_id"] in set(requirement_ids)]
    if linked:
        for r in linked:
            changed.add(r["req_id"])
        _add(direct, "classification", f"{label}: new information on {', '.join(r['req_id'] for r in linked[:5])}")
        _add(direct, "quality", f"{label}: may resolve findings on the linked requirements")
        _add(direct, "documentation", f"{label}: acceptance criteria of linked requirements may change")
    else:
        _add(direct, "quality", f"{label}: project-level answers are re-evaluated against open findings")
    text = f"{question} {answer}"
    if topic in TOPICS or detect_topics(text) or topic == "other_obligations":
        _add(direct, "compliance", f"{label}: concerns regulatory/compliance topics")
    if topic in AREA_TOPIC.values() or find_keywords(text, SECURITY_WORDS):
        _add(direct, "security", f"{label}: concerns security/privacy controls")
    if topic in SDLC_TOPICS or find_keywords(text, SDLC_WORDS):
        _add(direct, "sdlc", f"{label}: changes the evidence for SDLC decision factors")
    if topic in TARGET_TOPICS or quality.quantities(text):
        _add(direct, "risk", f"{label}: states targets that affect risk ratings")
        _add(direct, "sdlc", f"{label}: targets affect failure-consequence and verification factors")


def _assign_new_id(req: dict, existing: list[dict], domain: str) -> None:
    prefix = {"functional": "FR", "non_functional": "NFR", "business_rule": "BR", "constraint": "CON"}.get(req["kind"], "FR")
    code = domain_code(domain)
    numbers = [int(r["req_id"].rsplit("-", 1)[-1]) for r in existing
               if r["req_id"].startswith(f"{prefix}-{code}-") and r["req_id"].rsplit("-", 1)[-1].isdigit()]
    req["req_id"] = f"{prefix}-{code}-{(max(numbers) if numbers else 0) + 1:03d}"


# --------------------------------------------------------------------------
# 2. Plan (shown to the user before confirming)
# --------------------------------------------------------------------------
DOWNSTREAM = {key: [k for k, _, inputs in AGENTS if inputs and PRODUCES.get(key, set()) & inputs]
              for key, _, _ in AGENTS}


def plan(db: Session, analysis: Analysis) -> dict:
    inputs = pending_inputs(db, analysis)
    ctx = context_from_version(db, analysis)
    direct, changed, notes = apply_inputs(ctx, inputs)
    conditional = []
    frontier = list(direct)
    while frontier:
        for nxt in DOWNSTREAM.get(frontier.pop(), []):
            if nxt not in direct and nxt not in conditional:
                conditional.append(nxt)
                frontier.append(nxt)
    order = [k for k, _, _ in AGENTS]
    upstream = set(direct) | set(conditional)
    will = [{"key": k, "agent": NAMES[k], "reasons": direct[k], "output": OUTPUTS[k]} for k in order if k in direct]
    maybe = [{"key": k, "agent": NAMES[k], "output": OUTPUTS[k],
              "reasons": ["re-runs only if " + ", ".join(sorted(ARTEFACT_LABELS[a] for a in INPUTS_OF[k]
                                                                if any(a in PRODUCES.get(u, set()) for u in upstream)))
                          + " change"]}
             for k in order if k in conditional and k not in ("validation", "summary")]
    always = [{"key": k, "agent": NAMES[k], "reasons": ["always re-run after a regeneration"], "output": OUTPUTS[k]}
              for k in ("validation", "summary")]
    retained = [{"key": k, "agent": NAMES[k], "output": OUTPUTS[k],
                 "reasons": ["requirement set is changed only through approved changes"] if k == "extraction"
                 else ["not affected by the pending inputs"]}
                for k in order if k not in direct and k not in conditional and k not in ("validation", "summary")]
    return {"source_analysis_id": analysis.id, "source_version": analysis.version,
            "is_latest": latest_version(db, analysis).id == analysis.id,
            "inputs": {"changes": [{"id": c.id, "code": c.code, "type": c.change_type, "requirement_id": c.requirement_id}
                                   for c in inputs["changes"]],
                       "questions": [{"id": q.id, "code": q.code} for q in inputs["questions"]],
                       "interviews": [{"id": i.id, "role": i.stakeholder_role} for i in inputs["interviews"]]},
            "has_inputs": any(inputs.values()), "changed_requirements": sorted(changed), "notes": notes,
            "will_rerun": will, "may_rerun": maybe, "always_rerun": always, "retained": retained}


# --------------------------------------------------------------------------
# 3. Execute (background thread)
# --------------------------------------------------------------------------
def start(db: Session, source: Analysis, user) -> RegenerationRun:
    """Create the new version row and the run record, then execute in the background."""
    with _lock:
        if db.scalar(select(RegenerationRun).where(RegenerationRun.status == "running",
                                                   RegenerationRun.source_analysis_id.in_(
                                                       [a.id for a in family(db, source)]))):
            raise ValueError("A regeneration is already running for this analysis.")
        newest = latest_version(db, source)
        if newest.id != source.id:
            raise ValueError(f"Version {newest.version} (analysis #{newest.id}) is newer - refine the latest version.")
        if not any(pending_inputs(db, source).values()):
            raise ValueError("Nothing to regenerate: there are no approved changes, new answers or accepted "
                             "stakeholder interviews that have not been applied yet.")
        new = Analysis(project_id=source.project_id, created_by=user.email, status="queued",
                       mode="openai" if llm.enabled() else "demo",
                       model_name=source.model_name if llm.enabled() else "rule-based agents (demonstration)",
                       input_text=source.input_text, input_sources=source.input_sources,
                       submitted_count=source.submitted_count, preliminary=source.preliminary,
                       stage1_status="completed", stage2_status="not_started", version=source.version + 1,
                       parent_analysis_id=source.id, root_analysis_id=source.root_analysis_id or source.id,
                       current_stage="Queued for selective regeneration")
        db.add(new)
        db.flush()
        run = RegenerationRun(source_analysis_id=source.id, new_analysis_id=new.id, previous_version=source.version,
                              new_version=new.version, created_by=user.email, status="running")
        db.add(run)
        source.stage2_status = "regenerating"
        db.commit()
    start_in_background(run.id)
    return run


def start_in_background(run_id: int) -> None:
    threading.Thread(target=execute, args=(run_id,), daemon=True).start()


def execute(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(RegenerationRun, run_id)
        source, new = db.get(Analysis, run.source_analysis_id), db.get(Analysis, run.new_analysis_id)
        new.status, new.current_stage = "running", "Dependency analysis"
        db.commit()
        started = time.perf_counter()
        inputs = pending_inputs(db, source)
        ctx = context_from_version(db, source)
        before = {a: fingerprint(ctx, a) for a in ARTEFACT_LABELS}
        previous_sdlc, previous_workflow = ctx.get("sdlc"), ctx.get("workflow")
        previous_risks = list(ctx.get("risks", []))
        direct, changed, notes = apply_inputs(ctx, inputs)
        attach_to_requirements(ctx)  # re-link all answers (deduplicated by reference)
        run.changed_requirements = sorted(changed)
        run.inputs = {"changes": [c.id for c in inputs["changes"]], "questions": [q.id for q in inputs["questions"]],
                      "interviews": [i.id for i in inputs["interviews"]]}
        run.affected_agents = [{"agent": NAMES[k], "reasons": v} for k, v in direct.items()]
        _log(ctx, "Dependency Analysis", "completed", "rules", started,
             f"{len(inputs['changes'])} approved change(s), {len(inputs['questions'])} new answer(s), "
             f"{len(inputs['interviews'])} accepted interview(s); changed requirements: "
             f"{', '.join(sorted(changed)) or 'none'}; directly affected agents: "
             f"{', '.join(NAMES[k] for k in direct) or 'none'}. " + "; ".join(notes))
        ctx["only_requirement_ids"] = set(changed)  # per-requirement agents only redo changed requirements
        ctx["retained_agents"] = set()
        dirty: set[str] = set()
        rerun, retained = [], []
        for order, (key, name, reads) in enumerate(AGENTS, start=1):
            new.current_stage, new.progress = f"{name}", int(100 * order / len(AGENTS))
            db.commit()
            reasons = list(direct.get(key, []))
            if reads is None:
                reasons = ["always re-run after a regeneration"]
            elif reads & dirty:
                reasons.append("upstream output changed: " + ", ".join(ARTEFACT_LABELS[d] for d in sorted(reads & dirty)))
            if key == "extraction" or not reasons:
                ctx["retained_agents"].add(name)
                retained.append(NAMES[key])
                _record(ctx, order, name, "skipped", "rules", 0, f"Not re-run: output retained from version "
                        f"{source.version} ({OUTPUTS[key]}). " + ("The requirement set changes only through approved "
                        "changes." if key == "extraction" else "Not affected by the applied inputs."), [])
                continue
            if key == "summary":
                from app.pipeline import compute_metrics
                ctx["metrics"] = compute_metrics(ctx, time.perf_counter() - started)
            _remove_previous_outputs(ctx, key)
            ctx["llm_log"], ctx["retrieval_log"] = [], []  # a re-run agent retrieves FRESH knowledge
            t0 = time.perf_counter()
            try:
                status, message = RUNNERS[key](ctx)
            except Exception as exc:  # recorded honestly; previous output is NOT presented as new
                traceback.print_exc()
                status, message = "failed", f"{type(exc).__name__}: {exc}"
            _record(ctx, order, name, status, "llm+rules" if llm.enabled() else "rules",
                    int((time.perf_counter() - t0) * 1000), f"Re-run because: {'; '.join(reasons)}. {message}",
                    ctx["llm_log"], ctx["retrieval_log"])
            rerun.append(name)
            if status == "failed" and key in ("sdlc", "workflow"):
                ctx["fatal"] = f"{name} failed during regeneration: {message}"
            for artefact in PRODUCES.get(key, set()):
                if fingerprint(ctx, artefact) != before.get(artefact):
                    dirty.add(artefact)
        from app.pipeline import compute_metrics, save_results
        ctx["metrics"] = compute_metrics(ctx, time.perf_counter() - started)
        save_results(db, new, ctx)
        for req in ctx["all_requirements"]:  # obsolete requirements are kept (not analysed) for the record
            if req["status"] == "obsolete":
                db.add(Requirement(analysis_id=new.id, **{k: v for k, v in req.items() if k in _REQ_FIELDS}))
        _carry_decisions(db, new, previous_sdlc, previous_workflow, previous_risks, retained)
        now = datetime.now(timezone.utc)
        for c in inputs["changes"]:
            c.applied_in_version = new.version
        for q in inputs["questions"]:
            q.applied_in_version = new.version
        for i in inputs["interviews"]:
            i.applied_in_version = new.version
        run.agents_rerun, run.outputs_retained = rerun, retained
        run.outputs_updated = sorted(ARTEFACT_LABELS[a] for a in dirty) + ["validation report", "executive summary"]
        run.status = "failed" if new.status == "failed" else "completed"
        run.error = new.error
        run.finished_at = now
        source.stage2_status = "regenerated" if run.status == "completed" else "failed"
        new.baseline_status = "draft"  # a changed requirement set needs a new baseline approval
        db.commit()
        audit(db, "regeneration.completed", entity_type="analysis", entity_id=new.id,
              details={"from_version": source.version, "to_version": new.version, "agents_rerun": rerun,
                       "retained": retained, "status": run.status})
    except Exception as exc:
        traceback.print_exc()
        db.rollback()
        run = db.get(RegenerationRun, run_id)
        if run:
            run.status, run.error, run.finished_at = "failed", f"{type(exc).__name__}: {exc}", datetime.now(timezone.utc)
            new = db.get(Analysis, run.new_analysis_id)
            if new:
                new.status, new.error, new.finished_at = "failed", run.error, run.finished_at
            source = db.get(Analysis, run.source_analysis_id)
            if source:
                source.stage2_status = "failed"
            db.commit()
    finally:
        db.close()


_REQ_FIELDS = {"req_id", "statement", "original_text", "source", "kind", "categories", "priority", "risk_level",
               "confidence", "dependencies", "user_story", "acceptance_criteria", "test_recommendations",
               "generated_by", "status", "supplementary", "citations"}


def _log(ctx: dict, agent: str, status: str, mode: str, started: float, message: str) -> None:
    _record(ctx, 0, agent, status, mode, int((time.perf_counter() - started) * 1000), message, [])


def _record(ctx: dict, order: int, agent: str, status: str, mode: str, ms: int, message: str, calls: list,
            retrieval: list | None = None) -> None:
    ctx["agent_runs"].append({"order": order, "agent": agent, "status": status, "mode": mode, "duration_ms": ms,
                              "message": message, "llm_calls": calls, "stage": "stage2",
                              "retrieval": retrieval or []})


def _carry_decisions(db: Session, new: Analysis, previous_sdlc, previous_workflow, previous_risks: list[dict],
                     retained: list[str]) -> None:
    """Human decisions on outputs that did NOT change carry over; changed outputs need a new approval."""
    sdlc = db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == new.id))
    if sdlc and previous_sdlc and NAMES["sdlc"] in retained:
        sdlc.status, sdlc.approved_model = previous_sdlc.get("status", "pending_approval"), previous_sdlc.get(
            "approved_model", "")
    wf = db.scalar(select(Workflow).where(Workflow.analysis_id == new.id))
    if wf and previous_workflow and NAMES["workflow"] in retained:
        wf.status = previous_workflow.get("status", "draft")
    decided = {(r["description"], r["score"]): r.get("status", "open") for r in previous_risks}
    for r in db.scalars(select(Risk).where(Risk.analysis_id == new.id)).all():
        r.status = decided.get((r.description, r.score), "open")  # identical risk -> same human decision
    db.flush()


# --------------------------------------------------------------------------
# 4. Compare two versions
# --------------------------------------------------------------------------
def compare(db: Session, base: Analysis, target: Analysis) -> dict:
    def reqs(a):
        return {r.req_id: r for r in db.scalars(select(Requirement).where(Requirement.analysis_id == a.id)).all()}

    def sdlc(a):
        return db.scalar(select(SdlcRecommendation).where(SdlcRecommendation.analysis_id == a.id))

    def workflow_of(a):
        return db.scalar(select(Workflow).where(Workflow.analysis_id == a.id))

    rb, rt = reqs(base), reqs(target)
    changed = []
    for k in sorted(rb.keys() & rt.keys()):
        b, t = rb[k], rt[k]
        added = [s for s in (t.supplementary or []) if s not in (b.supplementary or [])]
        what = [name for name, differs in (("statement", b.statement != t.statement), ("status", b.status != t.status),
                                           ("supplementary information", bool(added))) if differs]
        if what:
            changed.append({"req_id": k, "before": b.statement, "after": t.statement, "status": t.status,
                            "changes": what, "supplementary_added": [f"{s['ref']}: {s['text']}" for s in added]})
    recat = [{"req_id": k, "before": rb[k].categories, "after": rt[k].categories}
             for k in sorted(rb.keys() & rt.keys()) if rb[k].categories != rt[k].categories]
    sb, st = sdlc(base), sdlc(target)
    wb, wt = workflow_of(base), workflow_of(target)
    mb, mt = base.metrics or {}, target.metrics or {}
    keys = ["extracted", "quality_issues", "issues_open", "issues_resolved", "conflicts", "ambiguities",
            "compliance_mappings", "compliance_gaps", "security_gaps", "risks", "high_risks", "sdlc_score",
            "traceability_coverage", "clarification_answered"]
    return {
        "base": {"id": base.id, "version": base.version, "status": base.status},
        "target": {"id": target.id, "version": target.version, "status": target.status},
        "requirements": {"added": [{"req_id": k, "statement": rt[k].statement} for k in sorted(rt.keys() - rb.keys())],
                         "removed": sorted(rb.keys() - rt.keys()), "changed": changed, "recategorised": recat},
        "metrics": [{"metric": k, "before": mb.get(k), "after": mt.get(k)} for k in keys if mb.get(k) != mt.get(k)],
        "sdlc": {"before": {"model": sb.recommended_model, "score": sb.ranking[0]["score"]} if sb else None,
                 "after": {"model": st.recommended_model, "score": st.ranking[0]["score"]} if st else None,
                 "ranking_before": [(r["model"], r["score"]) for r in sb.ranking] if sb else [],
                 "ranking_after": [(r["model"], r["score"]) for r in st.ranking] if st else [],
                 "factors_changed": [{"factor": a["name"], "before": a["value"], "after": b["value"]}
                                     for a, b in zip(sb.factors, st.factors) if a["value"] != b["value"]]
                 if sb and st else []},
        "workflow": {"before": {"model": wb.sdlc_model, "phases": len(wb.phases)} if wb else None,
                     "after": {"model": wt.sdlc_model, "phases": len(wt.phases)} if wt else None,
                     "customisations_added": sorted({c["item"] for c in (wt.customisations if wt else [])}
                                                    - {c["item"] for c in (wb.customisations if wb else [])}),
                     "customisations_removed": sorted({c["item"] for c in (wb.customisations if wb else [])}
                                                      - {c["item"] for c in (wt.customisations if wt else [])})},
        "validation": {"before": {c["check"]: c["status"] for c in base.validation_report or []},
                       "after": {c["check"]: c["status"] for c in target.validation_report or []}},
    }


def run_logs(db: Session, analysis_id: int, stage: str | None = None) -> list[dict]:
    query = select(AgentRun).where(AgentRun.analysis_id == analysis_id)
    if stage:
        query = query.where(AgentRun.stage == stage)
    return [row_dict(r) for r in db.scalars(query.order_by(AgentRun.id)).all()]


__all__ = ["plan", "start", "execute", "compare", "family", "latest_version", "pending_inputs", "run_logs"]
