"""Two-stage analysis: Stage 1 clarification -> full pipeline, optional Stage 2 refinement.

Covers: question generation (<= 12, grounded in the input), answering / deferring and
answer validation, the complete 12-agent pipeline after clarification, unanswered
questions staying unresolved, Stage 2 being optional, follow-up questions,
stakeholder interviews, requirement changes with four-eyes approval, dependency-aware
selective regeneration with preserved versions, and risk acceptance permissions.
"""
import uuid

import pytest

from app import pipeline, regeneration
from tests.helpers import CASES

PIPELINE_AGENTS = [stage[0] for stage in pipeline.STAGES]


@pytest.fixture(autouse=True)
def _synchronous_regeneration(monkeypatch):
    monkeypatch.setattr(regeneration, "start_in_background", regeneration.execute)


def submit(client, auth, case="digital_banking", user="analyst", **overrides) -> int:
    # A unique project name per submission: identical submissions still in progress are rejected (409).
    data = {**{k: str(v) for k, v in CASES[case]["project"].items()},
            "requirements_text": CASES[case]["requirements_text"], "interactive_clarification": "true",
            "name": f"{CASES[case]['project']['name']} {uuid.uuid4().hex[:6]}"}
    data.update(overrides)
    response = client.post("/api/analyses", data=data, headers=auth(user))
    assert response.status_code == 200, response.text
    return response.json()["analysis_id"]


def questions(client, auth, analysis_id, user="analyst") -> list[dict]:
    return client.get(f"/api/analyses/{analysis_id}/clarifications", headers=auth(user)).json()["questions"]


def answer(client, auth, question, status="answered", text=None, user="analyst"):
    if text is None:
        fmt = question["answer_format"]
        text = (question["options"][0] if fmt == "single_choice" else
                "; ".join(question["options"][:2]) if fmt == "multiple_choice" else
                "Yes - confirmed" if fmt == "yes_no" else "At most 2 seconds for 95% of requests at 500 TPS")
    return client.put(f"/api/clarifications/{question['id']}", json={"status": status, "answer": text},
                      headers=auth(user))


def payload(client, auth, analysis_id, user="pm") -> dict:
    return client.get(f"/api/analyses/{analysis_id}", headers=auth(user)).json()


def finish_stage1(client, auth, case="digital_banking", answers=3, user="analyst") -> int:
    analysis_id = submit(client, auth, case, user=user, stakeholder_availability="")
    for q in questions(client, auth, analysis_id)[:answers]:
        assert answer(client, auth, q).status_code == 200
    assert client.post(f"/api/analyses/{analysis_id}/continue", headers=auth(user)).status_code == 200
    return analysis_id


# --------------------------------------------------------------------------
# Test 1 - normal Stage 1 execution
# --------------------------------------------------------------------------
def test_stage1_generates_grounded_questions_then_waits(client, auth):
    analysis_id = submit(client, auth, stakeholder_availability="")
    status = client.get(f"/api/analyses/{analysis_id}/status", headers=auth("analyst")).json()
    assert status["status"] == "awaiting_clarification" and status["stage1_status"] == "awaiting_clarification"

    data = client.get(f"/api/analyses/{analysis_id}/clarifications", headers=auth("analyst")).json()
    qs = data["questions"]
    assert 7 <= len(qs) <= 12
    assert [q["code"] for q in qs] == [f"Q-{i:02d}" for i in range(1, len(qs) + 1)]
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    assert [order[q["priority"]] for q in qs] == sorted(order[q["priority"]] for q in qs)  # most important first
    for q in qs:
        assert q["question"] and q["reason"] and q["impact"] and q["category"] and q["answer_format"]
        assert q["status"] == "open" and q["stage"] == 1
    # Grounded in the actual submission: the conflicting session timeouts are asked about first.
    first = qs[0]
    assert first["category"] in ("conflict", "stakeholder_conflict") and first["priority"] == "Critical"
    assert all(r["text"] in CASES["digital_banking"]["requirements_text"] or r["text"]
               for r in first["related_requirements"])
    assert len({q["question"] for q in qs}) == len(qs)  # no duplicates
    # The pipeline has NOT run yet.
    assert [r["agent"] for r in payload(client, auth, analysis_id)["agent_runs"]] == [
        "Preliminary Analysis", "Clarification Question Generator"]


