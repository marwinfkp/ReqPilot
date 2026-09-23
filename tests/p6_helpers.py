"""Shared fixtures and a scripted model for the P6 tests.

Nothing here is a model. :class:`ScriptedComplianceModel` answers the three P6
prompts - compliance mapping, security and privacy derivation - from a small
table keyed by the words of the synthetic development requirements
(``data/dev/compliance``). Every scripted answer still passes through the real
gateway, schema validation, citation resolution and the deterministic
validators and evaluator, exactly like a model's.

:class:`FixtureRetriever` stands in for the P2 hybrid retrieval service on
SQLite, where pgvector search cannot run (the real service is exercised on
PostgreSQL). It is **not a second retrieval system**: it reads chunks through the
same P2 ``scoped_chunks`` allowlist join, and evidence recording re-checks every
chunk it returns against the project's scope, so it can never smuggle in a chunk
retrieval could not have produced.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import RequirementCategory, Role
from reqpilot.domain.ids import ProjectId, new_retrieval_id
from reqpilot.domain.models.knowledge import (
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
    SourceAllowlist,
)
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider
from reqpilot.repositories.knowledge import (
    EvidenceRepository,
    KnowledgeBaseRepository,
    scoped_chunks,
)
from reqpilot.retrieval.contracts import (
    EmptyReason,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalResult,
)
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.rules.compliance import (
    ComplianceRules,
    SecurityRules,
    load_compliance_rules,
    load_security_rules,
)
from reqpilot.services.knowledge import KnowledgeAdminService, KnowledgeScopeService
from reqpilot.services.knowledge.seed import seed_from_manifest
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent
from tests.kb_helpers import as_retrieved
from tests.p3_helpers import (
    RULES_DIR,
    TEST_SETTINGS,
    extraction_rules,
    ingest,
    member,
    retrieval_rules,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "data" / "dev" / "compliance" / "p6_compliance_synthetic.yaml"
KB_MANIFEST = REPO_ROOT / "data" / "dev" / "compliance" / "kb_manifest.yaml"

_VERSION = re.compile(r"\(version ([0-9a-f-]{36})\)")
_EVIDENCE = re.compile(
    r"\[evidence_id=([0-9a-f-]{36})\] (.*?) \| type=(\w+) .*?\| jurisdiction=(\w+)[^\n]*\n([^\n]*)"
)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "for",
        "with",
        "in",
        "on",
        "by",
        "be",
        "is",
        "are",
        "shall",
        "must",
        "will",
        "which",
        "that",
        "this",
        "after",
        "before",
        "when",
        "its",
        "their",
        "it",
        "as",
        "at",
        "from",
    ]
)


def compliance_rules() -> ComplianceRules:
    return load_compliance_rules(RULES_DIR)


def security_rules() -> SecurityRules:
    return load_security_rules(RULES_DIR)


def fixture() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The retrieval test double (SQLite)
# ---------------------------------------------------------------------------


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


@dataclass
class FixtureRetriever:
    """Allowlist-joined lexical retrieval over stored chunks, for SQLite tests only."""

    session: Session
    actor: Actor
    queries: list[RetrievalQuery] = field(default_factory=list)
    #: Force ``RETRIEVAL_EMPTY`` for statements containing any of these words.
    empty_for: tuple[str, ...] = ()

    def __call__(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        project_id = ProjectId(query.project_id)
        as_of = query.as_of or dt.date(2026, 9, 22)
        scope = EvidenceRepository(self.session, self.actor).load_scope(project_id, as_of)
        assert scope is not None
        allowlisted = int(
            self.session.scalar(
                select(func.count())
                .select_from(SourceAllowlist)
                .where(SourceAllowlist.project_id == project_id)
            )
            or 0
        )
        base: dict[str, Any] = {
            "retrieval_id": new_retrieval_id(),
            "project_id": project_id,
            "query_hash": query.query_hash,
            "as_of": as_of,
            "kb_version": scope.kb_version,
            "kb_version_pinned": scope.kb_version_pin is not None,
            "embedding_model": HashingEmbeddingProvider().model_id,
            "ruleset_version": "1.0.0",
            "classification": query.classification,
        }
        if not scope.jurisdictions:
            return RetrievalResult.empty(EmptyReason.NO_JURISDICTION_SCOPE, **base)
        if not allowlisted:
            return RetrievalResult.empty(EmptyReason.NO_ALLOWLISTED_SOURCES, **base)
        if any(w in query.text.lower() for w in self.empty_for):
            return RetrievalResult.empty(EmptyReason.NOTHING_RELEVANT, **base)
        rows = self.session.execute(
            scoped_chunks(select(KnowledgeChunk, KnowledgeItem, NormativeSource), scope)
        ).all()
        tags = query.classification.applicability
        wanted = _words(query.text)
        scored = []
        for chunk, item, _source in rows:
            item_tags = set(item.applicability or ())
            if tags and item_tags and not (item_tags & tags):
                continue
            score = len(wanted & _words(chunk.text))
            if score:
                scored.append((-score, item.item_key, chunk))
        scored.sort(key=lambda t: (t[0], t[1]))
        chunks = [c for _s, _k, c in scored[: query.top_k or 8]]
        if not chunks:
            return RetrievalResult.empty(EmptyReason.NOTHING_RELEVANT, **base)
        return RetrievalResult(
            outcome=RetrievalOutcome.SUCCESS,
            requires_human_review=False,
            chunks=as_retrieved(self.session, chunks),
            **base,
        )


# ---------------------------------------------------------------------------
# The scripted model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSeen:
    evidence_id: str
    title: str
    source_type: str
    jurisdiction: str
    quote: str


def requirement_text(request: LLMRequest) -> str:
    fenced = request.untrusted_content["requirement"]
    return "\n".join(fenced.splitlines()[1:-1])


def version_id(request: LLMRequest) -> str:
    match = _VERSION.search(request.instructions)
    assert match, "the P6 prompts name the requirement version"
    return match.group(1)


def evidence_seen(request: LLMRequest) -> list[EvidenceSeen]:
    return [EvidenceSeen(*m) for m in _EVIDENCE.findall(request.untrusted_content["evidence"])]


def cite(request: LLMRequest, phrase: str) -> list[EvidenceSeen]:
    return [e for e in evidence_seen(request) if phrase.lower() in e.quote.lower()]


#: (words of the requirement, control key, words of the evidence, relationship)
MAPPING_TABLE: tuple[tuple[str, str, str, str], ...] = (
    (
        "retained for eight years",
        "LO-RET-APPLICATION-RECORDS",
        "retained for eight years",
        "addresses",
    ),
    ("multi-factor", "LO-AUTH-MFA-PRIVILEGED", "second factor", "addresses"),
    ("consent", "LO-PRV-CONSENT", "recorded consent", "addresses"),
    (
        "second loan officer",
        "LO-TXN-DISBURSEMENT-INTEGRITY",
        "second authorised officer",
        "addresses",
    ),
    (
        "second loan officer",
        "LO-APR-SANCTION-CHECKPOINT",
        "second authorised officer",
        "partially_addresses",
    ),
)

#: (words of the requirement, category, family, proposed level, words of evidence)
SECURITY_TABLE: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("multi-factor", "security", "authentication", "low", "second factor"),
    (
        "second loan officer",
        "security",
        "transaction_integrity",
        "medium",
        "second authorised officer",
    ),
    ("retained", "privacy", "retention", "low", "retained for eight years"),
    ("consent", "privacy", "consent", "medium", "recorded consent"),
    ("date of birth", "privacy", "data_minimisation", "low", ""),
)


def mapping(
    request: LLMRequest,
    control_key: str,
    evidence: list[EvidenceSeen],
    *,
    relationship: str = "addresses",
    high: bool = False,
    rationale: str | None = None,
    candidate_text: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    first = evidence[0] if evidence else None
    return {
        "control_key": control_key,
        "relationship": relationship,
        "evidence_ids": [e.evidence_id for e in evidence],
        "jurisdiction": first.jurisdiction if first else "IN",
        "source_type": first.source_type if first else "org_policy",
        "is_high_impact_interpretation": high,
        "rationale": rationale
        or f"Candidate mapping: the requirement potentially addresses {control_key}.",
        "candidate_text": candidate_text
        or "Potentially applicable; requires review by a qualified compliance professional.",
        "implied_obligation": None,
        "review_signal": 0.7,
        **extra,
    }


def finding(
    family: str,
    statement: str,
    *,
    level: Any = "medium",
    evidence: list[EvidenceSeen] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "family": family,
        "proposed_requirement": statement,
        "rationale": f"scripted derivation for {family}",
        "evidence_ids": [e.evidence_id for e in evidence or []],
        "proposed_risk_level": level,
        "risk_rationale": "scripted",
        "review_signal": 0.6,
        **extra,
    }


@dataclass
class ScriptedComplianceModel:
    """A deterministic stand-in for the model on the P6 prompts."""

    calls: Counter = field(default_factory=Counter)
    overrides: dict[str, Callable[[LLMRequest], Any]] = field(default_factory=dict)

    def __call__(self, request: LLMRequest) -> Any:
        kind = request.prompt_template_id.split("@", 1)[0]
        self.calls[kind] += 1
        if kind in self.overrides:
            return self.overrides[kind](request)
        if kind == "compliance_mapping":
            return self.map(request)
        if kind == "security_requirement_analysis":
            return self.derive(request, "security")
        if kind == "privacy_requirement_analysis":
            return self.derive(request, "privacy")
        if kind == "requirement_quality_review":
            return json.dumps({"findings": []})
        raise AssertionError(f"unexpected prompt {kind}")

    def map(self, request: LLMRequest) -> str:
        text = requirement_text(request).lower()
        mappings = []
        for words, key, evidence_words, relationship in MAPPING_TABLE:
            cited = cite(request, evidence_words)
            if words in text and cited:
                mappings.append(mapping(request, key, cited[:1], relationship=relationship))
        return json.dumps({"requirement_version_id": version_id(request), "mappings": mappings})

    def derive(self, request: LLMRequest, category: str) -> str:
        text = requirement_text(request).lower()
        findings = []
        for words, cat, family, level, evidence_words in SECURITY_TABLE:
            if cat != category or words not in text:
                continue
            cited = cite(request, evidence_words)[:1] if evidence_words else []
            findings.append(
                finding(
                    family,
                    f"The system shall apply {family.replace('_', ' ')} controls (scripted).",
                    level=level,
                    evidence=cited,
                )
            )
        return json.dumps(
            {
                "requirement_version_id": version_id(request),
                "category": category,
                "findings": findings,
            }
        )


def scripted_gateway(
    model: ScriptedComplianceModel | None = None,
) -> tuple[LLMGateway, ScriptedComplianceModel, ScriptedProvider]:
    model = model or ScriptedComplianceModel()
    provider = ScriptedProvider(model)
    return LLMGateway(provider, settings=TEST_SETTINGS, sleep=lambda _s: None), model, provider


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


@dataclass
class P6World:
    session: Session
    project_id: ProjectId
    analyst: Actor
    reviewer_analyst: Actor
    compliance_officer: Actor
    security_reviewer: Actor
    auditor: Actor
    kb_admin: Actor
    versions: dict[str, RequirementVersion]
    gateway: LLMGateway
    model: ScriptedComplianceModel
    provider: ScriptedProvider
    retriever: FixtureRetriever

    def runner(self, gateway: LLMGateway | None = None, **kwargs: Any) -> AnalysisRunner:
        return AnalysisRunner(
            self.session,
            gateway or self.gateway,
            extraction_rules(),
            settings=TEST_SETTINGS,
            compliance_rules=compliance_rules(),
            security_rules=security_rules(),
            retriever=kwargs.pop("retriever", self.retriever),
            **kwargs,
        )

    def analyse(self, **kwargs: Any) -> RunSummary:
        gateway = kwargs.pop("gateway", None)
        return self.runner(gateway).analyse_compliance(
            actor=kwargs.pop("actor", self.analyst), project_id=self.project_id, **kwargs
        )

    def key_of(self, version_id: uuid.UUID) -> str:
        return next(k for k, v in self.versions.items() if v.id == version_id)


def seed_kb(session: Session, kb_admin: Actor, project_id: ProjectId) -> None:
    admin = KnowledgeAdminService(
        session, kb_admin, embedder=HashingEmbeddingProvider(), rules=retrieval_rules()
    )
    seed_from_manifest(KB_MANIFEST, admin, KnowledgeBaseRepository(session, kb_admin))
    scope = KnowledgeScopeService(session, kb_admin)
    scope.set_scope(project_id, jurisdiction_scope=["IN"], kb_version_pin=None)
    for source in KnowledgeBaseRepository(session, kb_admin).list_sources():
        if source.title.endswith("(fictional)") and source.jurisdiction == "IN":
            scope.allow(project_id, source.id)


def seed_requirements(
    session: Session,
    analyst: Actor,
    project_id: ProjectId,
    requirements: list[dict[str, Any]],
    *,
    domain: str = "LOAN",
) -> dict[str, RequirementVersion]:
    """Each statement becomes a requirement's first version, citing a synthetic source."""
    document = ingest(
        session,
        analyst,
        project_id,
        "\n".join(r["statement"] for r in requirements),
        title="P6 statements (synthetic)",
    )
    service = RequirementService(session, analyst)
    versions: dict[str, RequirementVersion] = {}
    for item in requirements:
        _requirement, version = service.create_requirement(
            project_id=project_id,
            domain=domain,
            content=RequirementContent(
                statement=" ".join(item["statement"].split()),
                category=RequirementCategory(item["category"]),
                source_refs=({"kind": "stakeholder_statement", "document": str(document.id)},),
            ),
        )
        versions[item["key"]] = version
    return versions


