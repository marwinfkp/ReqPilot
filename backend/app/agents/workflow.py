"""Agent 8 - SDLC Workflow Generation Agent.

How a project-specific workflow is built:
1. Template: the selected SDLC model defines its ordered phases (MODEL_PHASES).
   Every phase belongs to a generic stage (plan, design, build, verify, accept,
   release, operate) which supplies default activities, roles, deliverables,
   entry/exit criteria and human approval gates (STAGE_DEFAULTS).
2. Customisation rules (CUSTOMISATIONS) add items driven by THIS project's
   analysis: its domain, requirement categories, conflicts, compliance gaps,
   security gaps and risks - e.g. transaction-integrity testing for payments,
   DPIA when personal data is processed, DR drills for availability targets.
3. Traceability checkpoints list the actual requirement IDs per phase.
4. OpenAI mode: the model tailors phase descriptions and may add up to three
   project-specific activities per phase. It cannot remove approval gates.
"""
from pydantic import BaseModel

from app import llm
from app.agents import valid_ids
from app.rules import find_keywords
from app.security import mask_sensitive, wrap_untrusted

STAGE_DEFAULTS = {
    "plan": {
        "activities": ["Consolidate stakeholder requirements and resolve open issues",
                       "Prioritise requirements and define acceptance criteria",
                       "Confirm applicable regulations and organisational policies"],
        "roles": ["Business Analyst", "Product Owner", "Compliance Officer", "Project Manager"],
        "deliverables": ["Software Requirements Specification", "Requirements Traceability Matrix (baseline)",
                         "Compliance mapping report"],
        "security": ["Identify security and privacy requirements and classify data"],
        "testing": ["Define acceptance criteria for every high-priority requirement"],
        "compliance": ["Compliance officer reviews requirement-to-control mappings"],
        "entry": ["Project scope, domain and stakeholders identified"],
        "exit": ["Requirement baseline approved", "Regulatory interpretations approved by the compliance officer"],
        "gates": [("Requirement baseline approval", ["Project Manager", "Business Analyst"]),
                  ("Regulatory interpretation approval", ["Compliance Officer"])],
    },
    "design": {
        "activities": ["Define solution architecture and integration design",
                       "Design data model, retention and audit logging"],
        "roles": ["Solution Architect", "Security Architect", "Lead Developer"],
        "deliverables": ["Architecture and design document", "Threat model", "Interface specifications"],
        "security": ["Threat modelling of critical data and transaction flows", "Security architecture review"],
        "testing": ["Derive the test strategy and test plan from the requirements"],
        "compliance": ["Verify the design addresses data localisation and retention obligations"],
        "entry": ["Requirement baseline approved"],
        "exit": ["Architecture and threat model reviewed",
                 "Every high-priority requirement allocated to a design element"],
        "gates": [("Architecture and security design sign-off", ["Solution Architect", "Security Officer"])],
    },
    "build": {
        "activities": ["Implement features against the approved design / backlog", "Peer code review"],
        "roles": ["Developers", "Tech Lead", "DevOps Engineer"],
        "deliverables": ["Source code", "Unit test suite", "Build artefacts"],
        "security": ["Secure coding standard", "Static application security testing (SAST) and dependency scanning"],
        "testing": ["Unit testing with agreed coverage targets"],
        "compliance": ["Commits and user stories reference requirement IDs"],
        "entry": ["Design or sprint backlog approved"],
        "exit": ["Code reviewed and unit tests passing", "No open critical SAST findings"],
        "gates": [],
    },
    "verify": {
        "activities": ["Integrate components and external interfaces", "Execute system and regression tests"],
        "roles": ["QA Lead", "Test Engineers", "Security Tester"],
        "deliverables": ["Test reports", "Defect log", "Security test report"],
        "security": ["Dynamic application security testing (DAST)"],
        "testing": ["Integration testing", "System testing against functional and non-functional requirements"],
        "compliance": ["Trace every test result to requirement IDs"],
        "entry": ["Build complete and deployed to the test environment"],
        "exit": ["All high-severity defects closed", "Requirement test coverage meets the agreed target"],
        "gates": [("Test completion review", ["QA Lead"])],
    },
    "accept": {
        "activities": ["User acceptance testing with business stakeholders",
                       "Compliance verification of implemented controls"],
        "roles": ["Business Users", "Product Owner", "Compliance Officer", "Security Officer"],
        "deliverables": ["UAT sign-off", "Compliance verification checklist", "Security sign-off"],
        "security": ["Security sign-off on residual risks"],
        "testing": ["User acceptance testing"],
        "compliance": ["Compliance officer verifies that mapped controls are implemented"],
        "entry": ["System testing complete"],
        "exit": ["UAT, security and compliance sign-offs obtained"],
        "gates": [("UAT sign-off", ["Product Owner"]), ("Security sign-off", ["Security Officer"]),
                  ("Compliance sign-off", ["Compliance Officer"])],
    },
    "release": {
        "activities": ["Change approval and release planning", "Controlled deployment with a rollback plan"],
        "roles": ["Release Manager", "DevOps Engineer", "Project Manager"],
        "deliverables": ["Release notes", "Deployment runbook", "Rollback plan"],
        "security": ["Production configuration hardening review"],
        "testing": ["Smoke tests in production"],
        "compliance": ["Change record with approvals retained for audit"],
        "entry": ["All acceptance sign-offs obtained"],
        "exit": ["Deployment verified", "Post-deployment checks passed"],
        "gates": [("Production-readiness approval", ["Project Manager", "Change Advisory Board"])],
    },
    "operate": {
        "activities": ["Monitor availability, performance and errors", "Incident and problem management",
                       "Collect feedback for the next release"],
        "roles": ["Operations Team", "Security Operations (SOC)", "Product Owner"],
        "deliverables": ["Operational dashboards", "Incident reports", "Periodic compliance reports"],
        "security": ["Continuous security monitoring (SIEM) and vulnerability management"],
        "testing": ["Periodic regression and disaster-recovery testing"],
        "compliance": ["Periodic access reviews and audit-log reviews"],
        "entry": ["System live in production"],
        "exit": ["Next release planned or service retired"],
        "gates": [],
    },
}