def test_answer_validation_and_editing(client, auth):
    analysis_id = submit(client, auth, stakeholder_availability="")
    qs = questions(client, auth, analysis_id)
    choice = next(q for q in qs if q["answer_format"] == "single_choice")
    assert answer(client, auth, choice, text="Something unexpected").status_code == 422
    assert answer(client, auth, choice, text="Other: depends on the channel").status_code == 200
    edited = answer(client, auth, choice)  # editing replaces the single current response
    assert edited.status_code == 200 and edited.json()["answer"] == choice["options"][0]
    numeric = next((q for q in qs if q["answer_format"] == "numeric"), None)
    if numeric:
        assert answer(client, auth, numeric, text="as soon as possible").status_code == 422
    assert answer(client, auth, qs[0], text="").status_code == 422  # answered needs text
    assert answer(client, auth, qs[1], status="deferred", text="").status_code == 200
    assert answer(client, auth, qs[2], status="needs_stakeholder", text="Ask the CISO").status_code == 200
    assert answer(client, auth, qs[0], user="auditor").status_code == 403  # auditors cannot answer
    counts = client.get(f"/api/analyses/{analysis_id}/clarifications", headers=auth("analyst")).json()["counts"]
    assert counts["answered"] == 1 and counts["deferred"] == 1 and counts["needs_stakeholder"] == 1


def test_continue_runs_all_twelve_agents_with_clarified_context(client, auth):
    analysis_id = submit(client, auth, stakeholder_availability="")
    qs = questions(client, auth, analysis_id)
    conflict = qs[0]
    assert answer(client, auth, conflict).status_code == 200
    sdlc_q = next(q for q in qs if q["topic"] == "stakeholder_availability")
    assert answer(client, auth, sdlc_q, text="High - a dedicated product owner").status_code == 200

    response = client.post(f"/api/analyses/{analysis_id}/continue", headers=auth("analyst"))
    assert response.status_code == 200
    assert client.post(f"/api/analyses/{analysis_id}/continue", headers=auth("analyst")).status_code == 409

    data = payload(client, auth, analysis_id)
    assert data["status"] == "completed" and data["stage1_status"] == "completed" and data["version"] == 1
    runs = [r for r in data["agent_runs"] if r["order"] > 0]
    assert [r["agent"] for r in runs] == PIPELINE_AGENTS and all(r["status"] == "completed" for r in runs)
    assert all(r["stage"] == "stage1" for r in data["agent_runs"])
    # All existing outputs exist.
    assert data["sdlc"]["recommended_model"] and data["workflow"]["phases"] and data["traceability"]
    assert data["mappings"] and data["risks"] and data["use_cases"] and data["summary"]
    assert any(c["check"] == "Clarification provenance" for c in data["validation_report"])
    # The answered conflict is marked resolved, the original requirement text is untouched.
    resolved = [f for f in data["findings"] if conflict["code"] in f["clarification_refs"]]
    assert resolved and all(f["resolution"] == "resolved" for f in resolved)
    linked = [r for r in data["requirements"] if any(s["ref"] == conflict["code"] for s in r["supplementary"])]
    assert linked and all(r["original_text"] in CASES["digital_banking"]["requirements_text"] for r in linked)
    # The SDLC factor uses the explicit answer instead of an assumption.
    factor = next(f for f in data["sdlc"]["factors"] if f["id"] == "stakeholder_availability")
    assert factor["value"] == 4 and sdlc_q["code"] in factor["explanation"] and not factor["assumption"]
    clar = data["clarification"]
    assert clar["answered"] == 2 and clar["influenced_analysis"] and clar["issues_resolved"] >= 1
    assert data["metrics"]["clarification_questions"] == clar["total"]


# --------------------------------------------------------------------------
# Test 2 - unanswered questions stay unresolved
# --------------------------------------------------------------------------
def test_unanswered_questions_are_not_assumed(client, auth):
    analysis_id = submit(client, auth, stakeholder_availability="")
    qs = questions(client, auth, analysis_id)
    assert client.post(f"/api/analyses/{analysis_id}/continue", headers=auth("analyst")).status_code == 200
    data = payload(client, auth, analysis_id)
    assert [r["status"] for r in data["agent_runs"] if r["order"] > 0] == ["completed"] * 12
    after = questions(client, auth, analysis_id)
    assert all(q["resolution"] == "unresolved" for q in after)
    conflict_findings = [f for f in data["findings"] if qs[0]["code"] in f["clarification_refs"]]
    assert conflict_findings and all(f["resolution"] == "unresolved" for f in conflict_findings)
    factor = next(f for f in data["sdlc"]["factors"] if f["id"] == "stakeholder_availability")
    assert factor["assumption"] is True  # stated as an assumption, not invented as fact
    assert any("clarification question" in r["description"] for r in data["risks"])


