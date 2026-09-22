"""Run the P5 E2/E3 evaluation on a frozen quality benchmark.

    python scripts/run_p5_eval.py --benchmark data/gold/p5_quality_conflict_synthetic_v1 \\
        --mode deterministic --out docs/evaluation/p5-qc-synthetic-v1
    python scripts/run_p5_eval.py --benchmark data/gold/p5_quality_conflict_synthetic_v1 \\
        --mode model --out docs/evaluation/p5-qc-synthetic-v1        # BILLABLE

The benchmark's manifest is verified before anything runs. Each corpus is
loaded into a fresh project of a private in-memory database (the application
database is never touched), exactly as the application would hold it: one
synthetic source document, one requirement version per statement citing it and
the stakeholder it is attributed to. Then the unchanged P5 pipeline runs -
``AnalysisRunner.analyse_quality`` - and E2/E3 are computed by
``services/evaluation/quality_eval.py`` under the protocol in the benchmark's
``BENCHMARK.md``.

* ``deterministic``: the rule layer only; no model call is made.
* ``model``: the rules plus the LLM semantic layer through the configured
  gateway. Refused unless the configured provider is a real model. BILLABLE.

The API key is never printed; the report records counts, not content.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import (
    ActorKind,
    DataSensitivity,
    QualityFindingType,
    RequirementCategory,
    Role,
    SourceDocumentType,
)
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models import Base
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.extraction import ModelVersion
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.runs import AgentRun
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import LLMGateway, build_gateway
from reqpilot.retrieval.embeddings import EmbeddingProvider, build_embedding_provider
from reqpilot.retrieval.rules import load_retrieval_rules
from reqpilot.rules.extraction import load_extraction_rules
from reqpilot.rules.quality import load_quality_rules
from reqpilot.services.evaluation.quality_eval import (
    CorpusItem,
    QualityBenchmark,
    compute_e2,
    compute_e3,
    evaluation_report,
    load_quality_benchmark,
)
from reqpilot.services.extraction import SourceDocumentService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent

DOMAIN = "EVAL"


def private_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def seed(
    session: Session, settings: Settings, items: tuple[CorpusItem, ...], name: str
) -> tuple[Actor, ProjectId, dict[uuid.UUID, str]]:
    """A fresh project holding the corpus; returns version id -> corpus item id."""
    project = Project(name=f"P5 evaluation: {name}", domain="loan_origination")
    session.add(project)
    session.flush()
    user = User(email=f"p5-eval-{uuid.uuid4().hex[:8]}@example.test", display_name="P5 eval")
    session.add(user)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.ANALYST))
    session.flush()
    project_id = ProjectId(project.id)
    actor = Actor(
        actor_id=ActorId(user.id),
        kind=ActorKind.HUMAN,
        roles_by_project={project_id: frozenset({Role.ANALYST})},
    )
    document, _created = SourceDocumentService(
        session,
        actor,
        retrieval_rules=load_retrieval_rules(settings.rules_dir),
        extraction_rules=load_extraction_rules(settings.rules_dir),
    ).add_text(
        project_id=project_id,
        doc_type=SourceDocumentType.MEETING_NOTES,
        title=f"{name} (synthetic)",
        text="\n".join(item.statement for item in items),
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    service = RequirementService(session, actor)
    by_version: dict[uuid.UUID, str] = {}
    for item in items:
        ref: dict[str, Any] = {"kind": "benchmark_statement", "document": str(document.id)}
        if item.stakeholder:
            ref["stakeholder"] = item.stakeholder
        _requirement, version = service.create_requirement(
            project_id=project_id,
            domain=DOMAIN,
            content=RequirementContent(
                statement=item.statement,
                category=RequirementCategory.FUNCTIONAL,
                source_refs=(ref,),
            ),
        )
        by_version[version.id] = item.item_id
    return actor, project_id, by_version


def model_calls(session: Session, run_ids: list[uuid.UUID]) -> tuple[dict[str, int], bool]:
    calls: Counter[str] = Counter()
    real = True
    for run in session.scalars(select(AgentRun).where(AgentRun.graph_run_id.in_(run_ids))):
        if run.model_version_id is None:
            continue
        calls[str(run.role)] += 1
        model = session.get(ModelVersion, uuid.UUID(run.model_version_id))
        real = real and bool(model and model.is_model)
    return dict(calls), real and bool(calls)


def evaluate(
    benchmark: QualityBenchmark,
    *,
    settings: Settings,
    gateway: LLMGateway,
    embedder: EmbeddingProvider | None,
    mode: str,
) -> dict[str, Any]:
    semantic = mode == "model"
    session = private_session()
    rules_dir = Path(settings.rules_dir)
    notes: list[str] = []
    try:
        runner = AnalysisRunner(
            session,
            gateway,
            load_extraction_rules(rules_dir),
            settings=settings,
            quality_rules=load_quality_rules(rules_dir),
            embedder=embedder,
        )
        # E3: conflicts over the whole corpus.
        actor, project_id, e3_versions = seed(session, settings, benchmark.requirements, "E3")
        e3_run = runner.analyse_quality(actor=actor, project_id=project_id, semantic=semantic)
        predicted = [
            (e3_versions[c.version_a_id], e3_versions[c.version_b_id], str(c.conflict_class))
            for c in session.scalars(select(Conflict).where(Conflict.project_id == project_id))
        ]
        e3 = compute_e3(benchmark, predicted)
        # E2: ambiguity findings; no conflict detection needed.
        actor, project_id, e2_versions = seed(session, settings, benchmark.ambiguity, "E2")
        e2_run = runner.analyse_quality(
            actor=actor, project_id=project_id, semantic=semantic, detect_conflicts=False
        )
        ambiguous = {
            e2_versions[f.requirement_version_id]
            for f in session.scalars(
                select(QualityFinding).where(
                    QualityFinding.project_id == project_id,
                    QualityFinding.finding_type == QualityFindingType.AMBIGUITY,
                )
            )
        }
        e2 = compute_e2(benchmark, ambiguous)
        calls, real = model_calls(session, [e3_run.run_id, e2_run.run_id])
        for label, run in (("e3", e3_run), ("e2", e2_run)):
            notes.append(
                f"{label} run: status={run.status}, errors={len(run.errors)}, "
                f"semantic_failures={run.semantic_failures}, provider_calls={run.provider_calls}, "
                f"tokens_in={run.tokens_in}, tokens_out={run.tokens_out}"
            )
        notes.append(f"embedder: {embedder.model_id if embedder else 'none'}")
        notes.append(
            f"gateway provider: {gateway.provider_name}; model: {settings.llm_model_default}"
        )
        return evaluation_report(
            benchmark,
            mode=mode,
            e2=e2,
            e3=e3,
            model_calls=calls,
            all_calls_real_model=real,
            notes=notes,
        )
    finally:
        session.close()


def configured_embedder(settings: Settings) -> EmbeddingProvider | None:
    """The configured local embedding provider, or ``None`` if its model is not available."""
    provider = build_embedding_provider(settings)
    try:
        provider.embed_documents(["probe"])
    except EmbeddingUnavailableError:
        return None
    return provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("deterministic", "model"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    benchmark = load_quality_benchmark(args.benchmark)  # refuses a modified benchmark
    settings = get_settings()
    gateway = build_gateway(settings)
    if args.mode == "model" and not gateway.is_model:
        print("refusing: the configured provider is not a model", file=sys.stderr)
        return 2
    report = evaluate(
        benchmark,
        settings=settings,
        gateway=gateway,
        embedder=configured_embedder(settings),
        mode=args.mode,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.mode}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("mode", "e2", "e3", "model_calls")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
