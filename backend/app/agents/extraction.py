"""Agent 1 - Requirement Extraction Agent.

Turns the raw submission (pasted text + uploaded documents) into individual,
normalised requirement statements with a unique ID, kind and source.

1. The input is split into numbered lines; stakeholder tags such as
   "[Compliance Officer]" or "Customer:" are recognised as the source.
2. Rules mode: every line containing requirement language ("shall", "must",
   "should", ...) becomes one requirement; headings and notes are skipped.
   OpenAI mode: the model receives the numbered (masked) lines in batches and
   returns requirements that point back to line numbers. Line numbers that do
   not exist are rejected, so the model cannot invent requirements.
3. IDs are assigned per kind and domain, e.g. FR-PAY-001, NFR-PAY-004.
"""
import re
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.rules import STAKEHOLDER_ROLES, domain_code, find_keywords
from app.security import mask_sensitive, wrap_untrusted

_ROLE_TAG = re.compile(r"^\s*\[([^\]]{2,60})\]\s*")
_ROLE_PREFIX = re.compile(r"^\s*(" + "|".join(re.escape(r) for r in STAKEHOLDER_ROLES) + r")s?\s*[:\-–]\s+", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•●▪◦]|\(?\d{1,3}[.)]|\(?[a-z][.)]|(?:REQ|FR|NFR|BR|R)[-_ ]?\d+[:.)-]?)\s+", re.I)
_REQUIREMENT_LANGUAGE = re.compile(
    r"\b(shall|must|should|will be|will|may|needs? to|has to|have to|is required|are required|"
    r"wants? to|needs?|ensure|be able to)\b", re.I)
_NOTE_PREFIX = re.compile(r"^\s*(note|context|background|assumption|comment|fyi)\s*[:\-]", re.I)

_CONSTRAINT_WORDS = ["hosted", "deployed on", "must use", "shall use", "built using", "built on", "technology stack",
                     "deadline", "go-live", "go live", "launch by", "must be completed", "on-premise",
                     "on premise", "programming language", "framework", "within india", "only in india",
                     "in india", "budget"]
_BUSINESS_RULE_WORDS = ["limit", "maximum", "minimum", "not exceed", "exceeding", "eligib", "only if", "fee",
                        "charge", "interest", "penalty", "threshold", "at least", "no more than", "per day",
                        "per transaction", "cap ", "capped"]
_NFR_WORDS = ["response time", "latency", "within", "seconds", "availability", "uptime", "99.", "concurrent",
              "scalab", "encrypt", "tls", "comply", "complian", "user-friendly", "user friendly", "intuitive",
              "accessib", "reliab", "maintainab", "performance", "secure", "audit trail", "retain", "retention",
              "backup", "recover", "disaster", "24x7", "24/7", "load", "throughput", "stored", "localis",
              "localiz", "masked", "fast", "robust", "seamless", "available", "downtime"]
_FR_VERBS = ["allow", "enable", "send", "display", "show", "generate", "calculate", "compute", "transfer",
             "validate", "verify", "notify", "create", "register", "upload", "download", "view", "submit",
             "approve", "reject", "block", "freeze", "search", "export", "schedule", "apply", "pay ",
             "initiate", "capture", "record", "update", "cancel", "track", "flag", "assign", "raise",
             "provide", "support", "fetch", "integrate", "link", "open", "close", "disburse", "accept"]


# --------------------------------------------------------------------------
# Step 1: split the submission into numbered lines
# --------------------------------------------------------------------------
def segment_input(sources: list[dict]) -> list[dict]:
    """Split every input source into lines and recognise source tags/headings."""
    lines, number = [], 0
    for src in sources:
        section = "General"
        for raw in src["text"].splitlines():
            if not raw.strip():
                continue
            # Very long paragraphs (e.g. from PDFs) are split into sentences.
            pieces = [raw] if len(raw) < 400 else re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", raw)
            for piece in pieces:
                number += 1
                line = _parse_line(number, piece.strip(), src["name"])
                if line["is_heading"]:
                    section = line["body"].rstrip(":").strip()[:80]
                line["section"] = section
                lines.append(line)
    return lines


def _parse_line(number: int, raw: str, default_source: str) -> dict:
    body, source, tagged = raw, default_source, False
    body = _BULLET.sub("", body, count=1)
    tag = _ROLE_TAG.match(body)
    if tag:
        source, body, tagged = tag.group(1).strip().title(), body[tag.end():], True
    else:
        prefix = _ROLE_PREFIX.match(body)
        if prefix:
            source, body, tagged = prefix.group(1).strip().title(), body[prefix.end():], True
    body = _BULLET.sub("", body.strip(), count=1).strip()
    words = body.split()
    is_heading = (body.endswith(":") and len(words) <= 8) or (body.isupper() and len(words) <= 6)
    is_note = bool(_NOTE_PREFIX.match(body))
    looks_like_requirement = bool(_REQUIREMENT_LANGUAGE.search(body)) and len(words) >= 4
    return {"line": number, "raw": raw, "body": body, "source": source, "tagged": tagged,
            "is_heading": is_heading, "is_note": is_note,
            "is_requirement": looks_like_requirement and not is_heading and not is_note}