def test_non_interactive_api_clients_still_get_a_complete_analysis(client, auth, create_analysis):
    analysis_id = create_analysis("payment_system")  # existing API behaviour (no interactive flag)
    data = payload(client, auth, analysis_id)
    assert data["status"] == "completed" and data["stage1_status"] == "completed"
    log = [(r["agent"], r["status"]) for r in data["agent_runs"] if r["order"] == 0]
    assert log[0] == ("Preliminary Analysis", "completed") and log[-1] == ("Clarification Responses", "skipped")


def test_duplicate_submission_is_rejected(client, auth):
    first = submit(client, auth, "fraud_detection", name="Duplicate check")
    response = client.post("/api/analyses", data={**{k: str(v) for k, v in CASES["fraud_detection"]["project"].items()},
                                                  "requirements_text": CASES["fraud_detection"]["requirements_text"],
                                                  "interactive_clarification": "true", "name": "Duplicate check"},
                           headers=auth("analyst"))
    assert response.status_code == 409 and response.json()["detail"]["analysis_id"] == first


# --------------------------------------------------------------------------
# Test 3 - Stage 2 is optional
# --------------------------------------------------------------------------
def test_stage2_is_optional(client, auth):
    analysis_id = finish_stage1(client, auth, "loan_processing")
    data = payload(client, auth, analysis_id)
    assert data["stage2_status"] == "not_started"
    assert data["sdlc"] and data["workflow"]["phases"]
    for kind in ("sdlc", "workflow", "consolidated"):
        assert client.get(f"/api/analyses/{analysis_id}/export/{kind}", params={"format": "pdf"},
                          headers=auth("auditor")).content[:4] == b"%PDF"
    review = client.post(f"/api/analyses/{analysis_id}/sdlc/review", json={"action": "approve"}, headers=auth("pm"))
    assert review.status_code == 200  # existing approvals work without Stage 2


# --------------------------------------------------------------------------
# Test 4 - Stage 2 clarification and stakeholder interviews
# --------------------------------------------------------------------------
def test_stage2_followup_questions_and_interviews(client, auth):
    analysis_id = finish_stage1(client, auth)
    assert client.post(f"/api/analyses/{analysis_id}/refinement/open",
                       headers=auth("analyst")).json()["stage2_status"] == "in_progress"
    overview = client.get(f"/api/analyses/{analysis_id}/refinement", headers=auth("analyst")).json()
    assert overview["analysis"]["is_latest"] and overview["outstanding"]["ambiguities"]

    before = {q["question"] for q in questions(client, auth, analysis_id)}
    generated = client.post(f"/api/analyses/{analysis_id}/refinement/questions", headers=auth("analyst")).json()
    new = generated["questions"]
    assert 1 <= len(new) <= 8 and all(q["stage"] == 2 and q["code"].startswith("R1-") for q in new)
    assert not {q["question"] for q in new} & before  # never repeats an earlier question
    response = answer(client, auth, new[0])
    assert response.status_code == 200 and response.json()["answered_in_stage"] == 2

    roles = client.get(f"/api/analyses/{analysis_id}/stakeholders/suggestions", headers=auth("analyst")).json()
    assert roles and all(r["questions"] for r in roles)

    req_id = payload(client, auth, analysis_id)["requirements"][0]["req_id"]
    body = {"stakeholder_role": "Compliance Officer", "interviewee": "R. Sharma", "topic": "Data storage",
            "requirement_ids": [req_id], "entries": [{"question": "Where must data be stored?",
                                                      "answer": "Only in India"}]}
    assert client.post(f"/api/analyses/{analysis_id}/stakeholder-interactions", json=body,
                       headers=auth("auditor")).status_code == 403
    bad = {**body, "requirement_ids": ["FR-XXX-999"]}
    assert client.post(f"/api/analyses/{analysis_id}/stakeholder-interactions", json=bad,
                       headers=auth("compliance")).status_code == 422
    interview = client.post(f"/api/analyses/{analysis_id}/stakeholder-interactions", json=body,
                            headers=auth("compliance")).json()
    assert interview["status"] == "pending_review" and interview["recorded_by"] == "compliance@reqpilot.local"
    accepted = client.post(f"/api/stakeholder-interactions/{interview['id']}/review", json={"action": "accept"},
                           headers=auth("pm")).json()
    assert accepted["status"] == "accepted" and accepted["reviewed_by"] == "pm@reqpilot.local"
    plan = client.get(f"/api/analyses/{analysis_id}/regeneration/plan", headers=auth("pm")).json()
    assert plan["has_inputs"] and len(plan["inputs"]["interviews"]) == 1 and len(plan["inputs"]["questions"]) == 1
    log = client.get(f"/api/analyses/{analysis_id}/logs", params={"stage": "stage2"}, headers=auth("pm")).json()
    assert {"Refinement Centre Opened", "Additional Clarification Generation",
            "Stakeholder Interview Recorded"} <= {r["agent"] for r in log}


