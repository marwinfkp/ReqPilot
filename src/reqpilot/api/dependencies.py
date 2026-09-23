"""Request-scoped dependencies: the database session and the acting actor.

**Actor resolution here is development-only.** Real authentication - sessions,
password verification, MFA - is explicitly out of scope for this phase and was
never part of the foundation either. What exists is the smallest mechanism that
lets the API and the demonstration UI act *as* a real, project-scoped actor so
that authorization can be enforced for real:

    X-ReqPilot-Actor: <user id>

The header names a ``app_user`` row; the actor's roles are then read from
``project_member``, which is the same source the policy consults everywhere
else. **Nothing is trusted from the header except the identity claim**, and the
whole mechanism refuses to operate outside development.

The security property that matters: this is a weak *authentication* stand-in,
not a weak *authorization* path. Roles still come from the database, project
isolation still applies, and no header value can grant a role the user does not
hold.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.config import AppEnv, EmbeddingProviderKind, Settings, get_settings
from reqpilot.domain.enums import ActorKind, Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.identity import ProjectMember, User
from reqpilot.domain.policy import Actor
from reqpilot.llm.gateway import LLMGateway, build_gateway
from reqpilot.repositories.database import get_session_factory
from reqpilot.retrieval.embeddings import EmbeddingProvider, provider_for
from reqpilot.retrieval.rules import RetrievalRules, load_retrieval_rules
from reqpilot.rules.compliance import (
    ComplianceRules,
    SecurityRules,
    load_compliance_rules,
    load_security_rules,
)
from reqpilot.rules.elicitation import ElicitationRules, load_elicitation_rules
from reqpilot.rules.extraction import ExtractionRules, load_extraction_rules
from reqpilot.rules.quality import QualityRules, load_quality_rules
from reqpilot.rules.risk import RiskRules, load_risk_rules
from reqpilot.services.compliance import Retriever
from reqpilot.services.knowledge.retrieval import RetrievalService


def get_db() -> Iterator[Session]:
    """Yield a request-scoped session, committing on success."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_actor(session: Session, user_id: uuid.UUID) -> Actor:
    """Build a policy actor from persisted membership rows.

    Roles are never taken from the request. They are read from
    ``project_member``, so an actor can only ever exercise a role that a
    project manager actually granted them.
    """
    roles: dict[ProjectId, frozenset[Role]] = {}
    stmt = select(ProjectMember).where(ProjectMember.user_id == user_id)
    for membership in session.scalars(stmt):
        key = ProjectId(membership.project_id)
        roles[key] = roles.get(key, frozenset()) | {membership.role}

    return Actor(
        actor_id=ActorId(user_id),
        kind=ActorKind.HUMAN,
        roles_by_project=roles,
    )