# (phase name, stage, description, extra activities, verifies)
MODEL_PHASES = {
    "Waterfall": [
        ("Requirements & Regulatory Analysis", "plan", "Capture, analyse and baseline all requirements before design starts.", [], ""),
        ("System & Security Design", "design", "Design the complete architecture and security controls up front.", [], ""),
        ("Implementation", "build", "Build all modules against the approved design.", [], ""),
        ("Integration & System Testing", "verify", "Integrate all modules and test the complete system against the SRS.", [], ""),
        ("User Acceptance & Compliance Verification", "accept", "Business users and control owners accept the system.", [], ""),
        ("Deployment", "release", "Single planned release to production.", [], ""),
        ("Operations & Maintenance", "operate", "Run, support and maintain the system.", [], ""),
    ],
    "V-Model": [
        ("Requirements & Regulatory Analysis", "plan", "Baseline requirements; acceptance tests are designed from them.",
         ["Design acceptance test cases for every requirement"], ""),
        ("System Design", "design", "Specify the system's behaviour and interfaces; system tests are derived from it.",
         ["Derive system test cases from the system design"], ""),
        ("Architecture & Module Design", "design", "Detailed component design; integration and unit tests derived.",
         ["Derive integration and unit test specifications"], ""),
        ("Secure Implementation", "build", "Code the modules against the detailed design.", [], ""),
        ("Unit & Integration Testing", "verify", "Verifies the architecture and module design.", [],
         "Architecture & Module Design"),
        ("System & Security Testing", "verify", "Verifies the system design end to end, including security.", [],
         "System Design"),
        ("Acceptance & Compliance Verification", "accept", "Validates the requirements with users and control owners.",
         [], "Requirements & Regulatory Analysis"),
        ("Controlled Deployment", "release", "Formally approved release to production.", [], ""),
        ("Operations & Monitoring", "operate", "Operate and monitor the verified system.", [], ""),
    ],
    "Agile": [
        ("Inception & Product Backlog", "plan", "Shape the product vision and an initial prioritised backlog.",
         ["Write user stories with acceptance criteria"], ""),
        ("Release Planning & Architecture Runway", "design", "Plan releases and just-enough architecture.", [], ""),
        ("Iterative Sprints", "build", "Time-boxed sprints delivering potentially shippable increments.",
         ["Sprint planning, daily stand-ups, sprint review and retrospective"], ""),
        ("Continuous Testing", "verify", "Automated tests run in every sprint.", [], ""),
        ("Sprint Review & Acceptance", "accept", "Product Owner accepts increments against the Definition of Done.",
         [], ""),
        ("Release", "release", "Release accepted increments.", [], ""),
        ("Operate & Learn", "operate", "Run the product and feed learning into the backlog.", [], ""),
    ],
    "DevSecOps": [
        ("Plan", "plan", "Plan work items with security and compliance requirements built in.", [], ""),
        ("Secure Design & Threat Modelling", "design", "Design changes with security as a first-class concern.", [], ""),
        ("Code & Build (CI)", "build", "Every commit is built and scanned automatically.",
         ["Continuous integration pipeline with automated quality gates"], ""),
        ("Automated Security & Quality Testing", "verify", "SAST, DAST, SCA and automated tests on every change.",
         ["Policy-as-code checks in the pipeline"], ""),
        ("Release Approval", "accept", "Pipeline evidence reviewed before production promotion.", [], ""),
        ("Continuous Deployment", "release", "Automated, low-risk deployments (blue-green / canary).", [], ""),
        ("Operate & Monitor", "operate", "Continuous monitoring, alerting and feedback.", [], ""),
    ],
    "Spiral": [
        ("Determine Objectives & Constraints", "plan", "Each cycle starts by fixing objectives, alternatives and constraints.", [], ""),
        ("Risk Analysis & Prototyping", "design", "Identify and resolve the biggest risks, using prototypes.",
         ["Build prototypes for the highest-risk components", "Update the risk register for this cycle"], ""),
        ("Development & Verification", "build", "Develop and verify this cycle's increment.", [], ""),
        ("Cycle Evaluation & Testing", "verify", "Test the increment and evaluate the cycle's results.", [], ""),
        ("Stakeholder Review & Next-Cycle Planning", "accept", "Stakeholders review and approve the next cycle.",
         ["Go / no-go decision for the next spiral cycle"], ""),
        ("Release", "release", "Release once the final cycle is accepted.", [], ""),
        ("Operations", "operate", "Operate and monitor the system.", [], ""),
    ],
    "Hybrid (Agile + V-Model)": [
        ("Regulatory Requirements Baseline", "plan", "Baseline regulated requirements formally (V-Model) while "
         "keeping a prioritised backlog for evolving features (Agile).", ["Split backlog into regulated and evolving items"], ""),
        ("Architecture & Threat Modelling", "design", "Formal architecture and threat model for regulated components.", [], ""),
        ("Iterative Secure Sprints", "build", "Agile sprints deliver features; each increment keeps its V-Model test levels.",
         ["Sprint planning, review and retrospective"], ""),
        ("Integration & System Verification", "verify", "Formal integration and system verification of each release candidate.",
         [], "Architecture & Threat Modelling"),
        ("Compliance Verification & UAT", "accept", "Validation against the baselined requirements and controls.",
         [], "Regulatory Requirements Baseline"),
        ("Controlled Release", "release", "Gated release to production.", [], ""),
        ("Monitoring & Continuous Compliance", "operate", "Operate, monitor and keep compliance evidence current.", [], ""),
    ],
    "Hybrid (Agile + DevSecOps)": [
        ("Inception & Compliance Baseline", "plan", "Shape the backlog and baseline the non-negotiable regulatory controls.", [], ""),
        ("Secure Backlog & Threat Modelling", "design", "Security stories and threat models for each epic.", [], ""),
        ("Sprints with Secure CI Pipeline", "build", "Agile sprints; every commit passes the automated security pipeline.",
         ["Continuous integration with automated quality and security gates"], ""),
        ("Continuous Security Verification", "verify", "Automated SAST/DAST/SCA plus periodic manual security testing.",
         ["Policy-as-code compliance checks"], ""),
        ("Release Readiness & Sign-off", "accept", "Human sign-off on pipeline evidence before production.", [], ""),
        ("Continuous Deployment", "release", "Automated, progressive deployment with rollback.", [], ""),
        ("Continuous Monitoring & Feedback", "operate", "Production monitoring feeding the next sprints.", [], ""),
    ],
}