# --------------------------------------------------------------------------
# Tests 5, 6, 7 - requirement changes, approval, selective regeneration, versions
# --------------------------------------------------------------------------
def _propose(client, auth, analysis_id, user="analyst", **body):
    return client.post(f"/api/analyses/{analysis_id}/requirement-changes", json={"reason": "Make it testable", **body},
                       headers=auth(user))


def test_requirement_change_approval_rules(client, auth):
    analysis_id = finish_stage1(client, auth, "payment_system")
    req = payload(client, auth, analysis_id)["requirements"][0]
    change = _propose(client, auth, analysis_id, change_type="edit", requirement_id=req["req_id"],
                      proposed_text="The hub shall process UPI payments within 2 seconds.").json()
    assert change["status"] == "pending_review" and change["original_text"] == req["statement"]
    url = f"/api/requirement-changes/{change['id']}/review"
    assert client.post(url, json={"action": "approve"}, headers=auth("analyst")).status_code == 403  # own change
    assert client.post(url, json={"action": "approve"}, headers=auth("auditor")).status_code == 403  # no permission
    assert client.post(url, json={"action": "reject"}, headers=auth("pm")).status_code == 400  # comment required
    rejected = client.post(url, json={"action": "reject", "comment": "Out of scope"}, headers=auth("pm")).json()
    assert rejected["status"] == "rejected"
    assert client.post(url, json={"action": "approve"}, headers=auth("pm")).status_code == 409  # already decided
    # A rejected change is never applied.
    plan = client.get(f"/api/analyses/{analysis_id}/regeneration/plan", headers=auth("pm")).json()
    assert not plan["has_inputs"]
    assert client.post(f"/api/analyses/{analysis_id}/regeneration", json={"confirm": True},
                       headers=auth("pm")).status_code == 409
    history = client.get("/api/approvals", params={"analysis_id": analysis_id}, headers=auth("auditor")).json()
    assert any(h["target_type"] == "requirement_change" and h["action"] == "reject" for h in history)


