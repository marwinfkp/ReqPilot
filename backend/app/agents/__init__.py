"""ReqPilot agents. Each agent module exposes `run(ctx) -> (status, message)`.

`ctx` is the shared analysis context (a plain dict) owned by the orchestrator
in app/pipeline.py. Agents read what earlier agents produced and add their own
results to it - this explicit hand-over is the agents' "communication".
"""


def finding(kind: str, issue_type: str, severity: str, title: str, requirement_ids: list[str],
            explanation: str = "", evidence: str = "", suggestion: str = "", question: str = "",
            generated_by: str = "rules") -> dict:
    """Uniform shape for every finding stored in the `findings` table."""
    return {"kind": kind, "issue_type": issue_type, "severity": severity, "title": title[:300],
            "requirement_ids": requirement_ids, "explanation": explanation, "evidence": evidence,
            "suggestion": suggestion, "question": question, "generated_by": generated_by}


def check_reference(ctx: dict, agent: str, valid: bool) -> bool:
    """Record whether a reference produced by the LLM (requirement ID, chunk ID,
    quoted evidence) actually exists. Used for the hallucination-rate metric."""
    ctx["reference_checks"].append({"agent": agent, "valid": valid})
    return valid


def valid_ids(ctx: dict, agent: str, ids: list[str]) -> list[str]:
    known = ctx["requirement_ids"]
    return [i for i in ids if check_reference(ctx, agent, i in known)]
