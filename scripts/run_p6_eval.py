"""Run the P6 evaluation (E5 and the supplementary figures) on a frozen benchmark.

    python scripts/run_p6_eval.py --benchmark data/gold/p6_compliance_security_synthetic_v1 \\
        --mode deterministic --out docs/evaluation/p6-cs-synthetic-v1 --database-url <postgres>
    python scripts/run_p6_eval.py ... --mode adversarial ...
    python scripts/run_p6_eval.py ... --mode model ...                         # BILLABLE

The benchmark's manifest is verified before anything runs. P6 retrieval is the P2
hybrid retrieval (pgvector + full text, allowlist join), so a PostgreSQL database
is required: it is migrated to head, used inside one transaction, and rolled back
at the end - nothing the evaluation writes is kept. The benchmark's own fictional
knowledge base is seeded through the P2 curation path, the requirements are
created as synthetic versions, the P5 quality analysis runs (deterministic: its
security/privacy signals are P6 inputs), and then the unchanged P6 pipeline -
``AnalysisRunner.analyse_compliance`` - runs. E5 and the supplementary figures are
computed by ``services/evaluation/compliance_eval.py`` under the protocol in the
benchmark's ``BENCHMARK.md``.

* ``deterministic``: no model call - rules, catalogue, gaps.
* ``adversarial``: the attacks of ``adversarial.jsonl`` replayed as scripted model
  output over the expected answers. It evaluates the deterministic validation
  layer; it says nothing about model accuracy.
* ``model``: the configured provider. Refused unless it is a real model. BILLABLE.

The API key is never printed; the reports record counts and identifiers, not content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import (
    ActorKind,
    AuditEventType,
    DataSensitivity,
    RequirementCategory,
    Role,
    SourceDocumentType,
)
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.extraction import ModelVersion
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.runs import AgentRun
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider, build_gateway
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    build_embedding_provider,
)
from reqpilot.retrieval.rules import load_retrieval_rules
from reqpilot.rules.compliance import load_compliance_rules, load_security_rules
from reqpilot.rules.extraction import load_extraction_rules
from reqpilot.rules.quality import load_quality_rules
from reqpilot.services.evaluation.compliance_eval import (
    ComplianceBenchmark,
    adversarial_scores,
    compute_e5,
    compute_supplementary,
    evaluation_report,
    language_detector_scores,
    load_compliance_benchmark,
    observe,
)
from reqpilot.services.extraction import SourceDocumentService
from reqpilot.services.knowledge import KnowledgeAdminService, KnowledgeScopeService
from reqpilot.services.knowledge.retrieval import RetrievalService
from reqpilot.services.knowledge.seed import load_manifest, seed_from_manifest
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "EVAL"
_VERSION = re.compile(r"\(version ([0-9a-f-]{36})\)")
_EVIDENCE = re.compile(r"\[evidence_id=([0-9a-f-]{36})\][^\n]*\| clause=([^\s|]+)")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def migrate(url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def member(session: Session, project: Project, role: Role, label: str) -> Actor:
    user = User(email=f"p6-eval-{label}-{uuid.uuid4().hex[:8]}@example.test", display_name=label)
    session.add(user)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    session.flush()
    return Actor(
        actor_id=ActorId(user.id),
        kind=ActorKind.HUMAN,
        roles_by_project={ProjectId(project.id): frozenset({role})},
    )


def seed_project(
    session: Session,
    settings: Settings,
    benchmark: ComplianceBenchmark,
    embedder: EmbeddingProvider,
    name: str,
) -> tuple[Actor, ProjectId, dict[uuid.UUID, str]]:
    """A fresh project over the benchmark: its KB, allowlist, scope and requirements."""
    project = Project(name=f"P6 evaluation: {name}", domain="loan_origination")
    session.add(project)
    session.flush()
    analyst = member(session, project, Role.ANALYST, "analyst")
    kb_admin = member(session, project, Role.KB_ADMIN, "kb")
    project_id = ProjectId(project.id)
    rules_dir = Path(settings.rules_dir)
    admin = KnowledgeAdminService(
        session, kb_admin, embedder=embedder, rules=load_retrieval_rules(rules_dir)
    )
    seed_from_manifest(benchmark.kb_manifest, admin, KnowledgeBaseRepository(session, kb_admin))
    scope = KnowledgeScopeService(session, kb_admin)
    scope.set_scope(project_id, jurisdiction_scope=["IN"], kb_version_pin=None)
    titles = {s.title for s in load_manifest(benchmark.kb_manifest).sources}
    for source in KnowledgeBaseRepository(session, kb_admin).list_sources():
        if source.title in titles:
            scope.allow(project_id, source.id)
    document, _created = SourceDocumentService(
        session,
        analyst,
        retrieval_rules=load_retrieval_rules(rules_dir),
        extraction_rules=load_extraction_rules(rules_dir),
    ).add_text(
        project_id=project_id,
        doc_type=SourceDocumentType.MEETING_NOTES,
        title=f"P6 benchmark requirements: {name} (synthetic)",
        text="\n".join(r.statement for r in benchmark.requirements),
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    service = RequirementService(session, analyst)
    by_version: dict[uuid.UUID, str] = {}
    for item in benchmark.requirements:
        _requirement, version = service.create_requirement(
            project_id=project_id,
            domain=DOMAIN,
            content=RequirementContent(
                statement=item.statement,
                category=RequirementCategory(item.category),
                source_refs=({"kind": "benchmark_statement", "document": str(document.id)},),
            ),
        )
        by_version[version.id] = item.requirement_id
    return analyst, project_id, by_version


def configured_embedder(settings: Settings) -> EmbeddingProvider:
    """The configured local embedding model (ADR-005), or the deterministic stand-in."""
    provider = build_embedding_provider(settings)
    try:
        provider.embed_documents(["probe"])
    except EmbeddingUnavailableError:
        return HashingEmbeddingProvider()
    return provider


# ---------------------------------------------------------------------------
# the adversarial replay (scripted model output; evaluates validation only)
# ---------------------------------------------------------------------------


class AttackReplay:
    """Expected answers from the benchmark, with the attacks of adversarial.jsonl applied."""

    def __init__(
        self,
        benchmark: ComplianceBenchmark,
        foreign_evidence: list[str],
        family_category: dict[str, str],
        control_tags: dict[str, frozenset[str]],
    ) -> None:
        self.benchmark = benchmark
        self.foreign = foreign_evidence
        self.family_category = family_category
        self.control_tags = control_tags
        self.delivered: dict[str, bool] = {}
        manifest = yaml.safe_load(benchmark.kb_manifest.read_text(encoding="utf-8"))
        self.clause_item: dict[str, str] = {}
        self.item_tags: dict[str, frozenset[str]] = {}
        for source in manifest["sources"]:
            for item in source["items"]:
                self.clause_item[str(item["clause_ref"])] = str(item["item_key"])
                self.item_tags[str(item["item_key"])] = frozenset(item["applicability"])
        self.by_statement = {
            " ".join(r.statement.split()): r.requirement_id for r in benchmark.requirements
        }

    def __call__(self, request: LLMRequest) -> str:
        kind = request.prompt_template_id.split("@", 1)[0]
        fenced = request.untrusted_content["requirement"]
        statement = " ".join("\n".join(fenced.splitlines()[1:-1]).split())
        req = self.by_statement[statement]
        version = _VERSION.search(request.instructions)
        assert version is not None
        supplied = {
            self.clause_item.get(clause, clause): evidence_id
            for evidence_id, clause in _EVIDENCE.findall(request.untrusted_content["evidence"])
        }
        if kind == "compliance_mapping":
            return json.dumps(self._map(req, version.group(1), supplied))
        category = "security" if kind == "security_requirement_analysis" else "privacy"
        return json.dumps(self._derive(req, version.group(1), category))

    def _mapping(self, key: str, evidence: list[str]) -> dict[str, Any]:
        return {
            "control_key": key,
            "relationship": "addresses",
            "evidence_ids": evidence,
            "jurisdiction": "IN",
            "source_type": "org_policy",
            "is_high_impact_interpretation": False,
            "rationale": f"Candidate mapping: the requirement potentially addresses {key}.",
            "candidate_text": "Requires review by a qualified compliance professional.",
            "implied_obligation": None,
            "review_signal": 0.6,
        }

    def _map(self, req: str, version: str, supplied: dict[str, str]) -> dict[str, Any]:
        items: dict[str, dict[str, Any]] = {}
        for (r, key), clause in self.benchmark.expected_mappings.items():
            if r == req and clause in supplied:
                items[key] = self._mapping(key, [supplied[clause]])
        for case in self.benchmark.adversarial:
            if case["requirement"] != req or case["call"] != "compliance":
                continue
            key = str(case["control_key"])
            item = items.get(key)
            if item is None:
                evidence = supplied.get(str(case["clause"])) or next(iter(supplied.values()), None)
                if evidence is None:
                    self.delivered[case["id"]] = False
                    continue
                item = items[key] = self._mapping(key, [evidence])
            mutation = dict(case.get("mutation") or {})
            if mutation.get("evidence_ids") == ["@foreign"]:
                if not self.foreign:
                    self.delivered[case["id"]] = False
                    continue
                mutation["evidence_ids"] = self.foreign[:1]
            if case["attack"] == "irrelevant_citation":
                tags = self.control_tags.get(key, frozenset())
                other = next(
                    (
                        e
                        for item_key, e in supplied.items()
                        if not (self.item_tags.get(item_key, frozenset()) & tags)
                    ),
                    None,
                )
                if other is None:
                    self.delivered[case["id"]] = False
                    continue
                mutation["evidence_ids"] = [other]
            item.update(mutation)
            self.delivered[case["id"]] = True
        return {"requirement_version_id": version, "mappings": list(items.values())}

    def _derive(self, req: str, version: str, category: str) -> dict[str, Any]:
        items: dict[str, dict[str, Any]] = {}
        for (r, family), _g3 in self.benchmark.security_pairs.items():
            if r == req and self.family_category[family] == category:
                items[family] = {
                    "family": family,
                    "proposed_requirement": f"The system shall apply {family.replace('_', ' ')} "
                    "controls to this requirement.",
                    "rationale": "expected derivation (benchmark replay)",
                    "evidence_ids": [],
                    "proposed_risk_level": "medium",
                    "risk_rationale": "replay",
                    "review_signal": 0.5,
                }
        for case in self.benchmark.adversarial:
            if case["requirement"] != req or case["call"] != category:
                continue
            family = str(case["family"])
            item = items.setdefault(
                family,
                {
                    "family": family,
                    "proposed_requirement": f"The system shall apply {family.replace('_', ' ')} "
                    "controls to this requirement.",
                    "rationale": "attack replay",
                    "evidence_ids": [],
                    "risk_rationale": "replay",
                    "review_signal": 0.5,
                },
            )
            item["proposed_risk_level"] = case.get("proposed_risk_level")
            item.update(case.get("extra_field") or {})
            self.delivered[case["id"]] = True
        return {
            "requirement_version_id": version,
            "category": category,
            "findings": list(items.values()),
        }


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def model_calls(session: Session, run_id: uuid.UUID) -> tuple[dict[str, int], bool]:
    calls: Counter[str] = Counter()
    real = True
    for run in session.scalars(select(AgentRun).where(AgentRun.graph_run_id == run_id)):
        if run.model_version_id is None:
            continue
        calls[f"{run.node}:{run.role}"] += 1
        model = session.get(ModelVersion, uuid.UUID(run.model_version_id))
        real = real and bool(model and model.is_model)
    return dict(calls), real and bool(calls)


def supporting_clause_retrieved(
    session: Session,
    benchmark: ComplianceBenchmark,
    project_id: ProjectId,
    run_id: uuid.UUID,
    requirement_of: dict[uuid.UUID, str],
) -> dict[str, Any]:
    """Whether retrieval supplied each expected mapping's supporting clause (diagnostic)."""
    from reqpilot.domain.models.knowledge import Evidence, KnowledgeItem

    supplied: dict[str, set[str]] = {}
    for event in session.scalars(
        select(AuditEvent).where(
            AuditEvent.project_id == project_id,
            AuditEvent.graph_run_id == run_id,
            AuditEvent.event_type == AuditEventType.COMPLIANCE_RETRIEVED,
        )
    ):
        req = requirement_of[uuid.UUID(str(event.subject_id))]
        keys: set[str] = set()
        for evidence_id in event.payload.get("evidence_ids", []):
            row = session.get(Evidence, uuid.UUID(evidence_id))
            item = session.get(KnowledgeItem, row.target_id) if row else None
            if item is not None:
                keys.add(item.item_key)
        supplied[req] = keys
    hits = sum(
        1
        for (req, _key), clause in benchmark.expected_mappings.items()
        if clause in supplied.get(req, set())
    )
    return {
        "supporting_clause_supplied": hits,
        "expected_mappings": len(benchmark.expected_mappings),
        "rate": round(hits / len(benchmark.expected_mappings), 4),
        "requirements_with_no_evidence": sorted(r for r, keys in supplied.items() if not keys),
    }