def test_selective_regeneration_creates_a_new_version(client, auth):
    analysis_id = finish_stage1(client, auth, "loan_processing")
    v1 = payload(client, auth, analysis_id)
    target = next(r for r in v1["requirements"] if r["kind"] == "functional")
    change = _propose(client, auth, analysis_id, change_type="acceptance_criteria", requirement_id=target["req_id"],
                      proposed_text="Status is visible within 5 seconds of a change.").json()
    client.post(f"/api/requirement-changes/{change['id']}/review", json={"action": "approve", "comment": "ok"},
                headers=auth("pm"))

    plan = client.get(f"/api/analyses/{analysis_id}/regeneration/plan", headers=auth("pm")).json()
    will = {w["agent"] for w in plan["will_rerun"]}
    assert will == {"Requirement Quality & Conflict Agent", "Documentation Agent"}
    retained = {r["agent"] for r in plan["retained"]}
    assert {"Requirement Extraction Agent", "Requirement Classification Agent",
            "Compliance & Regulatory Agent"} <= retained
    assert plan["changed_requirements"] == [target["req_id"]]

    assert client.post(f"/api/analyses/{analysis_id}/regeneration", json={}, headers=auth("pm")).status_code == 400
    assert client.post(f"/api/analyses/{analysis_id}/regeneration", json={"confirm": True},
                       headers=auth("auditor")).status_code == 403
    run = client.post(f"/api/analyses/{analysis_id}/regeneration", json={"confirm": True}, headers=auth("pm")).json()
    v2 = payload(client, auth, run["new_analysis_id"])
    assert v2["status"] == "completed" and v2["version"] == 2 and v2["parent_analysis_id"] == analysis_id
    status = {r["agent"]: r["status"] for r in v2["agent_runs"] if r["order"] > 0}
    for agent in ("Requirement Extraction Agent", "Requirement Classification Agent", "Compliance & Regulatory Agent",
                  "Security & Privacy Agent", "Risk Analysis Agent", "SDLC Selection Agent",
                  "SDLC Workflow Generation Agent"):
        assert status[agent] == "skipped", agent  # unaffected agents are NOT re-run
    for agent in ("Requirement Quality & Conflict Agent", "Documentation Agent", "Traceability Builder",
                  "Validation Agent", "Summary Writer"):
        assert status[agent] == "completed", agent
    assert all(r["stage"] == "stage2" for r in v2["agent_runs"])
    changed = next(r for r in v2["requirements"] if r["req_id"] == target["req_id"])
    assert any("Status is visible within 5 seconds" in c for c in changed["acceptance_criteria"])
    assert changed["original_text"] == target["original_text"]
    # Retained outputs are identical; version 1 is untouched.
    assert v2["sdlc"]["ranking"] == v1["sdlc"]["ranking"] and v2["workflow"]["phases"] == v1["workflow"]["phases"]
    assert payload(client, auth, analysis_id)["requirements"] == v1["requirements"]
    versions = client.get(f"/api/analyses/{analysis_id}/versions", headers=auth("pm")).json()
    assert [v["version"] for v in versions] == [1, 2]
    compare = client.get("/api/analysis-versions/compare", params={"base": analysis_id, "target": v2["id"]},
                         headers=auth("pm")).json()
    assert compare["sdlc"]["before"] == compare["sdlc"]["after"]
    diff = next(c for c in compare["requirements"]["changed"] if c["req_id"] == target["req_id"])
    assert "supplementary information" in diff["changes"] and diff["supplementary_added"]
    # Applied inputs are not applied twice; only the latest version can be refined.
    assert client.post(f"/api/analyses/{v2['id']}/regeneration", json={"confirm": True},
                       headers=auth("pm")).status_code == 409
    assert client.get(f"/api/analyses/{analysis_id}/refinement",
                      headers=auth("pm")).json()["analysis"]["is_latest"] is False


def test_wording_change_reruns_dependent_agents_and_new_version_needs_approval(client, auth):
    analysis_id = finish_stage1(client, auth, "digital_banking")
    v1 = payload(client, auth, analysis_id)
    client.post(f"/api/analyses/{analysis_id}/sdlc/review", json={"action": "approve"}, headers=auth("pm"))
    conflict = next(f for f in v1["findings"] if f["issue_type"] in ("conflict", "stakeholder_conflict"))
    req_id = conflict["requirement_ids"][0]
    change = _propose(client, auth, analysis_id, change_type="resolve_conflict", requirement_id=req_id,
                      proposed_text="Customer sessions shall be terminated after 30 minutes of inactivity.").json()
    client.post(f"/api/requirement-changes/{change['id']}/review", json={"action": "approve", "comment": "agreed"},
                headers=auth("pm"))
    obsolete = _propose(client, auth, analysis_id, change_type="obsolete",
                        requirement_id=conflict["requirement_ids"][1], reason="Superseded").json()
    client.post(f"/api/requirement-changes/{obsolete['id']}/review", json={"action": "approve", "comment": "ok"},
                headers=auth("pm"))
    new = _propose(client, auth, analysis_id, change_type="new", new_kind="non_functional",
                   proposed_text="The system shall log every administrative action in a tamper-evident audit trail.",
                   reason="Audit requirement").json()
    client.post(f"/api/requirement-changes/{new['id']}/review", json={"action": "approve", "comment": "ok"},
                headers=auth("pm"))
    run = client.post(f"/api/analyses/{analysis_id}/regeneration", json={"confirm": True}, headers=auth("pm")).json()
    v2 = payload(client, auth, run["new_analysis_id"])
    assert v2["status"] in ("completed", "completed_with_warnings")
    ids = {r["req_id"]: r for r in v2["requirements"]}
    assert ids[req_id]["status"] == "modified" and "30 minutes" in ids[req_id]["statement"]
    assert ids[conflict["requirement_ids"][1]]["status"] == "obsolete"
    added = [r for r in v2["requirements"] if r["source"].startswith("Requirement change")]
    assert len(added) == 1 and added[0]["categories"]  # new requirement was classified
    assert not any(set(conflict["requirement_ids"]) == set(f["requirement_ids"]) for f in v2["findings"]
                   if f["issue_type"] in ("conflict", "stakeholder_conflict"))  # conflict gone
    assert v2["baseline_status"] == "draft"
    sdlc_run = next(r for r in v2["agent_runs"] if r["agent"] == "SDLC Selection Agent")
    if sdlc_run["status"] == "completed":  # a re-run recommendation needs a new human approval
        assert v2["sdlc"]["status"] == "pending_approval"
    assert all(c["status"] != "fail" for c in v2["validation_report"])


