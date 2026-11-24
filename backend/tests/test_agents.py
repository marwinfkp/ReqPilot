"""Unit tests for the rule-based behaviour of the individual agents (demonstration mode).

Most tests feed a few hand-written requirement lines through the real agents
(using tests.helpers.run_until) and check the findings they produce.
"""
import pytest

from app.agents import classification, documentation, extraction, quality, risk, security_privacy, validation
from app.agents import workflow as workflow_agent
from app.agents.sdlc_selection import MODELS
from tests.helpers import findings_of, new_ctx, req_by_text, run_until

# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
EXTRACTION_TEXT = """Accounts and onboarding:
Note: the bank runs a legacy core banking system with nightly batch interfaces.
- [Customer] The app shall allow a new customer to open a savings account using e-KYC.
- [Compliance Officer] The system must capture explicit customer consent before Aadhaar authentication.
Security Team: Customer sessions shall be terminated after 5 minutes of inactivity.
1. The system should display the account balance on the home screen.
SECURITY
Context: the backlog will change frequently.
The platform shall be hosted only in India.
Transfers to a new beneficiary shall be limited to INR 25,000 for the first 24 hours.
"""


def test_segment_input_recognises_headings_notes_and_sources():
    lines = extraction.segment_input([{"name": "Pasted requirements", "text": EXTRACTION_TEXT}])
    by_start = {line["body"][:20]: line for line in lines}

    heading = by_start["Accounts and onboard"]
    assert heading["is_heading"] and not heading["is_requirement"]
    assert by_start["SECURITY"]["is_heading"]
    assert by_start["Note: the bank runs "]["is_note"] and not by_start["Note: the bank runs "]["is_requirement"]
    assert by_start["Context: the backlog"]["is_note"]

    tagged = by_start["The app shall allow "]
    assert tagged["source"] == "Customer" and tagged["tagged"] and tagged["is_requirement"]
    assert tagged["section"] == "Accounts and onboarding"
    prefixed = by_start["Customer sessions sh"]
    assert prefixed["source"] == "Security Team" and prefixed["tagged"]
    untagged = by_start["The system should di"]  # "1." numbering removed
    assert untagged["source"] == "Pasted requirements" and not untagged["tagged"]


def test_extraction_assigns_kinds_and_domain_ids(db):
    ctx = new_ctx(db, EXTRACTION_TEXT, domain="digital_banking")
    status, _ = extraction.run(ctx)
    assert status == "completed"

    statements = [r["statement"] for r in ctx["requirements"]]
    assert len(statements) == 6 == ctx["submitted_count"]
    assert not any(s.startswith(("Note", "Context", "Accounts")) for s in statements)

    consent = req_by_text(ctx, "explicit customer consent")
    assert consent["source"] == "Compliance Officer" and consent["has_stakeholder_source"]
    assert consent["original_text"].startswith("- [Compliance Officer]")  # original kept verbatim
    assert consent["statement"].startswith("The system must capture")

    assert req_by_text(ctx, "hosted only in India")["kind"] == "constraint"
    assert req_by_text(ctx, "INR 25,000")["kind"] == "business_rule"
    assert req_by_text(ctx, "open a savings account")["kind"] == "functional"

    ids = [r["req_id"] for r in ctx["requirements"]]
    assert len(ids) == len(set(ids))
    assert "FR-BNK-001" in ids and "CON-BNK-001" in ids and "BR-BNK-001" in ids
    assert all(i.split("-")[1] == "BNK" for i in ids)


def test_ids_use_the_domain_code_and_gen_for_unknown_domains():
    reqs = [{"kind": "functional"}, {"kind": "non_functional"}, {"kind": "functional"}]
    extraction.assign_ids(reqs, "payments")
    assert [r["req_id"] for r in reqs] == ["FR-PAY-001", "NFR-PAY-001", "FR-PAY-002"]
    extraction.assign_ids(reqs, "space_tourism")
    assert reqs[0]["req_id"] == "FR-GEN-001"


@pytest.mark.parametrize("text, kind", [
    ("The application shall be deployed on the bank's private cloud.", "constraint"),
    ("The maximum transfer limit shall be INR 1,00,000 per day.", "business_rule"),
    ("The system shall respond within 2 seconds for 95% of requests.", "non_functional"),
    ("The system shall allow customers to transfer funds to saved beneficiaries.", "functional"),
])
def test_detect_kind(text, kind):
    assert extraction.detect_kind(text) == kind


