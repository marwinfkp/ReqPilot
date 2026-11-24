"""Rule-based financial requirement-engineering knowledge shared by the agents.

These keyword dictionaries power ReqPilot's deterministic baseline (used on its
own in demonstration mode, and combined with OpenAI output in real mode).
Keywords are matched as word prefixes, so "authenticat" matches
"authenticate", "authentication", ...
"""
import re
from functools import lru_cache

# --------------------------------------------------------------------------
# Generic text helpers
# --------------------------------------------------------------------------
STOPWORDS = set("""a an the and or of to in on for with by from at as is are be been being shall
must should will would may can could this that these those it its their there all any each
system application app user users able provide provides support supports allow allows using
use via within into than then such per not no only also if when which who whom what so""".split())


def tokens(text: str, ignore_numbers: bool = False) -> set[str]:
    """Content words of a text: lower-case, no stop-words, crude plural stemming."""
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words
            if w not in STOPWORDS and len(w) > 1 and not (ignore_numbers and w[0].isdigit())}


def jaccard(a: str, b: str, ignore_numbers: bool = False) -> float:
    """Share of content words two texts have in common (0..1)."""
    ta, tb = tokens(a, ignore_numbers), tokens(b, ignore_numbers)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


@lru_cache(maxsize=512)
def _keyword_regex(keywords: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in keywords) + ")", re.IGNORECASE)


def find_keywords(text: str, keywords: list[str] | tuple[str, ...]) -> list[str]:
    """Distinct keywords (prefix match at a word start) present in `text`."""
    if not keywords:
        return []
    found = _keyword_regex(tuple(keywords)).findall(text or "")
    return sorted({f.lower() for f in found})


def has_number(text: str) -> bool:
    return bool(re.search(r"\d", text))


# --------------------------------------------------------------------------
# Financial domains supported by ReqPilot  (key -> label, ID code)
# --------------------------------------------------------------------------
DOMAINS = {
    "digital_banking": ("Digital & Mobile Banking", "BNK"),
    "payments": ("Payment Processing", "PAY"),
    "lending": ("Loan Origination & Credit Assessment", "LND"),
    "fraud_detection": ("Fraud Detection", "FRD"),
    "kyc_onboarding": ("Customer Onboarding & KYC", "KYC"),
    "insurance": ("Insurance Platform", "INS"),
    "investment": ("Investment Platform", "INV"),
    "regulatory_reporting": ("Regulatory Reporting", "REG"),
    "data_analytics": ("Financial Data Analytics", "DAT"),
}


def domain_label(domain: str) -> str:
    return DOMAINS.get(domain, (domain.replace("_", " ").title(), "GEN"))[0]


def domain_code(domain: str) -> str:
    return DOMAINS.get(domain, ("", "GEN"))[1]


# --------------------------------------------------------------------------
# Requirement categories (multi-label)
# --------------------------------------------------------------------------
CATEGORIES = ["business", "stakeholder", "functional", "non_functional", "security", "privacy",
              "regulatory", "performance", "availability", "reliability", "usability",
              "data_management", "integration", "auditability", "operational", "maintenance"]

