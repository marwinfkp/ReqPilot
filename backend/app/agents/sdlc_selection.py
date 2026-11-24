"""Agent 7 - SDLC Selection Agent (deterministic, explainable scoring engine).

HOW THE RECOMMENDATION IS CALCULATED
------------------------------------
Step 1 - Project characteristics -> 13 factors, each rated 1 (low) .. 5 (high).
         The ratings are derived by rules from the analysis results (see
         derive_factors). In OpenAI mode the model may nudge a rating by at most
         +/-1 and must give a reason; the rule value is always shown alongside.

Step 2 - Every SDLC model has an AFFINITY for every factor, from -2 to +2:
           +2 = the model is strongly favoured when the factor is HIGH
           -2 = the model is strongly favoured when the factor is LOW
            0 = the factor does not matter for this model
         Example: Agile has +2 for "expected change" and -2 for "need for formal
         verification".

Step 3 - Suitability score of a model (0-100):
           x        = (rating - 1) / 4                 -> factor value in 0..1
           fit      = x if affinity > 0 else 1 - x     -> how well the factor fits
           points   = weight x |affinity| x fit
           score    = 100 x sum(points) / sum(weight x |affinity|)
         i.e. the weighted percentage of the model's preferences that this
         project satisfies.

Step 4 - A few explicit decision rules subtract points where a model is clearly
         unsuitable (e.g. Waterfall with volatile requirements).

Step 5 - Rank all models; the highest score is recommended. A model is called
         "suitable" when its score is >= 60. Hybrids are scored exactly like the
         other models, so a hybrid is recommended only when it scores best.

Suitability scores are NOT probabilities - they measure how well a model's
preferences match this project's characteristics.
"""
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agents import finding, valid_ids
from app.agents.clarification_context import answer_for_topic, clarification_text, is_open
from app.rules import find_keywords
from app.security import mask_sensitive, wrap_untrusted

SUITABLE_THRESHOLD = 60

MODELS = ["Agile", "DevSecOps", "Spiral", "V-Model", "Waterfall",
          "Hybrid (Agile + V-Model)", "Hybrid (Agile + DevSecOps)"]

FACTORS = [
    # id, name, default weight, meaning of 1 / 5
    ("requirement_stability", "Requirement stability", 1.0, "volatile", "stable"),
    ("regulatory_criticality", "Regulatory criticality", 1.5, "lightly regulated", "highly regulated"),
    ("security_risk", "Security risk", 1.3, "low", "very high"),
    ("project_complexity", "Project complexity / uncertainty", 1.0, "simple", "complex/novel"),
    ("system_size", "System size", 0.8, "small", "very large"),
    ("legacy_dependence", "Legacy-system dependence", 0.8, "none", "heavy"),
    ("expected_change", "Expected requirement changes", 1.2, "rare", "very frequent"),
    ("continuous_delivery", "Continuous delivery need", 1.0, "infrequent releases", "continuous releases"),
    ("stakeholder_availability", "Stakeholder availability", 0.7, "rarely available", "always available"),
    ("testing_documentation", "Testing & documentation needs", 1.0, "light", "extensive"),
    ("budget_schedule_constraints", "Budget & schedule constraints", 0.7, "flexible", "fixed/tight"),
    ("formal_verification", "Need for formal verification", 1.2, "none", "mandatory"),
    ("failure_consequence", "Consequences of system failure", 1.3, "minor", "severe"),
]
FACTOR_IDS = [f[0] for f in FACTORS]
DEFAULT_WEIGHTS = {f[0]: f[2] for f in FACTORS}