def test_long_paragraphs_are_split_into_sentences():
    paragraph = " ".join(["The system shall record every login attempt with a timestamp and device ID."] * 3
                         + ["The system shall notify customers by SMS about every debit transaction promptly."] * 3)
    assert len(paragraph) >= 400
    lines = extraction.segment_input([{"name": "doc.pdf", "text": paragraph}])
    assert len(lines) == 6 and all(line["is_requirement"] for line in lines)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def classify(statement: str, kind: str = "functional", req_id: str = "FR-BNK-001") -> dict:
    req = {"req_id": req_id, "statement": statement, "kind": kind}
    classification.classify_with_rules(req)
    return req


def test_classification_is_multi_label():
    req = classify("The system shall encrypt customer personal data and record every access in a "
                   "tamper-evident audit trail.", kind="non_functional")
    assert {"non_functional", "security", "privacy", "auditability"} <= set(req["categories"])
    assert 0.55 <= req["confidence"] <= 0.75


@pytest.mark.parametrize("statement, priority", [
    ("The system shall display the account balance.", "High"),
    ("The app must show the last ten transactions.", "High"),
    ("The system should display a welcome banner.", "Medium"),
    ("Customers should be able to log in with an OTP.", "High"),  # security -> always High
    ("The app may show personalised product offers.", "Low"),
])
def test_priority_rules(statement, priority):
    assert classify(statement)["priority"] == priority


def test_dependencies_are_detected_from_referenced_ids():
    req = classify("The system shall send the receipt described in FR-BNK-002 after BR-BNK-001 is checked.",
                   req_id="FR-BNK-005")
    assert req["dependencies"] == ["BR-BNK-001", "FR-BNK-002"]


# --------------------------------------------------------------------------
# Quality and conflicts
# --------------------------------------------------------------------------
def quality_ctx(db, text):
    return run_until(db, text, "Quality")


def test_weak_words_are_reported_as_ambiguity(db):
    ctx = quality_ctx(db, "[Product Owner] The system should process transfers fast.\n"
                          "[Customer] The onboarding journey should be simple and user-friendly.")
    ambiguities = findings_of(ctx, "ambiguity")
    assert len(ambiguities) == 2
    fast = next(f for f in ambiguities if "fast" in f["evidence"])
    assert fast["severity"] == "High"  # "fast" makes it a performance requirement
    assert "measurable response time" in fast["suggestion"]
    assert fast["question"]


def test_tbd_is_reported_as_incompleteness(db):
    ctx = quality_ctx(db, "[Business Analyst] The charge for IMPS transfers above INR 1,00,000 shall be TBD.")
    (incomplete,) = findings_of(ctx, "incompleteness")
    assert incomplete["severity"] == "High" and incomplete["evidence"] == "tbd"


def test_absolute_targets_are_reported_as_infeasible(db):
    ctx = quality_ctx(db, "[Operations] The platform shall guarantee 100% uptime for all customers.")
    assert findings_of(ctx, "infeasibility")


def test_numeric_conflict_session_timeout_5_vs_30_minutes(db):
    ctx = quality_ctx(db, "[Security Team] Customer sessions shall be terminated automatically after 5 minutes "
                          "of inactivity.\n"
                          "[Product Owner] To avoid frustrating customers, the session idle timeout must be "
                          "30 minutes.")
    (conflict,) = findings_of(ctx, "stakeholder_conflict")  # different stakeholders
    assert "session timeout" in conflict["title"]
    assert conflict["severity"] == "High" and len(conflict["requirement_ids"]) == 2
    assert "Security Team vs Product Owner" in conflict["explanation"]


def test_same_stakeholder_conflict_is_a_plain_conflict(db):
    ctx = quality_ctx(db, "[Security Team] Customer sessions shall be terminated automatically after 5 minutes "
                          "of inactivity.\n"
                          "[Security Team] The session idle timeout must be 30 minutes.")
    assert findings_of(ctx, "conflict") and not findings_of(ctx, "stakeholder_conflict")


def test_near_identical_requirements_with_different_numbers_are_a_conflict(db):
    ctx = quality_ctx(db, "[Security Team] Customer sessions shall expire after 5 minutes of inactivity.\n"
                          "[Security Team] Customer sessions shall expire after 15 minutes of inactivity.")
    assert findings_of(ctx, "conflict")
    assert not findings_of(ctx, "duplicate")