# Phrases that mean audit/event logging. A bare "log" prefix would also match
# "log in" / "login", so logging is recognised only through these phrases.
LOG_WORDS = ["audit log", "event log", "access log", "application log", "transaction log", "log entr",
             "logging", "logged", "log all", "log every", "log each"]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "security": ["authenticat", "authoriz", "authoris", "password", "otp", "mfa", "2fa", "multi-factor",
                 "encrypt", "tls", "ssl", "access control", "role-based", "rbac", "permission", "vulnerab",
                 "penetration", "malware", "biometric", "session", "tamper", "secure", "security", "cyber",
                 "hsm", "key management", "certificate", "login", "lockout", "step-up", "device binding",
                 "sast", "dast", "fraud"],
    "privacy": ["personal data", "privacy", "consent", "pii", "dpdp", "mask", "anonymi", "pseudonym",
                "data principal", "data subject", "erasure", "erase", "opt-out", "opt out", "sensitive data",
                "aadhaar", "confidential", "purpose limitation"],
    "regulatory": ["rbi", "regulat", "complian", "kyc", "aml", "sebi", "npci", "irdai", "guideline",
                   "directive", "master direction", "statutory", "legal", "cert-in", "pci", "regulator",
                   "localisation", "localization", "stored in india", "within india", "dpdp", "it act",
                   "key fact statement", "grievance"],
    "performance": ["response time", "latency", "millisecond", "ms", "seconds", "second", "throughput",
                    "tps", "transactions per second", "concurrent", "peak load", "performance", "fast", "quick"],
    "availability": ["availability", "uptime", "99.", "downtime", "24x7", "24/7", "disaster recovery",
                     "failover", "rto", "rpo", "high availability", "available"],
    "reliability": ["reliab", "fault toleran", "recover", "retry", "idempot", "consisten", "durab",
                    "error rate", "resilien", "duplicate transaction", "no data loss"],
    "usability": ["user-friendly", "user friendly", "intuitive", "easy to use", "usability", "accessib",
                  "wcag", "language", "multilingual", "screen", "ui", "ux", "simple", "navigation"],
    "data_management": ["data", "record", "retain", "retention", "archive", "storage", "store", "database",
                        "purge", "delete", "migrat", "report", "statement"],
    "integration": ["integrat", "api", "interface", "cbs", "core banking", "gateway", "npci", "upi", "imps",
                    "neft", "rtgs", "credit bureau", "cibil", "third-party", "third party", "external",
                    "webhook", "sftp", "batch file", "legacy", "swift", "aggregator"],
    "auditability": ["audit", *LOG_WORDS, "trail", "traceab", "evidence", "history", "tamper-evident",
                     "immutable"],
    "operational": ["monitor", "alert", "operations", "support", "sla", "incident", "helpdesk", "batch",
                    "deployment", "release", "on-call", "dashboard"],
    "maintenance": ["maintain", "maintenance", "upgrade", "patch", "configurable", "configuration",
                    "modular", "extensib", "version", "documentation", "parameteris", "parameteriz"],
    "business": ["revenue", "cost", "business", "customer acquisition", "market", "profit", "kpi", "fee",
                 "charge", "interest rate", "pricing", "growth", "cross-sell", "branch", "product",
                 "eligib", "limit", "loan amount", "tenure", "cashback", "reward"],
    "stakeholder": ["customer shall be able", "customers shall be able", "user shall be able",
                    "customer must be able", "customers must be able", "customer should be able",
                    "customers should be able", "customers want", "users want", "staff shall be able",
                    "officer shall be able", "manager shall be able", "agents shall be able"],
}

NON_FUNCTIONAL_CATEGORIES = {"security", "privacy", "performance", "availability", "reliability",
                             "usability", "maintenance", "operational"}

# Words that make a requirement vague / untestable (classic "weak words" list).
WEAK_WORDS = ["fast", "quick", "quickly", "user-friendly", "user friendly", "easy", "easily", "simple",
              "efficient", "adequate", "appropriate", "as needed", "as required", "etc", "and/or",
              "flexible", "robust", "seamless", "minimal", "sufficient", "reasonable", "normally",
              "generally", "approximately", "several", "many", "large number", "high performance",
              "best", "state-of-the-art", "intuitive", "modern", "if possible", "where possible",
              "secure enough", "highly secure", "highly available", "scalable", "good", "better",
              "proper", "properly", "timely", "regularly", "frequently", "significant", "minimum delay"]

INCOMPLETE_MARKERS = ["tbd", "tbc", "to be decided", "to be confirmed", "to be defined", "???", "xx",
                      "pending decision", "later phase decides"]

INFEASIBLE_MARKERS = ["100% uptime", "100% availability", "zero downtime", "never fail", "never be down",
                      "instantaneous", "instantly", "unlimited", "zero latency", "no errors ever",
                      "100% accuracy", "100% accurate", "always available", "zero fraud"]

# Acronyms that are well-known in Indian financial software and need no definition.
KNOWN_ACRONYMS = set("""API OTP KYC AML UPI IMPS NEFT RTGS NPCI RBI SEBI PAN MFA 2FA SMS PIN ATM POS
UI UX ID PDF CSV TLS SSL AES RSA HSM PCI DSS CBS EMI GST INR USD SLA RTO RPO DR CIBIL IFSC NACH
VPA QR NBFC CERT IN IT HTTP HTTPS REST JSON XML SQL DB OS iOS ML AI LLM KPI CRM ERP SFTP OAUTH SSO
JWT IP VPN DDOS SAST DAST SCA DPDP PII CKYC CKYCR VKYC CDD EDD STR CTR FIU AADHAAR NRI MSME FD RD
CASA SWIFT ISO WCAG GDPR SOC SIEM CICD CI CD MVP UAT QA BA PM PO DPIA KFS APR NPA ECS DSA LSP""".split())