# Columns follow FACTOR_IDS order:
#          stab reg sec cplx size leg chg cd stake test budg form fail
_AFFINITY_TABLE = {
    "Agile":                      [-2, -1, 0, 0, -1, -1, +2, +1, +2, -1, +1, -2, -1],
    "DevSecOps":                  [-1, +1, +2, +1, +1, -1, +1, +2, +1, +1, 0, 0, +1],
    "Spiral":                     [-1, +1, +1, +2, +1, +1, +1, -1, +1, +1, -2, 0, +2],
    "V-Model":                    [+1, +2, +1, 0, 0, +1, -1, -1, 0, +2, 0, +2, +2],
    "Waterfall":                  [+2, +1, 0, -1, 0, +1, -2, -2, -1, +2, +1, +1, 0],
    "Hybrid (Agile + V-Model)":   [-1, +2, +1, +1, +1, +1, +1, 0, +1, +2, -1, +2, +2],
    "Hybrid (Agile + DevSecOps)": [-1, +1, +2, +1, +1, -1, +2, +2, +2, 0, 0, -1, +1],
}
AFFINITY = {model: dict(zip(FACTOR_IDS, row)) for model, row in _AFFINITY_TABLE.items()}

MODEL_PROFILES = {
    "Agile": {
        "description": "Iterative, incremental delivery in short sprints with continuous stakeholder feedback.",
        "advantages": ["Absorbs changing requirements", "Early, frequent delivery of working software",
                       "Close stakeholder collaboration"],
        "disadvantages": ["Lighter documentation and fewer formal sign-offs than regulators usually expect",
                          "Needs continuously available stakeholders", "Fixed scope, budget and date are hard to combine"]},
    "DevSecOps": {
        "description": "Continuous integration and delivery with security automated into every stage "
                       "(plan, code, build, test, release, operate, monitor).",
        "advantages": ["Fast, frequent and secure releases", "Automated SAST/DAST/SCA on every change",
                       "Continuous monitoring and feedback from production"],
        "disadvantages": ["Needs mature automation and tooling", "Cultural change across development, "
                          "security and operations", "Formal regulatory sign-offs must be added explicitly"]},
    "Spiral": {
        "description": "Risk-driven iterations: each cycle sets objectives, analyses risks (often with "
                       "prototypes), develops and verifies, then plans the next cycle.",
        "advantages": ["Explicit risk analysis in every iteration", "Prototypes reduce technical uncertainty",
                       "Suits large, novel or high-risk systems"],
        "disadvantages": ["Costly and management-intensive", "Requires risk-assessment expertise",
                          "Overkill for small, well-understood projects"]},
    "V-Model": {
        "description": "Sequential development where every specification level has a matching test level "
                       "(requirements-acceptance, design-system/integration, module-unit).",
        "advantages": ["Strong verification and validation", "Full traceability from requirements to tests",
                       "Well suited to audits and regulated systems"],
        "disadvantages": ["Inflexible to requirement changes", "Working software arrives late",
                          "High documentation effort"]},
    "Waterfall": {
        "description": "Linear phases (requirements, design, build, test, deploy), each signed off before "
                       "the next starts.",
        "advantages": ["Simple to plan and manage", "Clear milestones and documentation",
                       "Works when requirements are stable and well understood"],
        "disadvantages": ["Late changes are expensive", "Testing and security issues surface late",
                          "Little user feedback until the end"]},
    "Hybrid (Agile + V-Model)": {
        "description": "Agile sprints for building features, wrapped in V-Model verification levels and "
                       "formal compliance gates.",
        "components": ["Agile: sprint-based delivery and backlog management for evolving features",
                       "V-Model: baselined requirements and designs with matching acceptance, system, "
                       "integration and unit test levels",
                       "Formal approval gates for regulatory and security sign-off"],
        "advantages": ["Adaptability combined with formal verification and validation",
                       "Audit-ready traceability while still iterating",
                       "Fits regulated systems whose requirements still evolve"],
        "disadvantages": ["More process overhead than pure Agile", "Requires disciplined baselining per increment",
                          "Needs an experienced team to balance both approaches"]},
    "Hybrid (Agile + DevSecOps)": {
        "description": "Agile product delivery combined with a DevSecOps pipeline: automated security and "
                       "compliance checks on every change and continuous deployment.",
        "components": ["Agile: sprints, backlog and continuous stakeholder feedback",
                       "DevSecOps: CI/CD pipeline with automated SAST/DAST/SCA and policy checks",
                       "Release gates for security and compliance approval"],
        "advantages": ["Rapid, secure delivery of frequently changing features", "Security built in, not bolted on",
                       "Continuous compliance evidence produced by the pipeline"],
        "disadvantages": ["Requires mature DevOps tooling and skills", "Formal documentation must be "
                          "produced deliberately", "Upfront investment in automation"]},
}