# --------------------------------------------------------------------------
# Rule helpers
# --------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Light, meaning-preserving normalisation: whitespace, capitalisation, full stop."""
    text = re.sub(r"\s+", " ", text).strip().strip("-–• ")
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def detect_kind(text: str) -> str:
    lower = text.lower()
    if find_keywords(lower, _CONSTRAINT_WORDS):
        return "constraint"
    if find_keywords(lower, _BUSINESS_RULE_WORDS) and re.search(r"\d|₹|rs\.?|inr", lower):
        return "business_rule"
    nfr = len(find_keywords(lower, _NFR_WORDS))
    fr = len(find_keywords(lower, _FR_VERBS))
    return "non_functional" if nfr > fr else "functional"


def assign_ids(requirements: list[dict], domain: str) -> None:
    prefixes = {"functional": "FR", "non_functional": "NFR", "business_rule": "BR", "constraint": "CON"}
    counters: dict[str, int] = {}
    code = domain_code(domain)
    for req in requirements:
        prefix = prefixes.get(req["kind"], "FR")
        counters[prefix] = counters.get(prefix, 0) + 1
        req["req_id"] = f"{prefix}-{code}-{counters[prefix]:03d}"


def _new_requirement(line: dict, statement: str, kind: str, source: str, generated_by: str,
                     original: str | None = None) -> dict:
    return {
        "req_id": "", "statement": statement, "original_text": original or line["raw"],
        "source": source, "has_stakeholder_source": line["tagged"] or source != line["source"],
        "kind": kind, "categories": [], "priority": "Medium", "confidence": 0.6,
        "dependencies": [], "risk_level": "Low", "user_story": "", "acceptance_criteria": [],
        "test_recommendations": [], "generated_by": generated_by, "line": line["line"],
        "section": line.get("section", "General"),
    }


def extract_with_rules(lines: list[dict]) -> list[dict]:
    return [
        _new_requirement(line, normalise(line["body"]), detect_kind(line["body"]), line["source"], "rules")
        for line in lines if line["is_requirement"]
    ]


# --------------------------------------------------------------------------
# OpenAI extraction
# --------------------------------------------------------------------------
class ExtractedRequirement(BaseModel):
    line_numbers: list[int]
    statement: str
    kind: Literal["functional", "non_functional", "business_rule", "constraint"]
    source: str


class ExtractionOutput(BaseModel):
    requirements: list[ExtractedRequirement]


_INSTRUCTIONS = """Extract every individual software requirement from the numbered lines.
- A line may contain several requirements; split them. Headings, notes and context lines are not requirements.
- statement: the requirement normalised into one clear sentence. Keep the original meaning, obligation \
strength (shall/must/should/may), numbers and conditions. Do not add new details or fix defects - \
ambiguous wording must stay ambiguous so it can be reported.
- kind: functional, non_functional (quality attribute), business_rule (policy, limit, eligibility, fee) \
or constraint (technology, hosting, schedule, budget restriction).
- line_numbers: the line number(s) the requirement came from (only numbers shown in the input).
- source: the stakeholder given in the line's tag (e.g. "Compliance Officer"), otherwise "Unspecified"."""


def _extract_batch_llm(batch: list[dict], project: dict, log: list) -> list[dict]:
    numbered = "\n".join(f"L{line['line']}: {mask_sensitive(line['raw'])[0]}" for line in batch)
    data = (f"Project: {project['name']} ({project['domain']}). {project['description']}\n\n"
            + wrap_untrusted("requirements", numbered))
    output = llm.structured("requirement extraction", _INSTRUCTIONS, data, ExtractionOutput, log)
    by_number = {line["line"]: line for line in batch}
    results = []
    for item in output.requirements:
        valid = [n for n in item.line_numbers if n in by_number]
        if not valid or not item.statement.strip():
            continue  # rejects hallucinated requirements that point to no input line
        first = by_number[valid[0]]
        source = first["source"] if first["tagged"] or item.source.lower() == "unspecified" else item.source.title()
        original = " ".join(by_number[n]["raw"] for n in valid)
        req = _new_requirement(first, normalise(item.statement), item.kind, source, llm.model_label(), original)
        req["confidence"] = 0.8
        results.append(req)
    return results


def run(ctx: dict) -> tuple[str, str]:
    lines = segment_input(ctx["sources"])
    ctx["lines"] = lines
    ctx["submitted_count"] = sum(1 for line in lines if line["is_requirement"])
    status, note = "completed", ""

    if llm.enabled():
        batches = llm.batched(lines, 60)
        results = llm.map_parallel(lambda b: _extract_batch_llm(b, ctx["project"], ctx["llm_log"]), batches)
        requirements, failed = [], 0
        for batch, result in zip(batches, results):
            if isinstance(result, Exception):
                failed += 1
                requirements.extend(extract_with_rules(batch))  # rule fallback for this batch only
            else:
                requirements.extend(result)
        if failed:
            status, note = "partial", f" OpenAI failed for {failed}/{len(batches)} batches; rules used for those lines."
    else:
        requirements = extract_with_rules(lines)

    requirements.sort(key=lambda r: r["line"])
    assign_ids(requirements, ctx["project"]["domain"])
    ctx["requirements"] = requirements
    return status, f"Extracted {len(requirements)} requirements from {len(lines)} input lines.{note}"