STAKEHOLDER_ROLES = ["customer", "business analyst", "product owner", "compliance officer", "compliance",
                     "security team", "security officer", "risk management", "risk team", "operations",
                     "auditor", "architect", "developer", "legal", "regulator", "branch staff",
                     "customer support", "loan officer", "credit analyst", "fraud analyst", "finance",
                     "marketing", "it operations", "management"]

# --------------------------------------------------------------------------
# Compliance topics: what a regulated financial application is expected to
# address. Used for gap detection and for tagging knowledge-base chunks.
# --------------------------------------------------------------------------
TOPICS: dict[str, dict] = {
    "authentication": {"label": "Strong / multi-factor authentication",
                       "keywords": ["authenticat", "otp", "mfa", "2fa", "multi-factor", "biometric", "step-up",
                                    "login", "password", "pin"]},
    "access_control": {"label": "Access control and least privilege",
                       "keywords": ["access control", "role-based", "rbac", "least privilege", "authoris",
                                    "authoriz", "permission", "privileged access", "maker-checker"]},
    "encryption": {"label": "Encryption of data in transit and at rest",
                   "keywords": ["encrypt", "tls", "ssl", "aes", "hsm", "key management", "cryptograph"]},
    "audit_logging": {"label": "Audit trail and logging",
                      "keywords": ["audit", *LOG_WORDS, "trail", "tamper-evident", "immutable"]},
    "data_localisation": {"label": "Data localisation (storage in India)",
                          "keywords": ["localisation", "localization", "stored in india", "within india",
                                       "india region", "only in india", "data residency", "hosted in india"]},
    "consent_privacy": {"label": "Consent, notice and personal-data protection",
                        "keywords": ["consent", "privacy", "personal data", "notice", "dpdp", "data principal",
                                     "purpose", "mask", "pii", "opt-out", "opt out"]},
    "data_retention": {"label": "Data retention and deletion",
                       "keywords": ["retain", "retention", "archive", "purge", "delete", "erasure", "years"]},
    "breach_incident_reporting": {"label": "Security incident / breach reporting",
                                  "keywords": ["incident", "breach", "cert-in", "notify the regulator",
                                               "report to", "security event"]},
    "transaction_integrity": {"label": "Transaction integrity and reconciliation",
                              "keywords": ["idempot", "reconcil", "duplicate transaction", "double debit",
                                           "integrity", "settlement", "reversal", "atomic", "ledger"]},
    "fraud_monitoring": {"label": "Fraud monitoring and transaction alerts",
                         "keywords": ["fraud", "suspicious", "anomal", "velocity", "alert", "risk scor",
                                      "aml", "mule"]},
    "kyc_cdd": {"label": "KYC / customer due diligence",
                "keywords": ["kyc", "due diligence", "e-kyc", "ekyc", "video kyc", "v-cip", "identity verification",
                             "aadhaar", "pan verification", "ckyc", "sanction", "pep"]},
    "customer_grievance": {"label": "Customer grievance redressal",
                           "keywords": ["grievance", "complaint", "dispute", "redressal", "ombudsman"]},
    "fair_lending_disclosure": {"label": "Fair lending and loan disclosures",
                                "keywords": ["key fact statement", "kfs", "apr", "annual percentage", "disclos",
                                             "fair", "discriminat", "cooling-off", "cooling off", "adverse action",
                                             "rejection reason"]},
    "business_continuity": {"label": "Business continuity and disaster recovery",
                            "keywords": ["disaster recovery", "business continuity", "failover", "rto", "rpo",
                                         "backup", "dr site", "dr drill"]},
    "third_party_api_security": {"label": "Secure third-party / API integration",
                                 "keywords": ["api", "third-party", "third party", "gateway", "mutual tls",
                                              "oauth", "vendor", "outsourc", "aggregator"]},
    "change_management": {"label": "Change and release management",
                          "keywords": ["change management", "change approval", "release", "rollback",
                                       "segregation of duties", "deployment approval"]},
    "secure_development": {"label": "Secure development lifecycle",
                           "keywords": ["threat model", "sast", "dast", "penetration", "code review",
                                        "vulnerability", "secure coding", "sca"]},
    "performance_capacity": {"label": "Performance and capacity",
                             "keywords": ["response time", "latency", "throughput", "tps", "concurrent",
                                          "capacity", "peak"]},
}