def test_availability_target_conflicts_with_maintenance_window(db):
    ctx = quality_ctx(db, "[Operations] The platform shall be available 99.99% of the time, 24x7.\n"
                          "[Operations] A maintenance window of 4 hours every Sunday night shall be reserved, "
                          "during which digital banking will be unavailable.")
    (conflict,) = findings_of(ctx, "conflict")
    assert "availability" in conflict["title"]


def test_negated_near_identical_statements_are_a_contradiction(db):
    ctx = quality_ctx(db, "[Security Team] The system shall store card numbers in application logs.\n"
                          "[Auditor] The system shall not store card numbers in application logs.")
    (conflict,) = findings_of(ctx, "stakeholder_conflict")
    assert conflict["title"].startswith("Contradiction")


def test_duplicates_are_detected(db):
    ctx = quality_ctx(db, "[Customer] Customers shall receive an SMS alert for every debit transaction.\n"
                          "[Operations] Customers shall receive an SMS alert for each debit transaction.")
    (duplicate,) = findings_of(ctx, "duplicate")
    assert duplicate["severity"] == "Medium"
    assert not findings_of(ctx, "conflict") and not findings_of(ctx, "stakeholder_conflict")


def test_unrelated_numbers_are_not_conflicts():
    assert quality._numeric_conflict("OTP shall expire after 3 minutes.",
                                     "Statements shall be generated within 5 days of month end.") is None


def test_quantities_are_normalised_to_a_common_unit():
    assert ("time", 300.0) in quality.quantities("after 5 minutes")
    assert ("time", 300.0) in quality.quantities("after 300 seconds")
    assert ("INR", 200000.0) in quality.quantities("up to INR 2 lakh")
    assert ("frequency", 30) in quality.quantities("reports are sent monthly")


def test_missing_source_and_undefined_terms(db):
    ctx = quality_ctx(db, "The system shall publish interaction events to the CDP within 5 minutes.\n"
                          "[Architect] All requests to the core banking system shall pass through the ESB.")
    (missing,) = findings_of(ctx, "missing_source")
    assert len(missing["requirement_ids"]) == 1
    undefined = {f["evidence"] for f in findings_of(ctx, "undefined_term")}
    assert undefined == {"CDP", "ESB"}

    defined = quality_ctx(db, "[Architect] Events shall be published to the Customer Data Platform (CDP) "
                              "within 5 minutes.")
    assert not findings_of(defined, "undefined_term")


# --------------------------------------------------------------------------
# Security and privacy
# --------------------------------------------------------------------------
SECURITY_TEXT = """[Security Team] Customers shall authenticate with a password and a one-time password (OTP).
[Security Team] All data in transit shall be encrypted using TLS 1.2 or higher.
[Customer] Customers shall be able to transfer money to saved beneficiaries.
"""


def test_security_coverage_and_gaps(db):
    ctx = run_until(db, SECURITY_TEXT, "Security", domain="payments")
    coverage = {area["area"]: area for area in ctx["security_coverage"]}
    assert len(coverage) == 10
    assert coverage["authentication"]["status"] == "covered"
    assert coverage["encryption"]["status"] == "covered"
    assert coverage["audit_logging"]["status"] == "missing"

    gaps = {f["issue_type"]: f for f in findings_of(ctx, kind="security_gap")}
    assert set(gaps) == {a for a, c in coverage.items() if c["status"] != "covered"}
    assert gaps["audit_logging"]["severity"] == "High"  # critical area, missing
    assert gaps["transaction_integrity"]["severity"] == "High"  # critical for payments
    assert gaps["data_retention"]["severity"] == "Medium"  # not a critical area
    assert gaps["audit_logging"]["suggestion"]  # recommended safeguard


def test_a_login_requirement_does_not_cover_audit_logging(db):
    ctx = run_until(db, "[Security Team] Customers shall log in with a password and a one-time password.",
                    "Security", domain="payments")
    coverage = {area["area"]: area["status"] for area in ctx["security_coverage"]}
    assert coverage["authentication"] == "covered"
    assert coverage["audit_logging"] == "missing"


def test_transaction_integrity_is_not_critical_outside_money_movement_domains(db):
    ctx = run_until(db, SECURITY_TEXT, "Security", domain="fraud_detection")
    gaps = {f["issue_type"]: f for f in findings_of(ctx, kind="security_gap")}
    assert gaps["transaction_integrity"]["severity"] == "Medium"


