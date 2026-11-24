"""Security controls in one place:

* password hashing (PBKDF2-SHA256, standard library)
* server-side login sessions (random token in an HttpOnly cookie)
* role-based access control (simple role -> permission table)
* in-memory rate limiting
* audit logging helper
* sensitive-data masking before text is sent to the LLM
* prompt-injection detection and wrapping of untrusted text
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import AuditLog, User, UserSession

SESSION_COOKIE = "reqpilot_session"

# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
_PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


# --------------------------------------------------------------------------
# Roles and permissions (kept deliberately small and readable)
# --------------------------------------------------------------------------
ROLES = ["admin", "business_analyst", "compliance_officer", "security_officer", "project_manager", "auditor"]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "business_analyst": {"project:write", "analysis:run", "requirement:review", "baseline:approve",
                         "kb:upload", "evaluation:run", "clarification:answer", "change:propose",
                         "change:approve", "stakeholder:record", "stakeholder:review", "regeneration:run"},
    "compliance_officer": {"mapping:review", "kb:upload", "kb:manage", "requirement:review_security",
                           "clarification:answer", "change:propose", "stakeholder:record"},
    "security_officer": {"requirement:review_security", "kb:upload", "clarification:answer", "change:propose",
                         "stakeholder:record", "risk:accept"},
    "project_manager": {"project:write", "analysis:run", "requirement:review", "baseline:approve",
                        "sdlc:approve", "workflow:approve", "evaluation:run", "clarification:answer",
                        "change:propose", "change:approve", "stakeholder:record", "stakeholder:review",
                        "regeneration:run", "risk:accept"},
    "auditor": {"audit:view"},
}
# Two-stage analysis permissions:
#   clarification:answer - answer / defer clarification questions
#   change:propose / change:approve - requirement change requests (an approver can never approve their own)
#   stakeholder:record / stakeholder:review - stakeholder interviews
#   regeneration:run - selective regeneration of an analysis (Stage 2)
#   risk:accept - human acceptance of high/critical risks
# Every authenticated user can view results and download exports.


def has_permission(user: User, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(user.role, set())
    return "*" in perms or permission in perms


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)
    db.add(UserSession(token_hash=_token_hash(token), user_id=user.id, expires_at=expires))
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    db.query(UserSession).filter(UserSession.token_hash == _token_hash(token)).delete()
    db.commit()


def read_token(request: Request) -> str | None:
    """Token from the HttpOnly cookie (browser) or an Authorization header (scripts/tests)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = read_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")
    session = db.get(UserSession, _token_hash(token))
    if not session:
        raise HTTPException(401, "Session expired or invalid")
    expires = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        raise HTTPException(401, "Session expired")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User disabled")
    return user


def require(permission: str):
    """Dependency factory: `user = Depends(require("sdlc:approve"))`."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(403, f"Your role '{user.role}' is not allowed to perform '{permission}'")
        return user

    return checker


# --------------------------------------------------------------------------
# Rate limiting (sliding window, in memory - fine for a single backend process)
# --------------------------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _hits_lock:
        bucket = _hits[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def rate_limit(name: str, limit: int, window_seconds: int):
    """Dependency factory limiting an endpoint per client IP."""

    def limiter(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not check_rate_limit(f"{name}:{client}", limit, window_seconds):
            raise HTTPException(429, "Too many requests - please wait and try again")

    return limiter


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------
def audit(db: Session, action: str, user: User | None = None, entity_type: str = "",
          entity_id: str | int = "", details: dict | None = None, request: Request | None = None) -> None:
    db.add(AuditLog(
        user_email=user.email if user else "system",
        user_role=user.role if user else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=details or {},
        ip_address=(request.client.host if request and request.client else ""),
    ))
    db.commit()


# --------------------------------------------------------------------------
# Sensitive-data masking (applied to every text before it is sent to OpenAI)
# --------------------------------------------------------------------------
_SENSITIVE_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("CARD_NUMBER", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    ("AADHAAR", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}\b|\b\d{12}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("PHONE", re.compile(r"(?<![\d.])(?:\+91[ -]?)?[6-9]\d{9}(?![\d.])")),
    ("ACCOUNT_NUMBER", re.compile(r"\b\d{9,18}\b")),
]


def mask_sensitive(text: str) -> tuple[str, int]:
    """Replace e-mails, card/Aadhaar/PAN/IFSC/phone/account numbers with
    placeholders. Returns (masked_text, number_of_replacements)."""
    total = 0
    for label, pattern in _SENSITIVE_PATTERNS:
        text, count = pattern.subn(f"[MASKED_{label}]", text)
        total += count
    return text, total


# --------------------------------------------------------------------------
# Prompt-injection protection
# --------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts|rules)",
    r"disregard\s+(all\s+|the\s+)?(previous|prior|above|earlier)",
    r"you\s+are\s+now\s+",
    r"(reveal|print|show|output)\s+(your|the)\s+(system\s+prompt|instructions|api\s*key)",
    r"system\s+prompt",
    r"act\s+as\s+(an?\s+)?(admin|administrator|system|developer)",
    r"override\s+(the\s+|all\s+)?(rules|instructions|policies|scoring)",
    r"(automatically\s+approve|approve\s+(this|all)\s+(automatically|without\s+review))",
    r"<\s*/?\s*(system|assistant|instructions)\s*>",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def find_injection(text: str) -> list[str]:
    """Return suspicious instruction-like phrases found in untrusted text."""
    return [m.group(0) for m in _INJECTION_RE.finditer(text or "")]


def wrap_untrusted(label: str, text: str) -> str:
    """Wrap untrusted content in tags the system prompt tells the model to treat as
    data only. Any attempt to close the tag early is removed."""
    tag = f"untrusted_{label}"
    cleaned = re.sub(rf"</?\s*{tag}\s*>", "", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{cleaned}\n</{tag}>"