# Topics a regulated application in each domain is expected to address.
DOMAIN_EXPECTED_TOPICS: dict[str, list[str]] = {
    "digital_banking": ["authentication", "access_control", "encryption", "audit_logging", "data_localisation",
                        "consent_privacy", "data_retention", "breach_incident_reporting", "transaction_integrity",
                        "fraud_monitoring", "kyc_cdd", "customer_grievance", "business_continuity"],
    "payments": ["authentication", "encryption", "audit_logging", "data_localisation", "transaction_integrity",
                 "fraud_monitoring", "breach_incident_reporting", "consent_privacy", "business_continuity",
                 "third_party_api_security", "customer_grievance"],
    "lending": ["kyc_cdd", "consent_privacy", "fair_lending_disclosure", "customer_grievance", "audit_logging",
                "data_retention", "access_control", "encryption"],
    "fraud_detection": ["fraud_monitoring", "audit_logging", "consent_privacy", "access_control",
                        "data_retention", "encryption", "breach_incident_reporting"],
    "kyc_onboarding": ["kyc_cdd", "consent_privacy", "data_retention", "audit_logging", "encryption",
                       "access_control", "data_localisation"],
}
DEFAULT_EXPECTED_TOPICS = ["authentication", "access_control", "encryption", "audit_logging",
                           "consent_privacy", "data_retention", "business_continuity"]


def expected_topics(domain: str) -> list[str]:
    return DOMAIN_EXPECTED_TOPICS.get(domain, DEFAULT_EXPECTED_TOPICS)


def detect_topics(text: str) -> list[str]:
    return [key for key, topic in TOPICS.items() if find_keywords(text, topic["keywords"])]


# --------------------------------------------------------------------------
# Security & privacy areas checked by the Security and Privacy Agent
# --------------------------------------------------------------------------
SECURITY_AREAS: dict[str, dict] = {
    "authentication": {"label": "Authentication",
                       "keywords": ["authenticat", "login", "otp", "mfa", "2fa", "multi-factor", "biometric",
                                    "password", "pin", "step-up", "device binding"],
                       "safeguard": "Multi-factor authentication with step-up authentication for high-value or "
                                    "high-risk transactions; device binding for mobile channels."},
    "authorisation": {"label": "Authorisation",
                      "keywords": ["authoris", "authoriz", "approve", "maker-checker", "entitlement", "permission"],
                      "safeguard": "Explicit authorisation rules per function and transaction type, including "
                                   "maker-checker for sensitive operations."},
    "access_control": {"label": "Access control",
                       "keywords": ["access control", "role-based", "rbac", "least privilege", "privileged",
                                    "role"],
                       "safeguard": "Role-based access control with least privilege and periodic access reviews."},
    "encryption": {"label": "Encryption",
                   "keywords": ["encrypt", "tls", "ssl", "aes", "hsm", "cryptograph", "key management"],
                   "safeguard": "TLS 1.2+ in transit, AES-256 at rest, keys managed in an HSM/KMS with rotation."},
    "data_protection": {"label": "Data protection & privacy",
                        "keywords": ["mask", "personal data", "privacy", "consent", "pii", "tokeni",
                                     "anonymi", "pseudonym", "dpdp", "sensitive data"],
                        "safeguard": "Consent capture, data minimisation, masking/tokenisation of personal and "
                                     "card data, and privacy notices."},
    "data_retention": {"label": "Data retention",
                       "keywords": ["retain", "retention", "archive", "purge", "delete", "erasure"],
                       "safeguard": "Documented retention schedule with secure deletion and legal-hold support."},
    "transaction_integrity": {"label": "Transaction integrity",
                              "keywords": ["idempot", "reconcil", "integrity", "duplicate transaction",
                                           "double debit", "atomic", "checksum", "settlement", "reversal"],
                              "safeguard": "Idempotency keys, atomic posting, end-of-day reconciliation and "
                                           "integrity checks on transaction messages."},
    "audit_logging": {"label": "Audit logging",
                      "keywords": ["audit", *LOG_WORDS, "trail", "tamper-evident", "immutable"],
                      "safeguard": "Tamper-evident audit trail of logins, transactions and administrative "
                                   "actions, retained per policy and monitored by a SIEM."},
    "secure_integration": {"label": "Secure integration",
                           "keywords": ["api", "gateway", "mutual tls", "oauth", "third-party", "third party",
                                        "webhook", "sftp", "integration"],
                           "safeguard": "Mutually-authenticated APIs (mTLS/OAuth2), input validation, rate "
                                        "limiting and signed messages for external integrations."},
    "threat_vulnerability": {"label": "Threat & vulnerability management",
                             "keywords": ["threat", "vulnerab", "penetration", "sast", "dast", "patch",
                                          "malware", "waf", "ddos", "security testing"],
                             "safeguard": "Threat modelling during design, SAST/DAST/dependency scanning in CI, "
                                          "and an independent penetration test before release."},
}