# Explicit decision rules: (id, description, condition(values), {model: penalty})
DECISION_RULES = [
    ("R1", "Volatile requirements (stability <= 2) make plan-driven models risky",
     lambda v: v["requirement_stability"] <= 2, {"Waterfall": 10, "V-Model": 5}),
    ("R2", "High regulatory criticality (>= 4) needs formal compliance gates that pure Agile lacks",
     lambda v: v["regulatory_criticality"] >= 4, {"Agile": 10}),
    ("R3", "Continuous delivery need (>= 4) conflicts with single big-bang releases",
     lambda v: v["continuous_delivery"] >= 4, {"Waterfall": 10, "V-Model": 5}),
    ("R4", "High security risk (>= 4) penalises models that test security only at the end",
     lambda v: v["security_risk"] >= 4, {"Waterfall": 5}),
]

_CHANGE_WORDS = ["may change", "subject to change", "evolving", "evolve", "phase 2", "phase two", "future phase",
                 "later phase", "frequent", "regulatory change", "pilot", "mvp", "iterat", "experiment", "a/b",
                 "new feature", "roadmap", "changing", "competitive", "market feedback", "rapid",
                 "change frequently", "changes frequently", "change often", "change periodically",
                 "change operating rules", "rules change", "absorb", "new regulation", "backlog"]
_CD_WORDS = ["continuous", "ci/cd", "cicd", "frequent release", "weekly release", "fortnightly", "every sprint",
             "rapid release", "feature toggle", "feature flag", "app store", "mobile app", "zero-downtime",
             "blue-green", "canary", "devops", "automated deployment", "release every",
             "released continuously", "deployed continuously", "pipeline", "infrastructure as code", "container"]
_LEGACY_WORDS = ["legacy", "core banking", "cbs", "mainframe", "existing system", "batch file", "nightly batch",
                 "migration", "migrate", "cobol", "finacle", "flexcube"]
_FORMAL_WORDS = ["reconcil", "ledger", "settlement", "interest calculation", "calculat", "accuracy",
                 "integrity", "certif", "sign-off", "regulatory report", "precision"]
_UNCERTAINTY_WORDS = ["machine learning", "ml model", "model", "artificial intelligence", "real-time", "real time",
                      "novel", "prototype", "proof of concept", "experiment", "uncertain", "new technology",
                      "anomal", "risk scor", "undecided", "proof-of-concept", "imbalanced", "risk review",
                      "depends on pilot", "go/no-go"]


def clamp(value: int) -> int:
    return max(1, min(5, value))


def _bucket(count: int, thresholds: list[int]) -> int:
    """Map a keyword count to a 1-5 rating using ascending thresholds."""
    return clamp(1 + sum(1 for t in thresholds if count >= t))


