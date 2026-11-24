"""Agent 6 - Risk Analysis Agent.

Risk scoring mechanism (classic 5x5 risk matrix):
    likelihood 1-5  x  impact 1-5  =  score 1-25
    severity:  1-4 Low | 5-9 Medium | 10-15 High | 16-25 Critical
The score is always computed by this code; OpenAI (when enabled) only proposes
additional project-specific risks with likelihood/impact ratings.

Rule-derived risks come from the other agents' results (security gaps,
compliance gaps, conflicts, ambiguity, legacy integration, demanding
performance targets, schedule pressure, ...).
Also assigns each requirement a risk level (High / Medium / Low).
"""
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agents import valid_ids
from app.rules import find_keywords
from app.security import mask_sensitive, wrap_untrusted

CATEGORIES =["business", "technical", "security", "regulatory", "operational", "integration", "delivery"]


def severity_for(score: int) -> str:
    if score >= 16:
        return "Critical"
    if score >= 10:
        return "High"
    if score >= 5:
        return "Medium"
    return "Low"


def make_risk(description: str, category: str, likelihood: int, impact: int, mitigation: str,
              requirement_ids: list[str], generated_by: str = "rules") -> dict:
    likelihood, impact = max(1, min(5, likelihood)), max(1, min(5, impact))
    score = likelihood * impact
    return {"description": description, "category": category, "likelihood": likelihood, "impact": impact,
            "score": score, "severity": severity_for(score), "mitigation": mitigation,
            "requirement_ids": requirement_ids, "generated_by": generated_by}


def rule_risks(ctx: dict) -> list[dict]:
    reqs, findings, project = ctx["requirements"], ctx["findings"], ctx["project"]
    high_impact = 5 if project["domain"] in ("payments", "digital_banking", "lending") else 4
    of = lambda *types: [f for f in findings if f["issue_type"] in types or f["kind"] in types]  # noqa: E731
    ids_of = lambda fs: sorted({i for f in fs for i in f["requirement_ids"]})[:15]  # noqa: E731
    risks = []

    security_gaps = [f for f in findings if f["kind"] == "security_gap"]
    if security_gaps:
        labels = ", ".join(f["title"].split(": ", 1)[-1] for f in security_gaps[:5])
        risks.append(make_risk(f"Security controls are not specified for: {labels}", "security",
                               4 if len(security_gaps) >= 2 else 3, high_impact,
                               "Add explicit security requirements and verify them in threat modelling and "
                               "security testing.", ids_of(security_gaps)))
    compliance_gaps = [f for f in findings if f["kind"] == "compliance_gap"]
    if compliance_gaps:
        labels = ", ".join(f["title"].split(": ", 1)[-1] for f in compliance_gaps[:4])
        risks.append(make_risk(f"Regulatory obligations may be missed: {labels}", "regulatory",
                               4 if len(compliance_gaps) >= 3 else 3, 5,
                               "Compliance officer to confirm applicable obligations before the requirement "
                               "baseline; add missing requirements.", ids_of(compliance_gaps)))
    conflicts = of("conflict", "stakeholder_conflict")
    if conflicts:
        risks.append(make_risk(f"{len(conflicts)} unresolved conflicting requirement(s) may cause rework",
                               "delivery", 4 if len(conflicts) >= 3 else 3, 3,
                               "Resolve conflicts with the stakeholders before baselining requirements.",
                               ids_of(conflicts)))
    ambiguous = of("ambiguity")
    if reqs and len(ambiguous) / len(reqs) > 0.1:
        risks.append(make_risk(f"Scope misinterpretation: {len(ambiguous)} requirements use vague wording",
                               "business", 4 if len(ambiguous) / len(reqs) > 0.2 else 3, 3,
                               "Clarify vague requirements and agree measurable acceptance criteria.",
                               ids_of(ambiguous)))
    integration = [r["req_id"] for r in reqs if "integration" in r["categories"]]
    if integration:
        legacy = any(find_keywords(r["statement"], ["legacy", "core banking", "cbs", "mainframe", "batch"])
                     for r in reqs)
        risks.append(make_risk("Dependency on external/legacy systems (interfaces, availability, data quality)",
                               "integration", 4 if legacy else 3, 4,
                               "Agree interface contracts early, build simulators and run end-to-end "
                               "integration tests.", integration[:15]))
    demanding = [r["req_id"] for r in reqs
                 if re.search(r"99\.9|\b\d{2,3},?\d{3}\s*(concurrent|users|tps|transactions)", r["statement"], re.I)]
    if demanding:
        risks.append(make_risk("Demanding performance/availability targets may not be met", "technical", 3, 4,
                               "Capacity planning, early load testing and an architecture review.", demanding))
    infeasible = of("infeasibility")
    if infeasible:
        risks.append(make_risk("Some targets are infeasible as written", "technical", 4, 3,
                               "Negotiate realistic, measurable targets.", ids_of(infeasible)))
    schedule = f"{project.get('schedule', '')} {project.get('budget', '')}".lower()
    months = [int(m) for m in re.findall(r"(\d+)\s*month", schedule)]
    if (months and min(months) <= 6) or "fixed" in schedule or "tight" in schedule:
        risks.append(make_risk("Tight schedule/budget constraints increase delivery risk", "delivery", 4, 3,
                               "Prioritise must-have requirements and plan incremental releases.", []))
    if not any("operational" in r["categories"] for r in reqs):
        risks.append(make_risk("No operational monitoring or incident-handling requirements", "operational", 3, 3,
                               "Add monitoring, alerting and incident-response requirements.", []))
    model_reqs = [r["req_id"] for r in reqs if find_keywords(r["statement"], ["model", "machine learning",
                                                                              "risk scor", "anomal"])]
    if model_reqs:
        risks.append(make_risk("Analytical/ML model accuracy, bias and drift", "technical", 3, 4,
                               "Model validation, bias testing and production monitoring of model performance.",
                               model_reqs[:15]))
    return risks


