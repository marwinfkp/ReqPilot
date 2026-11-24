"""Agent 9 (part 1) - Documentation Agent.

* run_artefacts    - user story, acceptance criteria and test recommendations for
                     every requirement, plus use-case descriptions.
* run_traceability - one Requirements Traceability Matrix row per requirement:
                     stakeholder statement -> requirement -> categories ->
                     regulatory / security controls -> user story -> acceptance
                     criteria -> tests -> workflow phases, with missing links.
* run_summary      - executive summary of the whole analysis.
Rules templates in demonstration mode; OpenAI-generated (validated) otherwise.
"""
import re

from pydantic import BaseModel

from app import llm
from app.agents import valid_ids
from app.security import mask_sensitive, wrap_untrusted

_ROLE_WORDS = [("borrower", "Borrower"), ("applicant", "Applicant"), ("merchant", "Merchant"),
               ("customer", "Customer"), ("account holder", "Customer"), ("auditor", "Auditor"),
               ("compliance", "Compliance Officer"), ("fraud analyst", "Fraud Analyst"),
               ("credit analyst", "Credit Analyst"), ("loan officer", "Loan Officer"),
               ("admin", "Administrator"), ("operations", "Operations Staff"), ("staff", "Bank Staff"),
               ("official", "Bank Staff"), ("user", "User")]
_BENEFITS = [("security", "my account and transactions stay protected"),
             ("privacy", "my personal data is protected"),
             ("regulatory", "the bank stays compliant with applicable regulations"),
             ("performance", "the service responds quickly"),
             ("availability", "the service is available when it is needed"),
             ("auditability", "every action can be traced during audits"),
             ("integration", "data flows reliably between systems"),
             ("usability", "the service is easy to use")]
_TESTS = [("security", "Security test: authentication/authorisation bypass and abuse cases"),
          ("performance", "Load test at peak volume against the stated target"),
          ("availability", "Failover and disaster-recovery test"),
          ("privacy", "Privacy test: consent, masking and data-access checks"),
          ("regulatory", "Compliance verification against the mapped controls"),
          ("integration", "Integration / contract test with the external system"),
          ("auditability", "Audit-log completeness and tamper-evidence test"),
          ("usability", "Usability test with representative users"),
          ("data_management", "Data integrity and retention test")]
_MODAL = re.compile(r"^(the\s+)?(system|application|app|platform|portal|solution|bank)?\s*"
                    r"(shall|must|should|will|may|needs to|is required to)\s+(be able to\s+)?", re.I)


def infer_role(req: dict) -> str:
    text = f"{req['statement']} {req['source']}".lower()
    for word, role in _ROLE_WORDS:
        if word in text:
            return role
    return "Bank user"


def rule_story(req: dict) -> tuple[str, list[str], list[str]]:
    role = infer_role(req)
    capability = _MODAL.sub("", req["statement"].rstrip(".")).strip()
    capability = capability[0].lower() + capability[1:] if capability else req["statement"]
    if capability.lower().startswith("allow "):
        capability = re.sub(r"^allow\s+\w+(\s+\w+)?\s+to\s+", "", capability, flags=re.I) or capability
    benefit = next((b for c, b in _BENEFITS if c in req["categories"]), "I can complete my task reliably")
    story = f"As a {role}, I want the system to {capability} so that {benefit}."
    criteria = [f"Given the preconditions are met, when the {role.lower()} performs the action, "
                f"then the system shall {capability}."]
    numbers = re.findall(r"[\d.,]+\s*(?:%|seconds?|minutes?|hours?|days?|years?|ms|attempts?|INR|lakh)", req["statement"])
    if numbers:
        criteria.append(f"The measured result meets the stated target(s): {', '.join(n.strip() for n in numbers)}.")
    if req["kind"] == "business_rule":
        criteria.append("Requests at, just below and just above each limit are handled as the rule specifies.")
    if {"security", "functional"} & set(req["categories"]):
        criteria.append("Given invalid or unauthorised input, when the action is attempted, then it is rejected "
                        "and an audit log entry is recorded.")
    tests = ["Functional test: positive and negative scenarios"] if req["kind"] != "non_functional" else []
    tests += [t for c, t in _TESTS if c in req["categories"]]
    if req["kind"] == "business_rule":
        tests.append("Boundary-value tests for limits and thresholds")
    return story, criteria, tests[:4]


