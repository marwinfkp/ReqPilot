"""Clarification context shared by the existing agents (not an agent itself).

Clarification answers never overwrite the submitted requirements. Instead:
* answers linked to a requirement are attached to it as `supplementary`
  entries ({kind, ref, question, text}) - agents may read them as context;
* answers about the project as a whole are kept in ctx["project_clarifications"];
* findings that a question addressed get a `resolution`:
    resolved | partially_resolved | deferred | unresolved
  (unanswered information is never assumed - it stays unresolved).

ctx["clarifications"] is a list of dicts built by app/clarification.py:
{code, question, category, issue_type, topic, priority, status, answer, lines,
 requirement_ids, stage}
"""
import re

# Which finding issue types each question category can resolve.
CATEGORY_RESOLVES = {
    "conflict": {"conflict", "stakeholder_conflict"},
    "stakeholder_conflict": {"conflict", "stakeholder_conflict"},
    "ambiguity": {"ambiguity", "untestable"},
    "incompleteness": {"incompleteness"},
    "acceptance_criteria": {"missing_acceptance_criteria", "untestable"},
    "infeasibility": {"infeasibility"},
    "terminology": {"undefined_term"},
    "source": {"missing_source"},
    "performance": {"missing_acceptance_criteria", "ambiguity"},
    "availability": {"missing_acceptance_criteria", "ambiguity", "infeasibility"},
}
# Issue types whose resolution needs a measurable value in the answer.
NEEDS_NUMBER = {"ambiguity", "untestable", "missing_acceptance_criteria", "infeasibility"}
# Security areas (Security & Privacy Agent) -> the clarification topic asking the same thing.
AREA_TOPIC = {"authentication": "authentication", "access_control": "access_control", "encryption": "encryption",
              "audit_logging": "audit_logging", "data_retention": "data_retention",
              "transaction_integrity": "transaction_integrity", "secure_integration": "third_party_api_security",
              "data_protection": "consent_privacy", "authorisation": "authorisation",
              "threat_vulnerability": "secure_development"}


def answered(question: dict) -> bool:
    return question["status"] == "answered" and bool((question.get("answer") or "").strip())


def attach_to_requirements(ctx: dict) -> None:
    """Link each question to the final requirement IDs (via the input line numbers)
    and attach answered questions to those requirements as supplementary info."""
    by_line: dict[int, list[dict]] = {}
    for req in ctx["requirements"]:
        by_line.setdefault(req.get("line", -1), []).append(req)
        req.setdefault("supplementary", [])
    project_level = []
    for q in ctx.get("clarifications", []):
        if q.get("lines"):
            linked = [r for line in q["lines"] for r in by_line.get(line, [])]
            q["requirement_ids"] = sorted({r["req_id"] for r in linked})
        else:
            linked = [r for r in ctx["requirements"] if r["req_id"] in set(q.get("requirement_ids", []))]
        if not answered(q):
            continue
        entry = {"kind": "clarification", "ref": q["code"], "question": q["question"], "text": q["answer"].strip()}
        if linked:
            for req in linked:
                if not any(s.get("ref") == q["code"] for s in req["supplementary"]):
                    req["supplementary"].append(entry)
        else:
            project_level.append(entry | {"topic": q.get("topic", ""), "category": q["category"]})
    ctx["project_clarifications"] = project_level


def supplementary_text(req: dict) -> str:
    """The requirement plus any supplementary information (for keyword checks and retrieval).
    Change-provenance notes (kind "change") are records, not requirement content."""
    extra = " ".join(s["text"] for s in req.get("supplementary", []) if s.get("kind") != "change")
    return f"{req['statement']} {extra}".strip()


def clarification_text(ctx: dict) -> str:
    """All answered clarifications as plain text (project-level context)."""
    return " ".join(q["answer"] for q in ctx.get("clarifications", []) if answered(q))


def answer_for_topic(ctx: dict, topic: str) -> dict | None:
    """The answered question about a project-level topic (e.g. an SDLC factor), if any."""
    for q in ctx.get("clarifications", []):
        if q.get("topic") == topic and answered(q):
            return q
    return None


def _resolution(question: dict, issue_type: str) -> str:
    if question["status"] == "deferred":
        return "deferred"
    if not answered(question):
        return "unresolved"  # open, or waiting for a stakeholder: nothing is assumed
    if issue_type in NEEDS_NUMBER and not re.search(r"\d", question["answer"]):
        return "partially_resolved"  # answered, but still no measurable value
    return "resolved"


_RANK = {"unresolved": 0, "deferred": 1, "partially_resolved": 2, "resolved": 3}


def resolve_findings(ctx: dict, findings: list[dict]) -> None:
    """Mark findings addressed by clarification questions (in place)."""
    questions = ctx.get("clarifications", [])
    for f in findings:
        matches = [q for q in questions
                   if f["issue_type"] in CATEGORY_RESOLVES.get(q["category"], set())
                   and set(q.get("requirement_ids", [])) & set(f["requirement_ids"])]
        if not matches:
            continue
        best = max((_resolution(q, f["issue_type"]) for q in matches), key=_RANK.get)
        f["resolution"] = best
        f["clarification_refs"] = sorted({q["code"] for q in matches})
        answers = [f"{q['code']}: {q['answer']}" for q in matches if answered(q)]
        if answers:
            f["explanation"] = f"{f['explanation']} Clarification: {' | '.join(answers)}".strip()


def resolve_topic_gap(ctx: dict, finding: dict, topic_keys: set[str], keywords: list[str]) -> None:
    """Compliance/security gaps: a question about the topic (or an answer that
    mentions it) addresses the gap only partially - a formal requirement is still missing."""
    from app.rules import find_keywords

    questions = [q for q in ctx.get("clarifications", []) if q.get("topic") in topic_keys]
    mentioned = [q for q in ctx.get("clarifications", [])
                 if answered(q) and q not in questions and find_keywords(q["answer"], keywords)]
    if not questions and not mentioned:
        return
    refs = sorted({q["code"] for q in questions + mentioned})
    finding["clarification_refs"] = refs
    if any(answered(q) for q in questions + mentioned):
        finding["resolution"] = "partially_resolved"
        answers = "; ".join(f"{q['code']}: {q['answer']}" for q in questions + mentioned if answered(q))
        finding["explanation"] += (f" Addressed in clarification ({answers}) but not yet captured as a formal "
                                   "requirement; a human must confirm the obligation.")
    elif any(q["status"] == "deferred" for q in questions):
        finding["resolution"] = "deferred"
    else:
        finding["resolution"] = "unresolved"


def is_open(finding: dict) -> bool:
    """Still counts as an open issue (anything except fully resolved)."""
    return finding.get("resolution", "") != "resolved"
