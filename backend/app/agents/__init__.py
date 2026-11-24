"""ReqPilot agents. Each agent module exposes `run(ctx) -> (status, message)`.

`ctx` is the shared analysis context (a plain dict) owned by the orchestrator
in app/pipeline.py. Agents read what earlier agents produced and add their own
results to it - this explicit hand-over is the agents' "communication".

Knowledge-base evidence: agents retrieve passages through services.rag
(retrieve_for_agent). Findings, risks and other outputs carry `citations` (checked
against the passages the agent was actually given) and an `evidence_status`.
"""
from pydantic import BaseModel


class Citation(BaseModel):
    """A citation requested from the model: the chunk_id of a knowledge-base passage that
    was given in the prompt, and a short quote copied word-for-word from that passage."""
    chunk_id: int
    quote: str


def finding(kind: str, issue_type: str, severity: str, title: str, requirement_ids: list[str],
            explanation: str = "", evidence: str = "", suggestion: str = "", question: str = "",
            generated_by: str = "rules") -> dict:
    """Uniform shape for every finding stored in the `findings` table."""
    return {"kind": kind, "issue_type": issue_type, "severity": severity, "title": title[:300],
            "requirement_ids": requirement_ids, "explanation": explanation, "evidence": evidence,
            "suggestion": suggestion, "question": question, "generated_by": generated_by,
            "citations": [], "evidence_status": ""}


def unique_citations(citations: list[dict | None], limit: int = 4) -> list[dict]:
    """One citation per passage (a verified one wins), in order, at most `limit`."""
    unique: dict[int, dict] = {}
    for c in citations:
        if c and (c["chunk_id"] not in unique or (c["verified"] and not unique[c["chunk_id"]]["verified"])):
            unique[c["chunk_id"]] = c
    return list(unique.values())[:limit]


def with_evidence(item: dict, knowledge, citations: list[dict | None]) -> dict:
    """Attach knowledge-base citations to a finding or risk and set its evidence status:
    verified / unverified / none (derived from the requirements) / unavailable."""
    item["citations"] = unique_citations(citations)
    item["evidence_status"] = knowledge.status(item["citations"])
    return item


def check_reference(ctx: dict, agent: str, valid: bool) -> bool:
    """Record whether a reference produced by the LLM (requirement ID, chunk ID,
    quoted evidence) actually exists. Used for the hallucination-rate metric."""
    ctx["reference_checks"].append({"agent": agent, "valid": valid})
    return valid


def valid_ids(ctx: dict, agent: str, ids: list[str]) -> list[str]:
    known = ctx["requirement_ids"]
    return [i for i in ids if check_reference(ctx, agent, i in known)]


def all_citations(ctx: dict) -> list[dict]:
    """Every knowledge-base citation attached to an output of this analysis (the compliance
    mappings have their own table and metric and are not included)."""
    phases = (ctx.get("workflow") or {}).get("phases", [])
    return ([c for f in ctx["findings"] for c in f.get("citations") or []]
            + [c for r in ctx.get("risks", []) for c in r.get("citations") or []]
            + [c for r in ctx["requirements"] for c in r.get("citations") or []]
            + list((ctx.get("sdlc") or {}).get("citations") or [])
            + [c for p in phases for c in p.get("citations") or []]
            + [c for check in ctx.get("validation", []) for c in check.get("citations") or []])