# --------------------------------------------------------------------------
# Step 1: derive factor ratings from the analysis
# --------------------------------------------------------------------------
def derive_factors(ctx: dict) -> list[dict]:
    project, reqs, findings = ctx["project"], ctx["requirements"], ctx["findings"]
    n = max(1, len(reqs))
    # Evidence = project details + requirements + clarification answers (kept as separate context).
    text = "\n".join([project.get("description", ""), project.get("schedule", ""), project.get("budget", "")]
                     + [r["statement"] for r in reqs] + [clarification_text(ctx)])
    count_cat = lambda c: sum(1 for r in reqs if c in r["categories"])  # noqa: E731
    # Issues fully resolved by clarification no longer lower requirement stability.
    count_issue = lambda *t: sum(1 for f in findings if f["issue_type"] in t and is_open(f))  # noqa: E731
    hits = lambda words: len(find_keywords(text, words))  # noqa: E731
    domain = project["domain"]
    factors: dict[str, tuple[int, str, bool]] = {}  # id -> (value, explanation, is_assumption)

    ambiguous, conflicts = count_issue("ambiguity"), count_issue("conflict", "stakeholder_conflict")
    change = hits(_CHANGE_WORDS)
    v = 4 - (ambiguous / n > 0.2) - (conflicts >= 2) - (change >= 2) - (count_issue("incompleteness") >= 2)
    factors["requirement_stability"] = (clamp(v), f"{ambiguous} ambiguous ({ambiguous / n:.0%}), {conflicts} "
                                        f"conflicting, {change} change indicator(s) in the text.", False)

    regulatory = count_cat("regulatory")
    base = {"regulatory_reporting": 5, "payments": 4, "digital_banking": 4, "lending": 4, "kyc_onboarding": 4,
            "insurance": 4, "investment": 4}.get(domain, 3)
    factors["regulatory_criticality"] = (clamp(base + (regulatory / n >= 0.15)),
                                         f"Domain baseline {base}/5; {regulatory} regulatory requirement(s) "
                                         f"({regulatory / n:.0%}).", False)

    security = sum(1 for r in reqs if {"security", "privacy"} & set(r["categories"]))
    v = 3 + (security / n >= 0.2) + (domain in ("payments", "digital_banking"))
    factors["security_risk"] = (clamp(v), f"{security} security/privacy requirement(s) ({security / n:.0%}); "
                                f"domain '{domain}'.", False)

    distinct = len({c for r in reqs for c in r["categories"]})
    integration, uncertain = count_cat("integration"), hits(_UNCERTAINTY_WORDS)
    v = 2 + (distinct >= 10) + (integration >= 3) + (uncertain >= 2) + (n >= 60)
    factors["project_complexity"] = (clamp(v), f"{distinct} requirement categories, {integration} integration "
                                     f"requirement(s), {uncertain} uncertainty indicator(s), {n} requirements.",
                                     False)

    scale = (project.get("scale") or "").lower()
    if scale in ("small", "medium", "large", "enterprise"):
        v = {"small": 2, "medium": 3, "large": 4, "enterprise": 5}[scale]
        factors["system_size"] = (v, f"Declared scale '{scale}'; {n} requirements submitted.", False)
    else:
        factors["system_size"] = (_bucket(n, [20, 50, 100]) + 1 if n >= 20 else 2,
                                  f"Scale not given; estimated from {n} requirements.", True)

    legacy = hits(_LEGACY_WORDS)
    factors["legacy_dependence"] = (_bucket(legacy, [1, 2, 4, 6]), f"{legacy} legacy/migration indicator(s).", False)
    factors["expected_change"] = (_bucket(change, [0, 1, 2, 4]), f"{change} change indicator(s) "
                                  "(e.g. 'phase 2', 'evolving', 'pilot').", False)
    cd = hits(_CD_WORDS)
    factors["continuous_delivery"] = (_bucket(cd, [0, 1, 2, 3]), f"{cd} continuous-delivery indicator(s) "
                                      "(e.g. 'mobile app', 'CI/CD', 'frequent releases').", False)
    cadence = answer_for_topic(ctx, "release_cadence")
    if cadence:
        value = next((v for word, v in (("fortnight", 5), ("weekly", 5), ("monthly", 4), ("quarter", 3),
                                        ("one major", 1), ("single", 1)) if word in cadence["answer"].lower()), None)
        if value:
            factors["continuous_delivery"] = (value, f"Release cadence from clarification {cadence['code']}: "
                                                     f"'{cadence['answer']}'.", False)

    availability = (project.get("stakeholder_availability") or "").lower()
    clarified = answer_for_topic(ctx, "stakeholder_availability")
    if availability in ("low", "medium", "high", "very high"):
        factors["stakeholder_availability"] = ({"low": 2, "medium": 3, "high": 4, "very high": 5}[availability],
                                               f"Declared stakeholder availability '{availability}'.", False)
    elif clarified and clarified["answer"].split(" ")[0].lower() in ("low", "medium", "high", "very"):
        level = "very high" if clarified["answer"].lower().startswith("very") else clarified["answer"].split(" ")[0].lower()
        factors["stakeholder_availability"] = ({"low": 2, "medium": 3, "high": 4, "very high": 5}[level],
                                               f"From clarification {clarified['code']}: '{clarified['answer']}'.",
                                               False)
    else:
        factors["stakeholder_availability"] = (3, "Not provided - moderate availability assumed.", True)

    audit = count_cat("auditability")
    v = 3 + (factors["regulatory_criticality"][0] >= 4) + (audit >= 3)
    factors["testing_documentation"] = (clamp(v), f"{audit} auditability requirement(s); regulatory "
                                        f"criticality {factors['regulatory_criticality'][0]}/5.", False)

    plan = f"{project.get('schedule', '')} {project.get('budget', '')}".lower()
    budget_answer = answer_for_topic(ctx, "budget_schedule")
    source = "Schedule/budget"
    if not plan.strip() and budget_answer:
        plan, source = budget_answer["answer"].lower(), f"From clarification {budget_answer['code']}"
    months = [int(m) for m in re.findall(r"(\d+)\s*month", plan)]
    if plan.strip():
        v = 3
        if months:
            v = 5 if min(months) <= 3 else 4 if min(months) <= 6 else 2 if min(months) >= 18 else 3
        v += ("fixed" in plan or "tight" in plan) - ("flexible" in plan)
        factors["budget_schedule_constraints"] = (clamp(v), f"{source}: '{plan.strip()[:120]}'.", False)
    else:
        factors["budget_schedule_constraints"] = (3, "Budget and schedule not provided - moderate constraints "
                                                     "assumed.", True)

    formal = hits(_FORMAL_WORDS)
    v = 2 + (factors["regulatory_criticality"][0] >= 4) + (formal >= 2) + (domain in ("payments", "lending",
                                                                                         "regulatory_reporting"))
    factors["formal_verification"] = (clamp(v), f"{formal} calculation/integrity indicator(s); "
                                      f"regulatory criticality {factors['regulatory_criticality'][0]}/5.", False)

    base = {"payments": 5, "digital_banking": 4, "lending": 4, "fraud_detection": 4, "regulatory_reporting": 4,
            "kyc_onboarding": 4}.get(domain, 3)
    high_availability = bool(re.search(r"99\.9", text))
    factors["failure_consequence"] = (clamp(base + high_availability), f"Domain baseline {base}/5"
                                      + ("; 99.9%+ availability required." if high_availability else "."), False)

    return [{"id": fid, "name": name, "low_meaning": low, "high_meaning": high,
             "rule_value": factors[fid][0], "value": factors[fid][0], "adjustment": 0,
             "explanation": factors[fid][1], "assumption": factors[fid][2], "llm_reason": ""}
            for fid, name, _w, low, high in FACTORS]


