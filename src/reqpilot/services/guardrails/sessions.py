"""Opaque server-side sessions (architecture ADR-009; roadmap P11 "session isolation").

ADR-009 selected opaque session tokens stored server-side over JWTs, because
revocation must be immediate. P11 implements that store:

* a token is 32 random bytes (URL-safe), shown **once** to its holder; only its
  SHA-256 is stored, so the table cannot be replayed as a set of logins;
* the session names exactly one user; the user's **roles are not in the
  session** - they are read from ``project_member`` on every request, so a role
  change or a removal takes effect on the next request;
* a session expires (``SESSION_TTL``), can be revoked by its holder, and all of
  a user's sessions are revoked when the user is deactivated or revoked in bulk;
* an unknown, expired or revoked token, a token of an inactive user, and a
  token presented alongside a *different* identity claim are all refused - the
  request is never answered as somebody else and never falls back to another
  mechanism.

What is **not** implemented (ADR-009 / Phase 0 E.2): password verification and
MFA. How a session is first obtained is still the development identity claim
(``X-ReqPilot-Actor``), which stays disabled in production. P11 hardens what a
session is and how it is checked; it does not add a login.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from reqpilot.domain.enums import ActorKind, AuditEventType
from reqpilot.domain.errors import AuthSessionError
from reqpilot.domain.models.base import as_utc, utc_now
from reqpilot.domain.models.guardrails import AuthSession
from reqpilot.domain.models.identity import User
from reqpilot.services.audit import AuditService

#: How long a session lives. A working day; revocation is immediate regardless.
SESSION_TTL = dt.timedelta(hours=8)

#: The cookie the demonstration UI uses (httpOnly, SameSite=Strict).
SESSION_COOKIE = "reqpilot_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    session_id: uuid.UUID
    user_id: uuid.UUID
    expires_at: dt.datetime
    #: Shown once to its holder; never logged, never stored.
    token: str = field(repr=False)


class AuthSessionService:
    def __init__(self, session: Session, *, ttl: dt.timedelta = SESSION_TTL) -> None:
        self._session = session
        self._ttl = ttl

    def issue(self, user_id: uuid.UUID) -> IssuedSession:
        """A new session for an existing, active user."""
        user = self._session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthSessionError("no active user to open a session for")
        token = secrets.token_urlsafe(32)
        now = utc_now()
        row = AuthSession(
            user_id=user.id,
            token_hash=token_hash(token),
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._session.add(row)
        self._session.flush()
        self._event(AuditEventType.AUTH_SESSION_ISSUED, user.id, row.id)
        return IssuedSession(
            session_id=row.id, user_id=user.id, expires_at=row.expires_at, token=token
        )

    def resolve(self, token: str) -> User:
        """The user a presented token belongs to. Refuses anything not current."""
        if not token or len(token) > 512:
            raise AuthSessionError("invalid session")
        row = self._session.scalars(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        ).first()
        if row is None:
            raise AuthSessionError("invalid session")
        if row.revoked_at is not None:
            raise AuthSessionError("the session was revoked")
        if as_utc(row.expires_at) <= utc_now():
            raise AuthSessionError("the session has expired")
        user = self._session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AuthSessionError("the session's user is not active")
        row.last_used_at = utc_now()
        return user

    def revoke(self, token: str) -> uuid.UUID:
        """Revoke the presented session (logout). Only its holder has the token."""
        user = self.resolve(token)
        row = self._session.scalars(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        ).one()
        row.revoked_at = utc_now()
        self._session.flush()
        self._event(AuditEventType.AUTH_SESSION_REVOKED, user.id, row.id)
        return row.id

    def revoke_all(self, user_id: uuid.UUID) -> int:
        """Revoke every live session of a user (deactivation, suspected compromise)."""
        now = utc_now()
        result = self._session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        count = int(result.rowcount or 0)  # type: ignore[attr-defined]
        if count:
            self._event(AuditEventType.AUTH_SESSION_REVOKED, user_id, None, count=count)
        return count

    def _event(
        self,
        event_type: AuditEventType,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        **extra: object,
    ) -> None:
        AuditService(self._session).append(
            event_type=event_type,
            actor_kind=ActorKind.HUMAN,
            actor_ref=str(user_id),
            project_id=None,
            subject_type="auth_session",
            subject_id=str(session_id) if session_id else None,
            payload={"user_id": str(user_id), **extra},
        )