def rule_use_cases(requirements: list[dict]) -> list[dict]:
    """One use case per input section, built from its functional requirements."""
    sections: dict[str, list[dict]] = {}
    for req in requirements:
        if req["kind"] in ("functional", "business_rule"):
            sections.setdefault(req.get("section") or "General", []).append(req)
    use_cases = []
    for index, (section, reqs) in enumerate(sections.items(), start=1):
        roles = [infer_role(r) for r in reqs]
        actor = max(set(roles), key=roles.count)
        steps = [f"{actor} starts '{section}'."]
        steps += [f"System: {_MODAL.sub('', r['statement']).rstrip('.')} ({r['req_id']})."
                  for r in reqs if r["kind"] == "functional"][:8]
        steps.append("System records the outcome in the audit trail and confirms it to the actor.")
        alternates = [f"If the rule in {r['req_id']} is not satisfied, the request is rejected with a reason."
                      for r in reqs if r["kind"] == "business_rule"][:4]
        secure = any("security" in r["categories"] for r in reqs)
        use_cases.append({
            "id": f"UC-{index:02d}", "name": section, "actor": actor,
            "preconditions": [f"{actor} is authenticated" if secure else f"{actor} has access to the channel"],
            "main_flow": steps, "alternate_flows": alternates or ["Validation errors are shown and nothing is saved."],
            "postconditions": ["The transaction/request is completed and logged."],
            "requirement_ids": [r["req_id"] for r in reqs], "generated_by": "rules"})
    return use_cases


class StoryItem(BaseModel):
    req_id: str
    user_story: str
    acceptance_criteria: list[str]
    test_recommendations: list[str]


class StoryOutput(BaseModel):
    items: list[StoryItem]


class UseCase(BaseModel):
    name: str
    actor: str
    preconditions: list[str]
    main_flow: list[str]
    alternate_flows: list[str]
    postconditions: list[str]
    requirement_ids: list[str]


class UseCaseOutput(BaseModel):
    use_cases: list[UseCase]


_STORY_INSTRUCTIONS = """For each requirement write: a user story ("As a <role>, I want <goal> so that \
<benefit>"), 2-4 testable acceptance criteria in Given/When/Then form that preserve the requirement's numbers \
and conditions (do not invent new targets; if the requirement is vague, write the criterion with a \
placeholder like <target to be agreed>), and 1-3 test recommendations."""
_USE_CASE_INSTRUCTIONS = """Group the functional requirements into at most 12 use cases. For each: name, \
primary actor, preconditions, main flow steps, alternate/exception flows, postconditions and the IDs of \
the requirements it covers (only IDs from the input)."""


def run_artefacts(ctx: dict) -> tuple[str, str]:
    requirements = ctx["requirements"]
    for req in requirements:  # baseline for every requirement
        req["user_story"], req["acceptance_criteria"], req["test_recommendations"] = rule_story(req)
    ctx["use_cases"] = rule_use_cases(requirements)
    if not llm.enabled():
        return "completed", (f"Generated {len(requirements)} user stories and {len(ctx['use_cases'])} use "
                             "cases from templates.")
    by_id = {r["req_id"]: r for r in requirements}

    def stories(batch: list[dict]) -> list[StoryItem]:
        # The whole line is masked: a stakeholder tag can also contain personal data.
        listing = "\n".join(mask_sensitive(f"{r['req_id']} ({r['kind']}; source {r['source']}): {r['statement']}")[0]
                            for r in batch)
        return llm.structured("user stories", _STORY_INSTRUCTIONS, wrap_untrusted("requirements", listing),
                              StoryOutput, ctx["llm_log"]).items

    batches = llm.batched(requirements)
    failed = 0
    for result in llm.map_parallel(stories, batches):
        if isinstance(result, Exception):
            failed += 1
            continue
        for item in result:
            req = by_id.get(item.req_id)
            if req and valid_ids(ctx, "documentation", [item.req_id]):
                req["user_story"] = item.user_story
                req["acceptance_criteria"] = item.acceptance_criteria
                req["test_recommendations"] = item.test_recommendations
    try:
        functional = [r for r in requirements if r["kind"] in ("functional", "business_rule")][:150]
        listing = "\n".join(f"{r['req_id']}: {mask_sensitive(r['statement'])[0]}" for r in functional)
        output = llm.structured("use cases", _USE_CASE_INSTRUCTIONS, wrap_untrusted("requirements", listing),
                                UseCaseOutput, ctx["llm_log"])
        ctx["use_cases"] = [{"id": f"UC-{i:02d}", **uc.model_dump(),
                             "requirement_ids": valid_ids(ctx, "documentation", uc.requirement_ids),
                             "generated_by": llm.model_label()} for i, uc in enumerate(output.use_cases, start=1)]
    except llm.LLMError:
        failed += 1
    status = "partial" if failed else "completed"
    note = f" {failed} OpenAI call(s) failed; templates kept for those." if failed else ""
    return status, f"Generated user stories, acceptance criteria and {len(ctx['use_cases'])} use cases.{note}"