# --------------------------------------------------------------------------
# Steps 2-5: scoring, decision rules, ranking
# --------------------------------------------------------------------------
def score_model(model: str, values: dict[str, int], weights: dict[str, float]) -> tuple[float, list[dict]]:
    earned, possible, breakdown = 0.0, 0.0, []
    for fid, name, *_ in FACTORS:
        affinity = AFFINITY[model][fid]
        if affinity == 0:
            continue
        x = (values[fid] - 1) / 4
        fit = x if affinity > 0 else 1 - x
        max_points = weights[fid] * abs(affinity)
        earned += max_points * fit
        possible += max_points
        breakdown.append({"factor": fid, "name": name, "value": values[fid], "affinity": affinity,
                          "weight": weights[fid], "fit": round(fit, 2), "points": round(max_points * fit, 2),
                          "max_points": round(max_points, 2)})
    return (100 * earned / possible if possible else 0.0), breakdown


def rank_models(values: dict[str, int], weights: dict[str, float]) -> tuple[list[dict], list[dict]]:
    fired = [(rid, text, penalties) for rid, text, condition, penalties in DECISION_RULES if condition(values)]
    ranking = []
    for model in MODELS:
        base, breakdown = score_model(model, values, weights)
        penalties = [{"rule": rid, "description": text, "points": -p[model]}
                     for rid, text, p in fired if model in p]
        score = max(0.0, base + sum(x["points"] for x in penalties))
        ranked = sorted(breakdown, key=lambda b: b["fit"] * b["max_points"], reverse=True)
        profile = MODEL_PROFILES[model]
        ranking.append({
            "model": model, "score": round(score, 1), "base_score": round(base, 1), "penalties": penalties,
            "is_hybrid": model.startswith("Hybrid"), "breakdown": breakdown,
            "strengths": [b["name"] for b in ranked if b["fit"] >= 0.75][:4],
            "weaknesses": [b["name"] for b in sorted(breakdown, key=lambda b: b["fit"]) if b["fit"] <= 0.25][:4],
            "description": profile["description"], "advantages": profile["advantages"],
            "disadvantages": profile["disadvantages"], "components": profile.get("components", []),
        })
    ranking.sort(key=lambda r: r["score"], reverse=True)
    for rank, entry in enumerate(ranking, start=1):
        entry["rank"] = rank
        entry["suitable"] = entry["score"] >= SUITABLE_THRESHOLD
    rules = [{"rule": rid, "description": text, "penalties": p} for rid, text, p in fired]
    return ranking, rules