def make_world(session: Session, name: str = "P6 retail loan origination (synthetic)") -> P6World:
    from tests.workflow.test_p1_exit_test import make_project

    project = make_project(session, name)
    suffix = uuid.uuid4().hex[:6]
    analyst = member(session, project, Role.ANALYST, f"analyst-{suffix}@example.test")
    reviewer = member(session, project, Role.ANALYST, f"reviewer-{suffix}@example.test")
    officer = member(session, project, Role.COMPLIANCE_OFFICER, f"co-{suffix}@example.test")
    security = member(session, project, Role.SECURITY_REVIEWER, f"sr-{suffix}@example.test")
    auditor = member(session, project, Role.AUDITOR, f"auditor-{suffix}@example.test")
    kb_admin = member(session, project, Role.KB_ADMIN, f"kb-{suffix}@example.test")
    project_id = ProjectId(project.id)
    seed_kb(session, kb_admin, project_id)
    versions = seed_requirements(session, analyst, project_id, fixture()["requirements"])
    gateway, model, provider = scripted_gateway()
    return P6World(
        session=session,
        project_id=project_id,
        analyst=analyst,
        reviewer_analyst=reviewer,
        compliance_officer=officer,
        security_reviewer=security,
        auditor=auditor,
        kb_admin=kb_admin,
        versions=versions,
        gateway=gateway,
        model=model,
        provider=provider,
        retriever=FixtureRetriever(session, analyst),
    )