def run_traceability(ctx: dict) -> tuple[str, str]:
    mappings_by_req: dict[str, list[dict]] = {}
    for m in ctx["mappings"]:
        mappings_by_req.setdefault(m["requirement_id"], []).append(m)
    areas_by_req: dict[str, list[str]] = {}
    for area in ctx.get("security_coverage", []):
        for rid in area["requirement_ids"]:
            areas_by_req.setdefault(rid, []).append(area["label"])
    phases = ctx.get("workflow", {}).get("phases", [])
    verify_phases = [p["name"] for p in phases if p["stage"] in ("verify", "accept")]
    records = []
    for req in ctx["requirements"]:
        rid, cats = req["req_id"], set(req["categories"])
        maps = mappings_by_req.get(rid, [])
        label = lambda m: f"{m['document_name']} {m['control_ref']} [{m['authenticity']}]"  # noqa: E731
        regulatory = [label(m) for m in maps if m["doc_type"] != "security_standard"]
        security = [label(m) for m in maps if m["doc_type"] == "security_standard"] + \
                   [f"Security area: {a}" for a in areas_by_req.get(rid, [])]
        workflow_phases = [p["name"] for p in phases if rid in p["key_requirements"]] or verify_phases[-1:]
        expected = {"user story": bool(req["user_story"]), "acceptance criteria": bool(req["acceptance_criteria"]),
                    "test recommendation": bool(req["test_recommendations"]), "workflow phase": bool(workflow_phases)}
        if cats & {"regulatory", "privacy"}:
            expected["regulatory control"] = bool(regulatory)
        if "security" in cats:
            expected["security control"] = bool(security)
        missing = [name for name, present in expected.items() if not present]
        records.append({
            "requirement_id": rid, "source_statement": req["original_text"], "source": req["source"],
            "categories": req["categories"], "regulatory_controls": regulatory, "security_controls": security,
            "user_story": req["user_story"], "acceptance_criteria": req["acceptance_criteria"],
            "tests": req["test_recommendations"], "workflow_phases": workflow_phases, "missing_links": missing,
            "coverage": round(1 - len(missing) / len(expected), 2)})
    ctx["traceability"] = records
    coverage = sum(r["coverage"] for r in records) / len(records) if records else 0
    return "completed", (f"Built {len(records)} traceability records; average link coverage {coverage:.0%}, "
                         f"{sum(1 for r in records if r['missing_links'])} with missing links.")


class Summary(BaseModel):
    summary: str
    key_findings: list[str]


def run_summary(ctx: dict) -> tuple[str, str]:
    m, project = ctx["metrics"], ctx["project"]
    sdlc = ctx.get("sdlc", {})
    rule_summary = (
        f"ReqPilot analysed {m['submitted']} submitted statements for '{project['name']}' "
        f"({project['domain'].replace('_', ' ')}) and extracted {m['extracted']} requirements "
        f"({m['functional']} functional, {m['non_functional']} non-functional). It found {m['quality_issues']} "
        f"quality issues including {m['conflicts']} conflicts and {m['ambiguities']} ambiguities, created "
        f"{m['compliance_mappings']} compliance mappings with {m['compliance_gaps']} compliance gaps, identified "
        f"{m['security_gaps']} security gaps and registered {m['risks']} risks ({m['high_risks']} high or critical). "
        + (f"The recommended SDLC model is {sdlc['recommended_model']} with a suitability score of "
           f"{m['sdlc_score']}/100." if sdlc else "No SDLC recommendation could be produced."))
    findings = [f["title"] for f in ctx["findings"] if f["severity"] in ("High", "Critical")][:5]
    ctx["summary"], ctx["key_findings"] = rule_summary, findings
    if not llm.enabled():
        return "completed", "Summary generated from analysis metrics (template)."
    data = (f"Metrics: {m}\nRecommended SDLC: {sdlc.get('recommended_model')}\nTop findings:\n"
            + "\n".join(f"- {f['title']}" for f in ctx["findings"] if f["severity"] in ("High", "Critical"))[:6000])
    try:
        output = llm.structured("executive summary", "Write a concise executive summary (120-180 words) of this "
                                "requirement analysis for a project board, and up to 6 key findings. Use only "
                                "the numbers given.", data, Summary, ctx["llm_log"])
        ctx["summary"], ctx["key_findings"] = output.summary, output.key_findings[:6]
        return "completed", "Executive summary written by OpenAI."
    except llm.LLMError as exc:
        return "partial", f"OpenAI summary failed ({exc}); template summary used."