# --------------------------------------------------------------------------
# Test 7 - human approval of high-risk acceptance
# --------------------------------------------------------------------------
def test_risk_acceptance_requires_authorised_role(client, auth):
    analysis_id = finish_stage1(client, auth, "payment_system")
    risks = payload(client, auth, analysis_id)["risks"]
    high = next(r for r in risks if r["severity"] in ("High", "Critical") and r["category"] != "security")
    url = f"/api/risks/{high['id']}/review"
    assert client.post(url, json={"action": "accept", "comment": "Accepted with mitigation"}, headers=auth("analyst")).status_code == 403
    assert client.post(url, json={"action": "accept", "comment": "Mitigated by phased rollout"},
                       headers=auth("pm")).json()["status"] == "accepted"
    security = next((r for r in risks if r["severity"] in ("High", "Critical") and r["category"] == "security"), None)
    if security:
        assert client.post(f"/api/risks/{security['id']}/review", json={"action": "accept", "comment": "Accepted with mitigation"},
                           headers=auth("pm")).status_code == 403
        assert client.post(f"/api/risks/{security['id']}/review", json={"action": "accept", "comment": "Accepted with mitigation"},
                           headers=auth("security")).status_code == 200


def test_openai_question_generation_is_validated(client, auth, openai_mode):
    analysis_id = submit(client, auth, "payment_system")
    assert "QuestionOutput" in openai_mode.schemas_called()  # the real OpenAI client path was used
    qs = questions(client, auth, analysis_id)
    assert len(qs) <= 12  # capped although the model returned 14
    assert qs[0]["priority"] == "Critical"  # re-ordered by priority
    assert all(q["generated_by"].startswith("openai:") for q in qs)
    invented = next(q for q in qs if q["question"] == "Question with an invented requirement?")
    assert invented["related_requirements"] == [] and invented["topic"] == ""  # unknown ID and topic dropped
    assert len({q["question"] for q in qs}) == len(qs)  # duplicate removed
    repaired = [q for q in qs if q["question"] == "Pick one?"]
    assert not repaired or repaired[0]["answer_format"] == "short_text"  # a 1-option choice is not a choice
    log = next(r for r in payload(client, auth, analysis_id)["agent_runs"]
               if r["agent"] == "Clarification Question Generator")
    assert log["mode"] == "llm+rules" and log["llm_calls"] and log["status"] == "completed"


def test_openai_failure_falls_back_to_rule_questions(client, auth, openai_mode):
    openai_mode.fail_schemas = {"QuestionOutput"}
    analysis_id = submit(client, auth, "payment_system")
    qs = questions(client, auth, analysis_id)
    assert qs and all(q["generated_by"] == "rules" for q in qs)
    log = next(r for r in payload(client, auth, analysis_id)["agent_runs"]
               if r["agent"] == "Clarification Question Generator")
    assert log["status"] == "partial" and "rule-based question templates" in log["message"]
    assert not any(c["ok"] for c in log["llm_calls"])  # the failed call is logged, not reported as success


def test_interrupted_clarified_analysis_returns_to_the_clarification_centre(client, auth, db):
    from app.main import recover_interrupted
    from app.models import Analysis

    analysis_id = submit(client, auth, "loan_processing")
    analysis = db.get(Analysis, analysis_id)
    analysis.status, analysis.stage1_status = "running", "analysing"  # simulate a crash mid-pipeline
    db.commit()
    recover_interrupted(db)
    db.refresh(analysis)
    assert analysis.status == "awaiting_clarification" and "saved" in analysis.error
    assert client.post(f"/api/analyses/{analysis_id}/continue", headers=auth("analyst")).status_code == 200
    assert payload(client, auth, analysis_id)["status"] == "completed"