def explain(ranking: list[dict], factors: list[dict]) -> tuple[str, list[str], str]:
    """Deterministic justification, trade-offs and hybrid explanation."""
    top, runner = ranking[0], ranking[1]
    by_factor = {f["id"]: f for f in factors}
    strong = [b for b in sorted(top["breakdown"], key=lambda b: b["points"], reverse=True) if b["fit"] >= 0.75][:4]
    reasons = "; ".join(f"{b['name'].lower()} is {by_factor[b['factor']]['value']}/5" for b in strong)
    text = (f"{top['model']} ranks first with a suitability score of {top['score']}/100 "
            f"({runner['model']} follows with {runner['score']}). It matches this project because {reasons}. "
            f"{top['description']}")
    if not top["suitable"]:
        text += (f" Note: no model reached the suitability threshold of {SUITABLE_THRESHOLD}; the recommendation "
                 "is low-confidence and needs careful human review.")
    if top["score"] - runner["score"] < 5:
        text += f" The margin over {runner['model']} is small, so both options deserve discussion."
    runner_fit = {b["factor"]: b for b in runner["breakdown"]}
    trade_offs = []
    for b in top["breakdown"]:
        other = runner_fit.get(b["factor"])
        if other and other["fit"] - b["fit"] >= 0.25:
            trade_offs.append(f"{runner['model']} handles '{b['name'].lower()}' "
                              f"({by_factor[b['factor']]['value']}/5) better than {top['model']}.")
    trade_offs += [f"{top['model']}: {d}" for d in top["disadvantages"]]
    hybrid = ""
    if top["is_hybrid"]:
        hybrid = (f"{top['model']} is preferred over a single model because the project pulls in two "
                  f"directions: {', '.join(top['strengths'][:3]).lower()}. Combined components: "
                  + " | ".join(top["components"]))
    return text, trade_offs, hybrid


# --------------------------------------------------------------------------
# OpenAI: bounded factor review and narrative explanation
# --------------------------------------------------------------------------
class FactorAdjustment(BaseModel):
    factor_id: Literal[tuple(FACTOR_IDS)]  # type: ignore[valid-type]
    adjustment: int
    reason: str
    requirement_ids: list[str]


class FactorReview(BaseModel):
    adjustments: list[FactorAdjustment]


class AlternativeNote(BaseModel):
    model: str
    reason: str


class Narrative(BaseModel):
    justification: str
    trade_offs: list[str]
    alternatives: list[AlternativeNote]


_REVIEW_INSTRUCTIONS = """Review the rule-derived SDLC decision factors (1=low, 5=high) against the \
requirements. Propose an adjustment of -1, 0 or +1 for a factor ONLY when the requirements clearly justify \
it; give the reason and the requirement IDs that justify it. Do not compute suitability scores."""

_NARRATIVE_INSTRUCTIONS = """Explain the deterministic SDLC ranking to a project board in clear prose: why \
the top-ranked model suits this specific project, the main trade-offs, and briefly why each alternative \
ranked lower. Do NOT change, recompute or contradict the scores. Scores are suitability scores, not \
probabilities."""