def _ids(ctx: dict, *categories: str, limit: int = 8) -> list[str]:
    return [r["req_id"] for r in ctx["requirements"] if set(categories) & set(r["categories"])][:limit]


def _has_words(ctx: dict, words: list[str]) -> bool:
    return any(find_keywords(r["statement"], words) for r in ctx["requirements"])


def _findings(ctx: dict, *types: str) -> list[dict]:
    return [f for f in ctx["findings"] if f["issue_type"] in types or f["kind"] in types]


def customisations(ctx: dict) -> list[tuple[str, str, str, str]]:
    """Return (reason, stage, field, item) additions for this specific project."""
    domain, items = ctx["project"]["domain"], []
    add = lambda reason, stage, field, item: items.append((reason, stage, field, item))  # noqa: E731
    sec, priv, perf = _ids(ctx, "security"), _ids(ctx, "privacy"), _ids(ctx, "performance")
    integ, avail = _ids(ctx, "integration"), _ids(ctx, "availability")
    if sec:
        add("Security requirements present", "design", "security",
            f"STRIDE threat modelling of authentication, transaction and data flows ({', '.join(sec[:5])})")
        add("Security requirements present", "verify", "security", "Independent penetration test before release")
    if priv:
        add("Personal data processed", "plan", "compliance", "Data protection impact assessment (DPIA)")
        add("Personal data processed", "verify", "testing",
            f"Verify consent capture, masking and data-principal rights flows ({', '.join(priv[:5])})")
    if domain in ("payments", "digital_banking") or _has_words(ctx, ["transfer", "payment", "upi", "imps", "neft"]):
        add("Financial transactions", "verify", "testing",
            "Transaction integrity testing: idempotency, duplicate prevention, reversals and reconciliation")
        add("Financial transactions", "accept", "compliance", "Reconcile test transactions with settlement reports")
    if perf:
        add("Performance targets", "verify", "testing", f"Load and stress testing against stated targets ({', '.join(perf[:5])})")
    if avail:
        add("Availability targets", "release", "activities", "Low-downtime deployment strategy (blue-green or canary)")
        add("Availability targets", "operate", "activities", f"Disaster-recovery drill validating RTO/RPO ({', '.join(avail[:4])})")
    if integ:
        add("External integrations", "design", "activities", "Agree interface contracts with external system owners")
        add("External integrations", "verify", "testing", f"Contract and end-to-end tests for interfaces ({', '.join(integ[:5])})")
    if _has_words(ctx, ["legacy", "core banking", "cbs", "mainframe", "migrat"]):
        add("Legacy dependence", "design", "activities", "Legacy core-banking adapter design and data-migration plan")
        add("Legacy dependence", "verify", "testing", "Migration dry-runs and data reconciliation with the legacy system")
    if _ids(ctx, "auditability"):
        add("Auditability requirements", "accept", "compliance",
            "Verify the tamper-evident audit trail covers all transactions and administrative actions")
    if domain == "lending" or _has_words(ctx, ["loan", "credit score", "emi"]):
        add("Lending domain", "verify", "testing", "Credit-decision rule/model validation incl. bias and fairness testing")
        add("Lending domain", "accept", "compliance", "Verify loan disclosures (e.g. key fact statement) and fees")
    if domain == "fraud_detection" or _has_words(ctx, ["machine learning", "ml model", "model", "anomal"]):
        add("Analytical / ML models", "design", "activities", "Model development plan: features, data governance, explainability")
        add("Analytical / ML models", "verify", "testing", "Model validation: precision, recall, false-positive rate, back-testing")
        add("Analytical / ML models", "operate", "activities", "Model drift monitoring and approved periodic retraining")
    if _has_words(ctx, ["kyc", "aadhaar", "onboard"]):
        add("Customer onboarding / KYC", "verify", "testing", "KYC flow testing: e-KYC, video KYC, rejection and re-KYC paths")
    if any(f["id"] == "regulatory_criticality" and f["value"] >= 4 for f in ctx.get("sdlc_factors", [])):
        add("High regulatory criticality", "release", "gates", "Regulatory readiness confirmation|Compliance Officer")
    conflicts = _findings(ctx, "conflict", "stakeholder_conflict")
    if conflicts:
        ids = sorted({i for f in conflicts for i in f["requirement_ids"]})
        add("Unresolved conflicts", "plan", "activities",
            f"Resolve {len(conflicts)} requirement conflict(s) with stakeholders before baselining ({', '.join(ids[:6])})")
    ambiguous = _findings(ctx, "ambiguity")
    if ambiguous:
        add("Ambiguous requirements", "plan", "activities",
            f"Clarify {len(ambiguous)} ambiguous requirement(s) and agree measurable acceptance criteria")
    gaps = [f for f in ctx["findings"] if f["kind"] == "compliance_gap"]
    if gaps:
        add("Compliance gaps", "plan", "compliance",
            "Close compliance gaps: " + "; ".join(f["title"].split(": ", 1)[-1] for f in gaps[:5]))
    security_gaps = [f for f in ctx["findings"] if f["kind"] == "security_gap"]
    if security_gaps:
        add("Security gaps", "design", "security",
            "Define missing security controls: " + "; ".join(f["title"].split(": ", 1)[-1] for f in security_gaps[:5]))
    high_risks = [r for r in ctx.get("risks", []) if r["severity"] in ("High", "Critical")]
    if high_risks:
        add("High risks", "plan", "activities",
            f"Risk response planning for {len(high_risks)} high/critical risk(s) ({', '.join(r['risk_id'] for r in high_risks[:5])})")
    return items