def requirement_risk_level(req: dict, findings_by_req: dict[str, list[dict]]) -> str:
    related = findings_by_req.get(req["req_id"], [])
    critical = req["priority"] == "High" and {"security", "regulatory", "privacy"} & set(req["categories"])
    if critical or any(f["severity"] in ("High", "Critical") for f in related):
        return "High"
    if related or req["kind"] == "non_functional":
        return "Medium"
    return "Low"


class ProposedRisk(BaseModel):
    description: str
    category: Literal["business", "technical", "security", "regulatory", "operational", "integration", "delivery"]
    likelihood: int
    impact: int
    mitigation: str
    requirement_ids: list[str]


class RiskOutput(BaseModel):
    risks: list[ProposedRisk]


_INSTRUCTIONS = """Identify up to 10 project-specific risks for delivering this financial application \
(business, technical, security, regulatory, operational, integration, delivery) that are NOT already in \
the list of known risks. Rate likelihood and impact as integers 1 (very low) to 5 (very high); give a \
concrete mitigation and the related requirement IDs (only IDs from the input)."""


def run(ctx: dict) -> tuple[str, str]:
    risks = rule_risks(ctx)
    status, note = "completed", ""
    if llm.enabled() and ctx["requirements"]:
        listing = "\n".join(f"{r['req_id']} [{', '.join(r['categories'][:4])}]: {r['statement']}"
                            for r in ctx["requirements"][:150])
        known = "\n".join(f"- {r['description']}" for r in risks) or "- none"
        data = (f"Project: {ctx['project']['name']} ({ctx['project']['domain']}), scale {ctx['project']['scale']}, "
                f"schedule: {ctx['project'].get('schedule') or 'not given'}\nKnown risks:\n{known}\n\n"
                + wrap_untrusted("requirements", mask_sensitive(listing)[0]))
        try:
            output = llm.structured("risk analysis", _INSTRUCTIONS, data, RiskOutput, ctx["llm_log"])
            for r in output.risks:
                risks.append(make_risk(r.description, r.category, r.likelihood, r.impact, r.mitigation,
                                       valid_ids(ctx, "risk", r.requirement_ids), llm.model_label()))
            note = f" {len(output.risks)} additional risks proposed by OpenAI."
        except llm.LLMError as exc:
            status, note = "partial", f" OpenAI failed ({exc}); rule-derived risks only."

    risks.sort(key=lambda r: r["score"], reverse=True)
    for index, risk in enumerate(risks, start=1):
        risk["risk_id"] = f"RISK-{index:02d}"
    ctx["risks"] = risks

    findings_by_req: dict[str, list[dict]] = {}
    for f in ctx["findings"]:
        for rid in f["requirement_ids"]:
            findings_by_req.setdefault(rid, []).append(f)
    for req in ctx["requirements"]:
        req["risk_level"] = requirement_risk_level(req, findings_by_req)
    high = sum(1 for r in risks if r["severity"] in ("High", "Critical"))
    return status, f"Registered {len(risks)} risks ({high} high or critical).{note}"