def test_area_covered_only_by_vague_requirements_is_partial(db):
    ctx = run_until(db, "[Architect] All data shall be encrypted properly where possible.", "Security")
    coverage = {area["area"]: area["status"] for area in ctx["security_coverage"]}
    assert coverage["encryption"] == "partial"
    weak = [f for f in findings_of(ctx, kind="security_gap") if f["issue_type"] == "encryption"]
    assert weak[0]["title"].startswith("Weak") and weak[0]["severity"] == "Medium"


def test_rule_threats_follow_stride(db):
    ctx = run_until(db, SECURITY_TEXT, "Security", domain="payments")
    stride = {f["issue_type"] for f in findings_of(ctx, kind="threat")}
    assert {"Spoofing", "Tampering", "Repudiation"} <= stride
    assert all(f["severity"] == "High" for f in findings_of(ctx, kind="threat"))  # payments = severe domain


# --------------------------------------------------------------------------
# Risk scoring
# --------------------------------------------------------------------------
@pytest.mark.parametrize("score, severity", [(1, "Low"), (4, "Low"), (5, "Medium"), (9, "Medium"),
                                             (10, "High"), (15, "High"), (16, "Critical"), (25, "Critical")])
def test_risk_severity_bands(score, severity):
    assert risk.severity_for(score) == severity


@pytest.mark.parametrize("likelihood, impact, score, severity", [
    (1, 1, 1, "Low"), (2, 2, 4, "Low"), (1, 5, 5, "Medium"), (3, 3, 9, "Medium"),
    (2, 5, 10, "High"), (3, 5, 15, "High"), (4, 4, 16, "Critical"), (5, 5, 25, "Critical"),
    (9, 0, 5, "Medium"),  # out-of-range inputs are clamped to 1..5 -> 5 x 1
])
def test_make_risk_is_likelihood_times_impact(likelihood, impact, score, severity):
    r = risk.make_risk("A risk", "technical", likelihood, impact, "Mitigate", [])
    assert 1 <= r["likelihood"] <= 5 and 1 <= r["impact"] <= 5
    assert r["score"] == r["likelihood"] * r["impact"] == score
    assert r["severity"] == severity


def test_rule_risks_and_requirement_risk_levels(db):
    ctx = run_until(db, SECURITY_TEXT, "Risk", domain="payments", schedule="Go-live in 4 months")
    risks = ctx["risks"]
    assert [r["risk_id"] for r in risks] == [f"RISK-{i:02d}" for i in range(1, len(risks) + 1)]
    assert all(a["score"] >= b["score"] for a, b in zip(risks, risks[1:]))  # sorted by score

    by_category = {r["category"]: r for r in risks}
    security_risk = by_category["security"]
    assert security_risk["impact"] == 5  # payments = high-impact domain
    assert security_risk["likelihood"] == 4  # two or more security gaps
    assert any("schedule" in r["description"].lower() for r in risks)  # 4 months <= 6
    assert by_category["operational"]["description"].startswith("No operational monitoring")

    login = req_by_text(ctx, "authenticate with a password")
    assert login["risk_level"] == "High"  # High priority + security category


# --------------------------------------------------------------------------
# Workflow customisation
# --------------------------------------------------------------------------
PAYMENT_TEXT = """[Security Team] Customers shall authenticate with an OTP before every payment.
[Operations] The payment hub shall be available 99.95% of the time.
[Architect] The hub shall integrate with the NPCI switch through a secure API.
"""


def test_payments_domain_adds_transaction_integrity_testing(db):
    ctx = run_until(db, PAYMENT_TEXT, "Workflow", domain="payments")
    wf = ctx["workflow"]
    assert wf["sdlc_model"] == ctx["sdlc"]["recommended_model"]
    verify_phases = [p for p in wf["phases"] if p["stage"] == "verify"]
    assert any(item.startswith("Transaction integrity testing") for item in verify_phases[-1]["testing"])
    reasons = {c["reason"] for c in wf["customisations"]}
    assert {"Financial transactions", "Security requirements present", "Availability targets",
            "External integrations"} <= reasons


def test_no_transaction_testing_without_money_movement(db):
    ctx = run_until(db, "[Analyst] The portal shall generate a monthly regulatory report as a PDF document.",
                    "Workflow", domain="regulatory_reporting")
    all_testing = [t for p in ctx["workflow"]["phases"] for t in p["testing"]]
    assert not any(t.startswith("Transaction integrity testing") for t in all_testing)