def _traceability(stage: str, ctx: dict) -> tuple[list[str], list[str]]:
    reqs = ctx["requirements"]
    high = [r["req_id"] for r in reqs if r["priority"] == "High"]
    checkpoints = {
        "plan": [f"Baseline RTM: {len(reqs)} requirements ({len(high)} high priority) linked to their sources"],
        "design": ["Every high-priority requirement allocated to a design component"],
        "build": ["Commits and user stories reference requirement IDs"],
        "verify": [f"Test cases exist for all {len(high)} high-priority requirements"],
        "accept": ["100% of high-priority requirements verified and accepted"],
        "release": ["RTM frozen for the release; deviations formally approved"],
        "operate": ["Changes to approved requirements go through change control and update the RTM"],
    }[stage]
    key = {"plan": high, "design": _ids(ctx, "security", "integration", "regulatory", limit=10),
           "verify": high, "accept": _ids(ctx, "regulatory", "security", "privacy", limit=10)}.get(stage, [])
    return checkpoints, key[:10]


def build_workflow(ctx: dict, model: str) -> tuple[list[dict], list[dict]]:
    phases = []
    for order, (name, stage, description, extra, verifies) in enumerate(MODEL_PHASES[model], start=1):
        d = STAGE_DEFAULTS[stage]
        checkpoints, key_reqs = _traceability(stage, ctx)
        phases.append({
            "order": order, "name": name, "stage": stage, "description": description,
            "activities": d["activities"] + extra, "roles": list(d["roles"]), "deliverables": list(d["deliverables"]),
            "security": list(d["security"]), "testing": list(d["testing"]), "compliance": list(d["compliance"]),
            "entry_criteria": list(d["entry"]), "exit_criteria": list(d["exit"]),
            "approval_gates": [{"name": g, "approvers": a} for g, a in d["gates"]],
            "traceability": checkpoints, "key_requirements": key_reqs, "verifies": verifies,
            "customised": [], "generated_by": "rules"})
    applied = []
    for reason, stage, field, item in customisations(ctx):
        targets = [p for p in phases if p["stage"] == stage]
        if not targets:
            continue
        phase = targets[-1] if stage == "verify" else targets[0]  # system-level testing goes to the last verify phase
        if field == "gates":
            gate, approver = item.split("|")
            phase["approval_gates"].append({"name": gate, "approvers": [approver]})
        else:
            phase[field].append(item)
        phase["customised"].append(reason)
        applied.append({"reason": reason, "phase": phase["name"], "field": field, "item": item.split("|")[0]})
    return phases, applied


