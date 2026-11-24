"""Agent 5 - Security and Privacy Agent.

* Coverage check (deterministic): for 10 security areas (authentication,
  authorisation, access control, encryption, data protection, retention,
  transaction integrity, audit logging, secure integration, threat management)
  find the requirements that address the area. An area is "missing" when no
  requirement covers it and "partial" when it is only covered by vague
  requirements (ones the Quality Agent flagged as ambiguous).
* Missing areas become security gaps with a recommended safeguard.
* Threats (STRIDE) and privacy considerations: rule templates in demonstration
  mode; produced by OpenAI (validated) in real mode.
"""
from typing import Literal

from pydantic import BaseModel

from app import llm
from app.agents import finding, valid_ids
from app.rules import LOG_WORDS, SECURITY_AREAS, find_keywords
from app.security import mask_sensitive, wrap_untrusted

_CRITICAL_AREAS = {"authentication", "encryption", "audit_logging", "access_control", "transaction_integrity"}

# (keywords that trigger the threat, STRIDE category, title, safeguard)
_THREAT_RULES = [
    (["login", "authenticat", "password", "otp", "pin"], "Spoofing", "Account takeover via credential "
     "stuffing, SIM-swap or OTP interception", "MFA with device binding, login velocity limits and SIM-swap checks."),
    (["transfer", "payment", "transaction", "upi", "imps", "neft", "disburse"], "Tampering",
     "Manipulation or replay of financial transactions", "Signed requests, idempotency keys and server-side "
     "validation of amounts and beneficiaries."),
    (["transaction", "approve", "payment", "transfer"], "Repudiation", "Customers or staff deny having performed "
     "a transaction", "Tamper-evident audit trail linking each transaction to an authenticated identity."),
    (["personal data", "aadhaar", "pan", "account", "kyc", "statement", "customer data"], "Information disclosure",
     "Leakage of personal and financial data", "Encryption at rest, field-level masking and strict access control."),
    (["api", "gateway", "third-party", "third party", "integrat", "cbs", "core banking", "bureau"],
     "Information disclosure", "Compromise through insecure third-party or legacy interfaces",
     "Mutual TLS/OAuth2 on APIs, input validation and network segmentation for legacy systems."),
    (["available", "availability", "24x7", "24/7", "uptime", "mobile app", "online"], "Denial of service",
     "Service disruption through volumetric or application-layer attacks", "WAF, DDoS protection, rate limiting "
     "and autoscaling with tested failover."),
    (["admin", "role", "officer", "staff", "maker-checker", "approve"], "Elevation of privilege",
     "Privilege escalation by internal users or compromised accounts", "Least privilege RBAC, maker-checker and "
     "privileged-access monitoring."),
]

# (keywords, title, recommendation)
_PRIVACY_RULES = [
    (["aadhaar", "pan", "kyc", "date of birth", "address", "mobile number", "biometric", "personal data"],
     "Sensitive personal identifiers are processed", "Apply purpose limitation, masking/tokenisation and "
     "consent records for Aadhaar, PAN and other identifiers."),
    ([*LOG_WORDS, "audit", "monitor"], "Logs and monitoring data may contain personal data",
     "Mask personal and card data in logs and restrict log access."),
    (["credit bureau", "cibil", "third-party", "third party", "share", "partner", "gateway"],
     "Personal data is shared with third parties", "Define data-sharing agreements, collect consent and "
     "minimise the fields shared."),
    (["score", "model", "machine learning", " ml ", "analytics", "profil", "automated decision"],
     "Automated decisions or profiling based on personal data", "Provide explanations of automated decisions, "
     "human review of adverse outcomes and periodic bias testing."),
    (["retain", "retention", "delete", "archive", "years"], "Retention of personal data",
     "Align the retention period with legal obligations and delete or anonymise data afterwards."),
]


def coverage(requirements: list[dict], ambiguous_ids: set[str]) -> list[dict]:
    result = []
    for key, area in SECURITY_AREAS.items():
        ids = [r["req_id"] for r in requirements if find_keywords(r["statement"], area["keywords"])]
        if not ids:
            status = "missing"
        elif all(i in ambiguous_ids for i in ids):
            status = "partial"
        else:
            status = "covered"
        result.append({"area": key, "label": area["label"], "status": status, "requirement_ids": ids,
                       "safeguard": area["safeguard"]})
    return result


