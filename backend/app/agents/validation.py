"""Agent 9 (part 2) - Validation Agent.

Deterministic consistency checks over everything the other agents produced.
Each check returns status pass | warn | fail | info with a short detail, and
the report is shown in the results dashboard and the consolidated report.
"""


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def run(ctx: dict) -> tuple[str, str]:
    reqs, checks = ctx["requirements"], []
    ids = [r["req_id"] for r in reqs]
    known = set(ids)

    incomplete = [r["req_id"] for r in reqs if not (r["statement"] and r["categories"] and r["priority"])]
    checks.append(_check("Requirement structure", "fail" if incomplete else "pass",
                         f"{len(reqs) - len(incomplete)}/{len(reqs)} requirements have ID, statement, category "
                         f"and priority." + (f" Incomplete: {', '.join(incomplete[:10])}" if incomplete else "")))
    duplicates = len(ids) - len(known)
    checks.append(_check("Unique requirement IDs", "fail" if duplicates else "pass",
                         f"{duplicates} duplicate IDs." if duplicates else "All requirement IDs are unique."))

    referenced = [i for f in ctx["findings"] for i in f["requirement_ids"]] + \
                 [m["requirement_id"] for m in ctx["mappings"]] + \
                 [i for r in ctx.get("risks", []) for i in r["requirement_ids"]]
    broken = sorted({i for i in referenced if i not in known})
    rejected = sum(1 for c in ctx["reference_checks"] if not c["valid"])
    checks.append(_check("Reference integrity", "fail" if broken else ("warn" if rejected else "pass"),
                         f"{len(referenced)} requirement references checked; {len(broken)} broken. "
                         f"{rejected} invalid reference(s) produced by the LLM were rejected before storage."))

    mappings = ctx["mappings"]
    unverified = [m for m in mappings if not m["citation_verified"]]
    needs = sum(1 for m in mappings if m["needs_verification"])
    if not mappings:
        checks.append(_check("Evidence availability", "warn", "No compliance mappings - evidence coverage is limited."))
    else:
        checks.append(_check("Evidence availability", "warn" if unverified else "pass",
                             f"{len(mappings) - len(unverified)}/{len(mappings)} mappings have a citation verified "
                             f"against the source passage; {needs} require human verification."))

    controlled = [r for r in reqs if {"regulatory", "privacy", "security"} & set(r["categories"])]
    mapped = {m["requirement_id"] for m in mappings}
    linked = sum(1 for r in controlled if r["req_id"] in mapped)
    ratio = linked / len(controlled) if controlled else 1.0
    checks.append(_check("Requirement-to-control traceability", "pass" if ratio >= 0.8 else "warn",
                         f"{linked}/{len(controlled)} regulatory/privacy/security requirements are linked to at "
                         f"least one control ({ratio:.0%})."))

    records = ctx.get("traceability", [])
    coverage = sum(r["coverage"] for r in records) / len(records) if records else 0
    checks.append(_check("Traceability completeness", "pass" if coverage >= 0.9 else "warn",
                         f"Average traceability link coverage {coverage:.0%}; "
                         f"{sum(1 for r in records if r['missing_links'])} requirement(s) with missing links."))

    sdlc = ctx.get("sdlc")
    if not sdlc:
        checks.append(_check("SDLC recommendation consistency", "fail", "No SDLC recommendation was produced."))
    else:
        ranking = sdlc["ranking"]
        ok = (ranking[0]["model"] == sdlc["recommended_model"]
              and all(a["score"] >= b["score"] for a, b in zip(ranking, ranking[1:]))
              and all(0 <= r["score"] <= 100 for r in ranking) and len(sdlc["factors"]) == 13)
        checks.append(_check("SDLC recommendation consistency", "pass" if ok else "fail",
                             f"Recommended model is the top-ranked of {len(ranking)} models; 13 factors scored; "
                             "scores within 0-100." if ok else "Ranking and recommendation are inconsistent."))

    workflow = ctx.get("workflow")
    if not workflow:
        checks.append(_check("Workflow completeness", "fail", "No workflow was generated."))
    else:
        phases = workflow["phases"]
        weak = [p["name"] for p in phases if not (p["entry_criteria"] and p["exit_criteria"] and p["roles"]
                                                  and p["deliverables"])]
        gated = any(p["approval_gates"] for p in phases if p["stage"] in ("accept", "release"))
        matches = sdlc and workflow["sdlc_model"] == sdlc["recommended_model"]
        status = "pass" if not weak and gated and matches else "fail"
        checks.append(_check("Workflow completeness", status,
                             f"{len(phases)} phases with entry/exit criteria, roles and deliverables; release "
                             f"approval gates {'present' if gated else 'MISSING'}; matches the recommended model: "
                             f"{'yes' if matches else 'no'}." + (f" Incomplete phases: {', '.join(weak)}" if weak else "")))

    runs = ctx["agent_runs"]
    failed = [r["agent"] for r in runs if r["status"] == "failed"]
    partial = [r["agent"] for r in runs if r["status"] == "partial"]
    checks.append(_check("Agent execution", "fail" if failed else ("warn" if partial else "pass"),
                         f"{len(runs)} agent stages ran before validation. Failed: {', '.join(failed) or 'none'}. "
                         f"Partial (fallback used): {', '.join(partial) or 'none'}."))
    questions = ctx.get("clarifications", [])
    if questions:
        answered_qs = [q for q in questions if q["status"] == "answered"]
        linked = [q for q in answered_qs if q.get("requirement_ids")]
        lost = [q["code"] for q in answered_qs if q.get("lines") and not q.get("requirement_ids")]
        critical_open = [q["code"] for q in questions if q["priority"] == "Critical" and q["status"] != "answered"]
        status = "warn" if lost or critical_open else "pass"
        checks.append(_check("Clarification provenance", status,
                             f"{len(questions)} clarification question(s): {len(answered_qs)} answered "
                             f"({len(linked)} linked to requirements, {len(answered_qs) - len(linked)} project-level), "
                             f"{sum(q['status'] == 'deferred' for q in questions)} deferred, "
                             f"{sum(q['status'] in ('open', 'needs_stakeholder') for q in questions)} open. Original "
                             "requirement text is unchanged; answers are stored as supplementary information."
                             + (f" Answers that could not be linked to an extracted requirement: {', '.join(lost)}."
                                if lost else "")
                             + (f" Critical questions still unanswered: {', '.join(critical_open)}."
                                if critical_open else "")))
    checks.append(_check("Human approval required", "info",
                         f"The requirement baseline, {len(mappings)} compliance mapping(s), the SDLC recommendation "
                         "and the workflow await approval by authorised reviewers. AI agents never auto-approve."))
    ctx["validation"] = checks
    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    return "completed", f"{len(checks)} validation checks: {fails} failed, {warns} warnings."