def get_actor(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_reqpilot_actor: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the acting user for this request. Development only."""
    if settings.app_env is AppEnv.PRODUCTION:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "header-based actor resolution is a development mechanism and is "
                "disabled outside development; real authentication is not implemented"
            ),
        )

    if not x_reqpilot_actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-ReqPilot-Actor header is required (development actor mechanism)",
        )

    try:
        user_id = uuid.UUID(x_reqpilot_actor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-ReqPilot-Actor must be a user id",
        ) from None

    if session.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown actor")

    return load_actor(session, user_id)


@lru_cache(maxsize=4)
def _embedding_provider(
    kind: EmbeddingProviderKind, model: str, allow_download: bool
) -> EmbeddingProvider:
    return provider_for(kind, model, allow_download=allow_download)


def get_embedding_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmbeddingProvider:
    """The configured embedding provider, loaded once per process (ADR-005).

    Cached because the local model takes seconds to load; the cache key is the
    configuration, so a different configuration gets a different provider.
    """
    return _embedding_provider(
        settings.embedding_provider, settings.embedding_model, settings.embedding_allow_download
    )


@lru_cache(maxsize=4)
def _retrieval_rules(rules_dir: str) -> RetrievalRules:
    return load_retrieval_rules(Path(rules_dir))


def get_retrieval_rules(settings: Annotated[Settings, Depends(get_settings)]) -> RetrievalRules:
    """The versioned retrieval ruleset (J.3, J.4), validated once per process."""
    return _retrieval_rules(str(settings.rules_dir))


@lru_cache(maxsize=4)
def _extraction_rules(rules_dir: str) -> ExtractionRules:
    return load_extraction_rules(Path(rules_dir))


def get_extraction_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExtractionRules:
    """The versioned extraction and classification ruleset, validated once per process."""
    return _extraction_rules(str(settings.rules_dir))


@lru_cache(maxsize=4)
def _elicitation_rules(rules_dir: str) -> ElicitationRules:
    return load_elicitation_rules(Path(rules_dir))


def get_elicitation_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ElicitationRules:
    """The versioned elicitation ruleset and interview templates (P4), loaded once."""
    return _elicitation_rules(str(settings.rules_dir))


@lru_cache(maxsize=4)
def _quality_rules(rules_dir: str) -> QualityRules:
    return load_quality_rules(Path(rules_dir))


def get_quality_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> QualityRules:
    """The versioned quality and conflict heuristics (P5), validated once per process."""
    return _quality_rules(str(settings.rules_dir))


@lru_cache(maxsize=4)
def _compliance_rules(rules_dir: str) -> ComplianceRules:
    return load_compliance_rules(Path(rules_dir))


@lru_cache(maxsize=4)
def _risk_rules(rules_dir: str) -> RiskRules:
    return load_risk_rules(Path(rules_dir))


def get_risk_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RiskRules:
    """The versioned severity matrix and register rules (P7, I.3), validated once
    per process. Loading refuses a matrix that differs from the approved I.3 table."""
    return _risk_rules(str(settings.rules_dir))


def get_compliance_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ComplianceRules:
    """The versioned expected-control checklists (P6, K.2), validated once per process."""
    return _compliance_rules(str(settings.rules_dir))


@lru_cache(maxsize=4)
def _security_rules(rules_dir: str) -> SecurityRules:
    return load_security_rules(Path(rules_dir))


def get_security_rules(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SecurityRules:
    """The versioned security/privacy catalogue and risk floors (P6, I.7), loaded once."""
    return _security_rules(str(settings.rules_dir))


def get_llm_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> LLMGateway:
    """The one model access boundary (ADR-006), built from configuration.

    The offline stub by default; the OpenAI provider when ``LLM_PROVIDER=openai``
    (architecture Y). Either may sit behind recorded fixtures.
    """
    return build_gateway(settings)


DbSession = Annotated[Session, Depends(get_db)]
CurrentActor = Annotated[Actor, Depends(get_actor)]
Embedder = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
Rules = Annotated[RetrievalRules, Depends(get_retrieval_rules)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Gateway = Annotated[LLMGateway, Depends(get_llm_gateway)]
ExtractionRulesDep = Annotated[ExtractionRules, Depends(get_extraction_rules)]
ElicitationRulesDep = Annotated[ElicitationRules, Depends(get_elicitation_rules)]
QualityRulesDep = Annotated[QualityRules, Depends(get_quality_rules)]
ComplianceRulesDep = Annotated[ComplianceRules, Depends(get_compliance_rules)]
SecurityRulesDep = Annotated[SecurityRules, Depends(get_security_rules)]
RiskRulesDep = Annotated[RiskRules, Depends(get_risk_rules)]

#: Builds the P2 allowlisted retrieval boundary for one request's session and actor.
RetrieverFactory = Callable[[Session, Actor], Retriever]


def get_retriever_factory(
    embedder: Embedder, rules: Rules, compliance_rules: ComplianceRulesDep
) -> RetrieverFactory:
    """P6 retrieval: the P2 hybrid ``RetrievalService`` - allowlist join, jurisdiction
    scope, KB pin - with the checklist's k. The only way a compliance run retrieves."""

    def build(session: Session, actor: Actor) -> Retriever:
        return RetrievalService(
            session,
            actor,
            embedder=embedder,
            rules=rules,
            default_top_k=compliance_rules.retrieval_top_k,
        ).retrieve

    return build


RetrieverFactoryDep = Annotated[RetrieverFactory, Depends(get_retriever_factory)]