@pytest.mark.parametrize("model", MODELS)
def test_every_model_workflow_has_complete_phases_and_approval_gates(db, model):
    ctx = run_until(db, PAYMENT_TEXT, "SDLC Selection", domain="payments")
    phases, _ = workflow_agent.build_workflow(ctx, model)
    assert [p["order"] for p in phases] == list(range(1, len(phases) + 1))
    for phase in phases:
        assert phase["entry_criteria"] and phase["exit_criteria"] and phase["roles"] and phase["deliverables"]
    gates = [g["name"] for p in phases if p["stage"] in ("plan", "accept", "release") for g in p["approval_gates"]]
    assert "Requirement baseline approval" in gates
    assert "Compliance sign-off" in gates
    assert "Production-readiness approval" in gates
    # Payments has regulatory criticality >= 4 -> an extra regulatory release gate.
    assert "Regulatory readiness confirmation" in gates


def test_v_model_test_levels_verify_their_design_phases(db):
    ctx = run_until(db, PAYMENT_TEXT, "SDLC Selection", domain="payments")
    phases, _ = workflow_agent.build_workflow(ctx, "V-Model")
    names = {p["name"] for p in phases}
    verifying = {p["name"]: p["verifies"] for p in phases if p["verifies"]}
    assert verifying["Acceptance & Compliance Verification"] == "Requirements & Regulatory Analysis"
    assert set(verifying.values()) <= names


# --------------------------------------------------------------------------
# Documentation and traceability
# --------------------------------------------------------------------------
def test_user_story_template():
    req = {"req_id": "FR-BNK-001", "statement": "The system shall allow customers to download statements "
           "within 5 seconds.", "source": "Customer", "kind": "functional",
           "categories": ["functional", "performance"]}
    story, criteria, tests = documentation.rule_story(req)
    assert story.startswith("As a Customer, I want the system to download statements")
    assert any("5 seconds" in c for c in criteria)  # the number is preserved
    assert "Load test at peak volume against the stated target" in tests


def test_traceability_record_hand_computed():
    """One security requirement with a story and criteria but no test and no control:
    expected links = story, criteria, test, workflow phase, security control (5);
    missing = test recommendation + security control (2) -> coverage 1 - 2/5 = 0.6."""
    ctx = {"requirements": [{"req_id": "FR-BNK-001", "original_text": "- [Security Team] Use MFA.",
                             "source": "Security Team", "categories": ["functional", "security"],
                             "user_story": "As a customer ...", "acceptance_criteria": ["Given ..."],
                             "test_recommendations": []}],
           "mappings": [], "security_coverage": [],
           "workflow": {"phases": [{"name": "System Testing", "stage": "verify", "key_requirements": []}]}}
    documentation.run_traceability(ctx)
    (record,) = ctx["traceability"]
    assert record["missing_links"] == ["test recommendation", "security control"]
    assert record["coverage"] == 0.6
    assert record["workflow_phases"] == ["System Testing"]  # falls back to the last verify phase
    assert record["source_statement"] == "- [Security Team] Use MFA."


def test_traceability_covers_every_requirement(db):
    ctx = run_until(db, PAYMENT_TEXT, "Traceability", domain="payments")
    assert [t["requirement_id"] for t in ctx["traceability"]] == [r["req_id"] for r in ctx["requirements"]]
    for record in ctx["traceability"]:
        assert 0 <= record["coverage"] <= 1
        assert record["user_story"] and record["acceptance_criteria"] and record["tests"]


# --------------------------------------------------------------------------
# Validation agent: it must notice inconsistent results
# --------------------------------------------------------------------------
def test_validation_detects_inconsistencies(db):
    ctx = run_until(db, PAYMENT_TEXT, "Traceability", domain="payments")
    validation.run(ctx)
    assert all(c["status"] != "fail" for c in ctx["validation"])

    ctx["requirements"].append(dict(ctx["requirements"][0]))  # duplicate ID
    ctx["findings"].append({"kind": "quality", "issue_type": "ambiguity", "severity": "Low", "title": "x",
                            "requirement_ids": ["FR-PAY-999"]})  # reference to a requirement that does not exist
    ctx["workflow"]["sdlc_model"] = "Waterfall" if ctx["sdlc"]["recommended_model"] != "Waterfall" else "Agile"
    validation.run(ctx)
    status = {c["check"]: c["status"] for c in ctx["validation"]}
    assert status["Unique requirement IDs"] == "fail"
    assert status["Reference integrity"] == "fail"
    assert status["Workflow completeness"] == "fail"