def schema_refusals(
    session: Session,
    benchmark: ComplianceBenchmark,
    run_id: uuid.UUID,
    version_of: dict[str, uuid.UUID],
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for case in benchmark.adversarial:
        if case.get("expected") != "schema_refused":
            continue
        version = str(version_of[str(case["requirement"])])
        out[str(case["id"])] = any(
            run.error_code == "malformed_output"
            and run.input_refs.get("requirement_version_id") == version
            and run.input_refs.get("category") == case["call"]
            for run in session.scalars(select(AgentRun).where(AgentRun.graph_run_id == run_id))
        )
    return out


def evaluate(
    benchmark: ComplianceBenchmark,
    *,
    session: Session,
    settings: Settings,
    gateway: LLMGateway,
    embedder: EmbeddingProvider,
    mode: str,
) -> dict[str, Any]:
    rules_dir = Path(settings.rules_dir)
    notes: list[str] = []
    compliance_rules = load_compliance_rules(rules_dir)
    security_rules = load_security_rules(rules_dir)

    def runner(analyst: Actor, run_gateway: LLMGateway) -> AnalysisRunner:
        return AnalysisRunner(
            session,
            run_gateway,
            load_extraction_rules(rules_dir),
            settings=settings,
            quality_rules=load_quality_rules(rules_dir),
            compliance_rules=compliance_rules,
            security_rules=security_rules,
            retriever=RetrievalService(
                session,
                analyst,
                embedder=embedder,
                rules=load_retrieval_rules(rules_dir),
                default_top_k=compliance_rules.retrieval_top_k,
            ).retrieve,
        )

    foreign: list[str] = []
    if mode == "adversarial":
        # A separate project whose evidence the attacks will try to cite.
        other_analyst, other_project, _v = seed_project(
            session, settings, benchmark, embedder, "foreign project"
        )
        other = runner(other_analyst, gateway).analyse_compliance(
            actor=other_analyst, project_id=other_project, semantic=False
        )
        foreign = [str(e) for e in other.evidence_ids]

    analyst, project_id, requirement_of = seed_project(session, settings, benchmark, embedder, mode)
    version_of = {req: vid for vid, req in requirement_of.items()}
    quality = runner(analyst, gateway).analyse_quality(
        actor=analyst, project_id=project_id, semantic=False, detect_conflicts=False
    )
    notes.append(f"P5 quality run (deterministic): status={quality.status}")

    replay: AttackReplay | None = None
    run_gateway = gateway
    if mode == "adversarial":
        (checklist,) = compliance_rules.checklists_for("loan_origination", ["IN"])
        replay = AttackReplay(
            benchmark,
            foreign,
            {f.family.value: f.category.value for f in security_rules.families},
            {c.key: c.evidence_tags for c in checklist.controls},
        )
        run_gateway = LLMGateway(ScriptedProvider(replay), settings=settings, sleep=lambda _s: None)
    summary: RunSummary = runner(analyst, run_gateway).analyse_compliance(
        actor=analyst, project_id=project_id, semantic=mode != "deterministic"
    )
    notes.append(
        f"P6 run: status={summary.status}, errors={len(summary.errors)}, "
        f"semantic_failures={summary.semantic_failures}, provider_calls={summary.provider_calls}, "
        f"tokens_in={summary.tokens_in}, tokens_out={summary.tokens_out}, "
        f"evidence={len(summary.evidence_ids)}, "
        f"evidence_unavailable={len(summary.evidence_unavailable_ids)}"
    )
    notes.append(f"embedder: {embedder.model_id}")
    notes.append(
        f"gateway provider: {run_gateway.provider_name}; model: {settings.llm_model_default}"
    )
    notes.append(
        f"checklist: {compliance_rules.ruleset_ref}; risk rules: {security_rules.ruleset_ref}"
    )

    obs = observe(session, analyst, project_id, summary.run_id, requirement_of)
    supplementary = compute_supplementary(benchmark, obs)
    supplementary["retrieval"] = supporting_clause_retrieved(
        session, benchmark, project_id, summary.run_id, requirement_of
    )
    adversarial = None
    if replay is not None:
        adversarial = adversarial_scores(
            benchmark,
            obs,
            delivered=replay.delivered,
            schema_refused=schema_refusals(session, benchmark, summary.run_id, version_of),
        )
    calls, real = model_calls(session, summary.run_id)
    return evaluation_report(
        benchmark,
        mode=mode,
        e5=compute_e5(benchmark, obs),
        supplementary=supplementary,
        language=language_detector_scores(benchmark),
        adversarial=adversarial,
        model_calls=calls,
        all_calls_real_model=real,
        notes=notes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("deterministic", "adversarial", "model"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("REQPILOT_EVAL_DATABASE_URL"),
        help="a PostgreSQL + pgvector database (P2 hybrid retrieval needs it)",
    )
    args = parser.parse_args(argv)
    if not args.database_url or not args.database_url.startswith("postgresql"):
        print(
            "refusing: P6 evaluation needs a PostgreSQL + pgvector --database-url", file=sys.stderr
        )
        return 2

    benchmark = load_compliance_benchmark(args.benchmark)  # refuses a modified benchmark
    settings = get_settings()
    gateway = build_gateway(settings)
    if args.mode == "model" and not gateway.is_model:
        print("refusing: the configured provider is not a model", file=sys.stderr)
        return 2
    migrate(args.database_url)
    engine = create_engine(args.database_url, future=True)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        report = evaluate(
            benchmark,
            session=session,
            settings=settings,
            gateway=gateway,
            embedder=configured_embedder(settings),
            mode=args.mode,
        )
    finally:
        session.close()
        transaction.rollback()  # nothing the evaluation wrote is kept
        connection.close()
        engine.dispose()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.mode}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: report[k] for k in ("mode", "e5", "model_calls")}
            | {"supplementary": report["supplementary"]},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