def _review_factors(ctx: dict, factors: list[dict]) -> str:
    table = "\n".join(f"{f['id']}: {f['value']}/5 - {f['explanation']}" for f in factors)
    listing = "\n".join(f"{r['req_id']}: {r['statement']}" for r in ctx["requirements"][:120])
    clarified = clarification_text(ctx)
    if clarified:
        listing += f"\n\nAnswers given during clarification (supplementary, not requirements): {clarified[:4000]}"
    data = (f"Project: {ctx['project']['name']} ({ctx['project']['domain']}): {ctx['project']['description']}\n"
            f"Factors:\n{table}\n\n" + wrap_untrusted("requirements", mask_sensitive(listing)[0]))
    review = llm.structured("SDLC factor review", _REVIEW_INSTRUCTIONS, data, FactorReview, ctx["llm_log"])
    by_id = {f["id"]: f for f in factors}
    changed = 0
    for adj in review.adjustments:
        step = max(-1, min(1, adj.adjustment))  # bounded: never more than one point
        factor = by_id.get(adj.factor_id)
        if not factor or step == 0:
            continue
        ids = valid_ids(ctx, "sdlc", adj.requirement_ids)
        factor["adjustment"] = step
        factor["value"] = clamp(factor["rule_value"] + step)
        factor["llm_reason"] = adj.reason + (f" (evidence: {', '.join(ids)})" if ids else "")
        changed += 1
    return f"OpenAI adjusted {changed} factor(s) by +/-1."


def _narrative(ctx: dict, ranking: list[dict]) -> Narrative:
    table = "\n".join(f"{r['rank']}. {r['model']}: {r['score']} (strengths: {', '.join(r['strengths'])}; "
                      f"weaknesses: {', '.join(r['weaknesses'])})" for r in ranking)
    factors = "\n".join(f"{f['name']}: {f['value']}/5" for f in ctx["sdlc_factors"])
    data = (f"Project: {ctx['project']['name']} ({ctx['project']['domain']})\nFactors:\n{factors}\n"
            f"Ranking (fixed):\n{table}")
    return llm.structured("SDLC recommendation narrative", _NARRATIVE_INSTRUCTIONS, data, Narrative, ctx["llm_log"])


def run(ctx: dict) -> tuple[str, str]:
    factors = derive_factors(ctx)
    ctx["sdlc_factors"] = factors
    status, notes = "completed", []
    if llm.enabled():
        try:
            notes.append(_review_factors(ctx, factors))
        except llm.LLMError as exc:
            status = "partial"
            notes.append(f"OpenAI factor review failed ({exc}); rule ratings used.")

    for f in factors:
        if f["assumption"]:
            ctx["findings"].append(finding("assumption", "sdlc_factor", "Low", f"{f['name']}: {f['explanation']}",
                                           [], explanation=f"Used rating {f['value']}/5 for the SDLC decision."))
    values = {f["id"]: f["value"] for f in factors}
    weights = {**DEFAULT_WEIGHTS, **ctx.get("sdlc_weights", {})}
    ranking, rules = rank_models(values, weights)
    justification, trade_offs, hybrid = explain(ranking, factors)
    generated_by = "rules"
    if llm.enabled():
        try:
            narrative = _narrative(ctx, ranking)
            justification = f"{justification}\n\n{narrative.justification}"
            trade_offs = narrative.trade_offs + trade_offs
            notes_by_model = {a.model: a.reason for a in narrative.alternatives}
            for entry in ranking:
                entry["llm_note"] = notes_by_model.get(entry["model"], "")
            generated_by = f"rules + {llm.model_label()} (explanation only)"
        except llm.LLMError as exc:
            status = "partial"
            notes.append(f"OpenAI narrative failed ({exc}); rule-based explanation used.")

    ctx["sdlc"] = {"recommended_model": ranking[0]["model"], "is_hybrid": ranking[0]["is_hybrid"],
                   "factors": factors, "ranking": ranking, "rules_applied": rules, "justification": justification,
                   "trade_offs": trade_offs, "hybrid_explanation": hybrid, "generated_by": generated_by,
                   "weights": weights}
    return status, (f"Recommended {ranking[0]['model']} ({ranking[0]['score']}/100); runner-up "
                    f"{ranking[1]['model']} ({ranking[1]['score']}). {' '.join(notes)}").strip()