class PhaseTailoring(BaseModel):
    phase_name: str
    project_specific_description: str
    additional_activities: list[str]
    requirement_ids: list[str]


class WorkflowTailoring(BaseModel):
    phases: list[PhaseTailoring]


_INSTRUCTIONS = """Tailor each phase of this SDLC workflow to the specific financial application. For every \
phase write a project-specific description (2-3 sentences) and at most 3 additional concrete activities \
that are not already listed, citing related requirement IDs. Do not remove or weaken approval gates."""


def run(ctx: dict) -> tuple[str, str]:
    model = ctx["sdlc"]["recommended_model"]
    phases, applied = build_workflow(ctx, model)
    status, note, generated_by = "completed", "", "rules"
    if llm.enabled():
        outline = "\n".join(f"- {p['name']} ({p['stage']}): {p['description']} Activities: {'; '.join(p['activities'])}"
                            for p in phases)
        reqs = "\n".join(f"{r['req_id']}: {r['statement']}" for r in ctx["requirements"] if r["priority"] == "High")[:12000]
        data = (f"Project: {ctx['project']['name']} ({ctx['project']['domain']}): {ctx['project']['description']}\n"
                f"Selected SDLC: {model}\nWorkflow:\n{outline}\n\n" + wrap_untrusted("requirements", mask_sensitive(reqs)[0]))
        try:
            output = llm.structured("SDLC workflow tailoring", _INSTRUCTIONS, data, WorkflowTailoring, ctx["llm_log"])
            by_name = {p["name"]: p for p in phases}
            for t in output.phases:
                phase = by_name.get(t.phase_name)
                if not phase:
                    continue
                phase["model_description"] = phase["description"]
                phase["description"] = t.project_specific_description
                phase["activities"] += [a for a in t.additional_activities[:3] if a not in phase["activities"]]
                phase["key_requirements"] = list(dict.fromkeys(phase["key_requirements"] +
                                                               valid_ids(ctx, "workflow", t.requirement_ids)))[:12]
                phase["generated_by"] = llm.model_label()
            generated_by = f"rules + {llm.model_label()}"
            note = " Phase descriptions tailored by OpenAI."
        except llm.LLMError as exc:
            status, note = "partial", f" OpenAI tailoring failed ({exc}); rule-based workflow used."
    ctx["workflow"] = {"sdlc_model": model, "phases": phases, "customisations": applied, "generated_by": generated_by}
    gates = sum(len(p["approval_gates"]) for p in phases)
    return status, (f"Generated a {len(phases)}-phase {model} workflow with {gates} approval gates and "
                    f"{len(applied)} project-specific customisations.{note}")
