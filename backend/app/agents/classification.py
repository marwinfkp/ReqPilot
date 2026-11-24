"""Agent 2 - Requirement Classification Agent.

Multi-label classification into the 16 ReqPilot categories, plus priority,
confidence and dependencies.

Rules mode: keyword dictionaries (app/rules.py) decide the categories, the
obligation word decides the priority (must/shall -> High, should -> Medium,
may/could -> Low; regulatory or security requirements are always High).
OpenAI mode: the model classifies batches of requirements; unknown requirement
IDs returned by the model are ignored and dependencies are validated.
"""
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agents.clarification_context import supplementary_text
from app.rules import CATEGORIES, CATEGORY_KEYWORDS, NON_FUNCTIONAL_CATEGORIES, find_keywords
from app.security import mask_sensitive, wrap_untrusted

Category = Literal["business", "stakeholder", "functional", "non_functional", "security", "privacy",
                   "regulatory", "performance", "availability", "reliability", "usability",
                   "data_management", "integration", "auditability", "operational", "maintenance"]

_KIND_CATEGORIES = {
    "functional": ["functional"],
    "non_functional": ["non_functional"],
    "business_rule": ["functional", "business"],
    "constraint": ["non_functional"],
}
_ID_REFERENCE = re.compile(r"\b(?:FR|NFR|BR|CON)-[A-Z]{3}-\d{3}\b")


def classify_with_rules(req: dict) -> None:
    # Clarification answers linked to the requirement add context for the categories;
    # the obligation word (priority) is always taken from the requirement itself.
    text = supplementary_text(req) if req.get("supplementary") else req["statement"]
    categories = list(_KIND_CATEGORIES.get(req["kind"], ["functional"]))
    hits = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        found = find_keywords(text, keywords)
        if found and category not in categories:
            categories.append(category)
            hits += len(found)
    # A requirement carrying quality attributes is also non-functional.
    if NON_FUNCTIONAL_CATEGORIES & set(categories) and "non_functional" not in categories \
            and req["kind"] != "functional":
        categories.append("non_functional")
    req["categories"] = categories
    req["priority"] = rule_priority(req["statement"], categories)
    req["confidence"] = round(min(0.75, 0.55 + 0.03 * hits), 2)
    req["dependencies"] = sorted(set(_ID_REFERENCE.findall(text)) - {req["req_id"]})


def rule_priority(text: str, categories: list[str]) -> str:
    lower = text.lower()
    if re.search(r"\b(may|could|nice to have|optional|in future|future phase)\b", lower):
        return "Low"
    if re.search(r"\b(must|shall|mandatory|critical|required)\b", lower) or \
            {"regulatory", "security"} & set(categories):
        return "High"
    return "Medium"


class ClassifiedRequirement(BaseModel):
    req_id: str
    categories: list[Category]
    priority: Literal["High", "Medium", "Low"]
    confidence: float
    dependencies: list[str]


class ClassificationOutput(BaseModel):
    items: list[ClassifiedRequirement]


_INSTRUCTIONS = f"""Classify each requirement. categories: every applicable label from {CATEGORIES} \
(multi-label; include "functional" or "non_functional"). priority: High (mandatory, regulatory, \
security-critical or core business), Medium, or Low (optional/nice to have). confidence: 0-1, how certain \
the classification is. dependencies: IDs of other listed requirements that must exist for this one to work \
(only IDs from the input; empty list if none)."""


def _classify_batch_llm(batch: list[dict], all_ids: list[str], log: list) -> list[ClassifiedRequirement]:
    listing = "\n".join(mask_sensitive(f"{r['req_id']} ({r['kind']}): {r['statement']}"
                                       + "".join(f" [Clarified {s['ref']}: {s['text']}]"
                                                 for s in r.get("supplementary", [])))[0] for r in batch)
    data = (f"All requirement IDs in this project: {', '.join(all_ids)}\n\n"
            + wrap_untrusted("requirements", listing))
    return llm.structured("requirement classification", _INSTRUCTIONS, data, ClassificationOutput, log).items


def run(ctx: dict) -> tuple[str, str]:
    # Stage 2 regeneration may limit the work to changed requirements; the rest keep their classification.
    only = ctx.get("only_requirement_ids")
    requirements = [r for r in ctx["requirements"] if only is None or r["req_id"] in only]
    for req in requirements:  # deterministic baseline for every requirement
        classify_with_rules(req)
    if not llm.enabled():
        return "completed", f"Classified {len(requirements)} requirements with keyword rules."

    by_id = {r["req_id"]: r for r in requirements}
    all_ids = [r["req_id"] for r in ctx["requirements"]]
    batches = llm.batched(requirements)
    results = llm.map_parallel(lambda b: _classify_batch_llm(b, all_ids, ctx["llm_log"]), batches)
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            continue
        for item in result:
            req = by_id.get(item.req_id)
            if not req:
                continue  # ignore IDs the model invented
            categories = list(dict.fromkeys(_KIND_CATEGORIES.get(req["kind"], []) + list(item.categories)))
            req["categories"] = categories
            req["priority"] = item.priority
            req["confidence"] = round(max(0.0, min(1.0, item.confidence)), 2)
            req["dependencies"] = sorted({d for d in item.dependencies if d in all_ids and d != req["req_id"]})
            req["generated_by"] = llm.model_label()
    if failed:
        return "partial", f"OpenAI failed for {failed}/{len(batches)} batches; keyword rules kept for those."
    return "completed", f"Classified {len(requirements)} requirements (OpenAI, validated against rules baseline)."
