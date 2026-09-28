"""Agent capability tokens - least privilege for agent roles (architecture P.1, ``[DESIGN] D10``).

The architecture's design, implemented as written:

* a **static per-role capability table** (:data:`ROLE_CAPABILITIES`), derived
  from the E.1 role capability matrix: which entity types each role may read,
  which it may write (nearly always none - roles propose), whether it may
  retrieve (Compliance and Security & Privacy only) and whether it may call a
  model at all;
* an **immutable token** (:class:`CapabilityToken`) minted by the Coordinator
  for one role, in one run of one project, from that table and nothing else;
* **checked where the access happens**, never by the agent: the policy
  (``reqpilot.domain.policy.can``) refuses every repository read or write by an
  agent-role actor that its token does not grant, and the LLM gateway refuses a
  model call whose token is not the calling role's.

Why a model cannot widen a token. A token is a frozen dataclass whose every
field is covered by an HMAC seal under a key that exists only in this process
and is never stored, logged or serialised. Changing any field - with
``dataclasses.replace``, ``object.__setattr__`` or by building a new instance
from a model's JSON or a client's request - produces a token whose seal no
longer verifies. Verification also re-derives the grants from the static table,
so even a correctly sealed token cannot carry more than its role's row. Tokens
are transient: minted per node invocation, bound to one run and one project,
short-lived, and never persisted (a resumed run mints fresh ones).

``HUMAN_APPROVAL`` is never minted. The only role that can approve is exercised
by an authenticated human through the approval service (architecture M.2), and
no token can carry an approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from reqpilot.domain.enums import AgentRole, ResourceType

#: How long a token lives. A node invocation is seconds; a model call with its
#: retries is at most a few minutes. Expiry bounds the damage of a leaked token.
DEFAULT_TTL_SECONDS = 15 * 60

#: The process-local sealing key. Never stored, never logged, never serialised.
_SEAL_KEY = secrets.token_bytes(32)

_R = ResourceType


class Access(StrEnum):
    """What an access does to the resource."""

    READ = "read"
    WRITE = "write"
    RETRIEVE = "retrieve"


@dataclass(frozen=True)
class RoleCapability:
    """One row of the static capability table."""

    may_read: frozenset[ResourceType]
    may_write: frozenset[ResourceType]
    may_retrieve: bool
    may_call_model: bool


def _row(
    reads: Iterable[ResourceType],
    *,
    writes: Iterable[ResourceType] = (),
    retrieve: bool = False,
    model: bool = True,
) -> RoleCapability:
    return RoleCapability(frozenset(reads), frozenset(writes), retrieve, model)


#: The static table (architecture E.1, P.1). Read column-wise: exactly two roles
#: retrieve; no role writes a proposal's subject; no role writes an approval, a
#: gate, a membership, a baseline or the audit log; no row is ever minted for
#: Human Approval.
ROLE_CAPABILITIES: Mapping[AgentRole, RoleCapability] = MappingProxyType(
    {
        # #1 - deterministic; writes run records directly (E.1: "direct (runs)").
        AgentRole.COORDINATOR: _row(
            (_R.PROJECT, _R.GRAPH_RUN, _R.SOURCE_DOCUMENT, _R.REQUIREMENT, _R.REQUIREMENT_VERSION),
            writes=(_R.GRAPH_RUN,),
            model=False,
        ),
        AgentRole.STAKEHOLDER_INTERACTION: _row(
            (_R.STAKEHOLDER, _R.INTERVIEW_SESSION, _R.UTTERANCE)
        ),
        # Also the role of the semantic quality check, whose prompt is registered
        # for it (P5) - hence the requirement-version read.
        AgentRole.REQUIREMENT_EXTRACTION: _row(
            (_R.SOURCE_DOCUMENT, _R.UTTERANCE, _R.REQUIREMENT_VERSION)
        ),
        AgentRole.CLARIFICATION: _row(
            (_R.REQUIREMENT_VERSION, _R.QUALITY_FINDING, _R.CLARIFICATION, _R.UTTERANCE)
        ),
        AgentRole.CLASSIFICATION: _row((_R.REQUIREMENT_VERSION,)),
        AgentRole.CONFLICT_DETECTION: _row((_R.REQUIREMENT_VERSION, _R.CONFLICT, _R.GLOSSARY_TERM)),
        AgentRole.COMPLIANCE: _row(
            (
                _R.REQUIREMENT_VERSION,
                _R.SOURCE_ALLOWLIST,
                _R.KNOWLEDGE_CHUNK,
                _R.EVIDENCE,
                _R.COMPLIANCE_MAPPING,
            ),
            retrieve=True,
        ),
        AgentRole.SECURITY_PRIVACY: _row(
            (
                _R.REQUIREMENT_VERSION,
                _R.SOURCE_ALLOWLIST,
                _R.KNOWLEDGE_CHUNK,
                _R.EVIDENCE,
                _R.SECURITY_PRIVACY_FINDING,
            ),
            retrieve=True,
        ),
        AgentRole.RISK_ANALYSIS: _row(
            (
                _R.REQUIREMENT_VERSION,
                _R.COMPLIANCE_MAPPING,
                _R.COMPLIANCE_GAP,
                _R.SECURITY_PRIVACY_FINDING,
                _R.QUALITY_FINDING,
                _R.CONFLICT,
                _R.RISK,
                _R.EVIDENCE,
            )
        ),
        AgentRole.SDLC_SELECTION: _row(
            (_R.SDLC_RUN, _R.BASELINE, _R.REQUIREMENT_VERSION, _R.RISK, _R.COMPLIANCE_MAPPING)
        ),
        AgentRole.DOCUMENTATION: _row(
            (
                _R.BASELINE,
                _R.REQUIREMENT_VERSION,
                _R.ARTIFACT,
                _R.TRACEABILITY_LINK,
                _R.RISK,
                _R.COMPLIANCE_MAPPING,
            )
        ),
        # #12 - deterministic; writes its reports directly (E.1: "direct (reports)").
        AgentRole.VALIDATION: _row(
            (_R.REQUIREMENT_VERSION, _R.EXTRACTION_CANDIDATE, _R.SOURCE_DOCUMENT, _R.UTTERANCE),
            writes=(_R.REVIEW_ITEM,),
            model=False,
        ),
    }
)

#: Roles for which no token is ever minted (architecture M.2).
NEVER_MINTED: frozenset[AgentRole] = frozenset({AgentRole.HUMAN_APPROVAL})

#: Resource types no token may ever write, whatever the table says. Checked at
#: verification as well, so a table edit cannot quietly grant one.
NEVER_WRITABLE: frozenset[ResourceType] = frozenset(
    {
        _R.APPROVAL_TASK,
        _R.PROJECT,
        _R.PROJECT_MEMBER,
        _R.AUDIT_EVENT,
        _R.BASELINE,
        _R.SOURCE_ALLOWLIST,
        _R.KNOWLEDGE_ITEM,
        _R.KNOWLEDGE_CHUNK,
        _R.NORMATIVE_SOURCE,
        _R.CONTROL,
    }
)


@dataclass(frozen=True)
class CapabilityToken:
    """An immutable, sealed grant for one agent role in one run of one project."""

    token_id: str
    run_id: str
    project_id: str
    role: AgentRole
    may_read: frozenset[ResourceType]
    may_write: frozenset[ResourceType]
    may_retrieve: bool
    may_call_model: bool
    allowlisted_sources: frozenset[str]
    issued_at: float
    expires_at: float
    #: HMAC over every other field. Excluded from ``repr`` so it is never logged.
    seal: str = field(repr=False, compare=False)

    def canonical(self) -> bytes:
        return _canonical(
            token_id=self.token_id,
            run_id=self.run_id,
            project_id=self.project_id,
            role=self.role,
            may_read=self.may_read,
            may_write=self.may_write,
            may_retrieve=self.may_retrieve,
            may_call_model=self.may_call_model,
            allowlisted_sources=self.allowlisted_sources,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )


def _canonical(**fields: object) -> bytes:
    def norm(value: object) -> object:
        if isinstance(value, frozenset):
            return sorted(str(v) for v in value)
        if isinstance(value, StrEnum):
            return value.value
        return value

    return json.dumps({k: norm(v) for k, v in sorted(fields.items())}, sort_keys=True).encode()


def _seal(payload: bytes) -> str:
    return hmac.new(_SEAL_KEY, payload, hashlib.sha256).hexdigest()


def mint_capability(
    *,
    run_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
    role: AgentRole,
    allowlisted_sources: Iterable[str] = (),
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> CapabilityToken:
    """Mint the token for ``role`` in one run of one project - from the table alone.

    Called by the Coordinator per node invocation. Nothing a caller passes can
    widen the grant: the entity types, retrieval and model rights come from
    :data:`ROLE_CAPABILITIES`; only the run, the project and (for a retrieving
    role) the project's allowlist are supplied.
    """
    if role in NEVER_MINTED:
        raise ValueError(f"no capability is ever minted for {role}: it acts only as a human")
    if not run_id or not project_id:
        raise ValueError("a capability is minted for one run of one project")
    row = ROLE_CAPABILITIES[role]
    sources = frozenset(str(s) for s in allowlisted_sources)
    if sources and not row.may_retrieve:
        raise ValueError(f"{role} does not retrieve, so it holds no allowlist")
    if ttl_seconds <= 0:
        raise ValueError("a capability must have a positive lifetime")
    issued = time.time() if now is None else now
    fields: dict[str, object] = {
        "token_id": uuid.uuid4().hex,
        "run_id": str(run_id),
        "project_id": str(project_id),
        "role": role,
        "may_read": row.may_read,
        "may_write": row.may_write,
        "may_retrieve": row.may_retrieve,
        "may_call_model": row.may_call_model,
        "allowlisted_sources": sources,
        "issued_at": float(issued),
        "expires_at": float(issued + ttl_seconds),
    }
    return CapabilityToken(**fields, seal=_seal(_canonical(**fields)))  # type: ignore[arg-type]


def token_problem(token: object, *, now: float | None = None) -> str | None:
    """Why ``token`` is not a valid capability, or ``None`` if it is.

    Fails closed on anything unexpected: a non-token, a subclass, a broken seal,
    grants that differ from the role's table row, an expired or future-dated
    token, a never-minted role.
    """
    if token is None:
        return "no capability token was presented"
    if type(token) is not CapabilityToken:
        return "the capability is not a token minted by the coordinator"
    try:
        expected = _seal(token.canonical())
    except Exception:  # pragma: no cover - a malformed field
        return "the capability token is malformed"
    if not isinstance(token.seal, str) or not hmac.compare_digest(expected, token.seal):
        return "the capability token's seal does not verify (forged or altered)"
    if token.role in NEVER_MINTED or token.role not in ROLE_CAPABILITIES:
        return f"no capability exists for {token.role}"
    row = ROLE_CAPABILITIES[token.role]
    if (
        token.may_read != row.may_read
        or token.may_write != row.may_write
        or token.may_retrieve != row.may_retrieve
        or token.may_call_model != row.may_call_model
        or token.may_write & NEVER_WRITABLE
    ):
        return "the capability token grants more than its role's table row"
    current = time.time() if now is None else now
    if current >= token.expires_at:
        return "the capability token has expired"
    if token.issued_at > current + 5:
        return "the capability token is dated in the future"
    return None


def access_problem(
    token: object,
    *,
    project_id: object,
    resource_type: ResourceType,
    access: Access,
    resource_id: str | None = None,
    now: float | None = None,
) -> str | None:
    """Why ``token`` does not permit this access, or ``None`` if it does."""
    problem = token_problem(token, now=now)
    if problem is not None:
        return problem
    assert isinstance(token, CapabilityToken)
    if project_id is None or str(project_id) != token.project_id:
        return "the capability token is for another project"
    if resource_type is ResourceType.GRAPH_RUN and resource_id and resource_id != token.run_id:
        return "the capability token is for another run"
    if access is Access.RETRIEVE:
        return None if token.may_retrieve else f"{token.role} may not retrieve"
    grants = token.may_read if access is Access.READ else token.may_write
    if resource_type not in grants:
        return f"{token.role} may not {access} {resource_type}"
    return None


def model_call_problem(token: object, role: AgentRole, *, now: float | None = None) -> str | None:
    """Why ``token`` does not permit ``role`` to call a model, or ``None``."""
    problem = token_problem(token, now=now)
    if problem is not None:
        return problem
    assert isinstance(token, CapabilityToken)
    if token.role is not role:
        return f"the capability token belongs to {token.role}, not {role}"
    if not token.may_call_model:
        return f"{role} makes no model call"
    return None