def gap_findings(cov: list[dict], domain: str) -> list[dict]:
    results = []
    for area in cov:
        if area["status"] == "covered":
            continue
        critical = area["area"] in _CRITICAL_AREAS and not (
            area["area"] == "transaction_integrity" and domain not in ("payments", "digital_banking", "lending"))
        missing = area["status"] == "missing"
        results.append(finding(
            "security_gap", area["area"], "High" if critical and missing else "Medium",
            f"{'Missing' if missing else 'Weak'} security requirement: {area['label']}", area["requirement_ids"],
            explanation=("No submitted requirement addresses this area." if missing else
                         "The area is only covered by vague requirements without specific controls."),
            suggestion=area["safeguard"], question=f"What {area['label'].lower()} controls are required?"))
    return results


def rule_threats_and_privacy(requirements: list[dict], domain: str) -> list[dict]:
    results = []
    severe_domain = domain in ("payments", "digital_banking", "lending")
    for keywords, stride, title, safeguard in _THREAT_RULES:
        ids = [r["req_id"] for r in requirements if find_keywords(r["statement"], keywords)]
        if ids:
            results.append(finding("threat", stride, "High" if severe_domain else "Medium", title, ids[:12],
                                   explanation=f"STRIDE category: {stride}. Triggered by {len(ids)} related "
                                               "requirement(s).", suggestion=safeguard))
    for keywords, title, recommendation in _PRIVACY_RULES:
        ids = [r["req_id"] for r in requirements if find_keywords(f" {r['statement']} ", keywords)]
        if ids:
            results.append(finding("privacy", "privacy_consideration", "Medium", title, ids[:12],
                                   explanation=f"{len(ids)} requirement(s) involve this processing.",
                                   suggestion=recommendation))
    return results


class Threat(BaseModel):
    title: str
    description: str
    stride_category: Literal["Spoofing", "Tampering", "Repudiation", "Information disclosure",
                             "Denial of service", "Elevation of privilege"]
    severity: Literal["Low", "Medium", "High", "Critical"]
    requirement_ids: list[str]
    safeguard: str


class PrivacyConsideration(BaseModel):
    title: str
    description: str
    requirement_ids: list[str]
    recommendation: str


class SecurityOutput(BaseModel):
    threats: list[Threat]
    privacy_considerations: list[PrivacyConsideration]


_INSTRUCTIONS = """Perform a security and privacy analysis of this financial application.
threats: the most important STRIDE threats (max 10) with the related requirement IDs and a concrete \
safeguard. privacy_considerations: personal-data processing concerns (consent, minimisation, sharing, \
retention, profiling, masking) with recommendations (max 8). Use only requirement IDs from the input."""


def run(ctx: dict) -> tuple[str, str]:
    requirements, domain = ctx["requirements"], ctx["project"]["domain"]
    ambiguous = {i for f in ctx["findings"] if f["issue_type"] == "ambiguity" for i in f["requirement_ids"]}
    cov = coverage(requirements, ambiguous)
    ctx["security_coverage"] = cov
    findings = gap_findings(cov, domain)
    status, note = "completed", ""

    if llm.enabled() and requirements:
        relevant = [r for r in requirements if {"security", "privacy", "integration", "data_management",
                                                "auditability", "regulatory"} & set(r["categories"])] or requirements
        listing = "\n".join(f"{r['req_id']}: {mask_sensitive(r['statement'])[0]}" for r in relevant[:150])
        gaps = ", ".join(a["label"] for a in cov if a["status"] != "covered") or "none"
        data = (f"Project: {ctx['project']['name']} ({domain}). Security areas without adequate "
                f"requirements: {gaps}\n\n" + wrap_untrusted("requirements", listing))
        try:
            output = llm.structured("security and privacy analysis", _INSTRUCTIONS, data, SecurityOutput,
                                    ctx["llm_log"])
            for t in output.threats:
                findings.append(finding("threat", t.stride_category, t.severity, t.title,
                                        valid_ids(ctx, "security", t.requirement_ids), explanation=t.description,
                                        suggestion=t.safeguard, generated_by=llm.model_label()))
            for p in output.privacy_considerations:
                findings.append(finding("privacy", "privacy_consideration", "Medium", p.title,
                                        valid_ids(ctx, "security", p.requirement_ids), explanation=p.description,
                                        suggestion=p.recommendation, generated_by=llm.model_label()))
            note = " Threats and privacy considerations by OpenAI."
        except llm.LLMError as exc:
            findings += rule_threats_and_privacy(requirements, domain)
            status, note = "partial", f" OpenAI failed ({exc}); rule-based threat templates used."
    else:
        findings += rule_threats_and_privacy(requirements, domain)

    ctx["findings"].extend(findings)
    missing = sum(1 for a in cov if a["status"] != "covered")
    return status, (f"{10 - missing}/10 security areas covered; {missing} gaps, "
                    f"{sum(1 for f in findings if f['kind'] == 'threat')} threats identified.{note}")
