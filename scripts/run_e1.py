"""Run ReqPilot on a frozen E1 benchmark and export what the E1 harness needs.

This is glue around the existing pieces, not a second evaluation framework:

* ``extract`` - **BILLABLE when a real provider is configured.** It:
  1. verifies the frozen benchmark (``load_gold_set``, which refuses a modified set);
  2. creates an evaluation project and analyst in the configured database;
  3. ingests **only the transcript text**, declared synthetic;
  4. runs the unchanged P3 batch pipeline through the configured gateway;
  5. exports the run metadata, the predictions and the harness's candidate pairs.

  The reference annotations are never passed to the pipeline or the model.
* ``diagnostics`` - offline, after adjudication. Supplementary, descriptive figures:

  * structured-output validity;
  * source-span correctness;
  * label agreement on matched items;
  * review signals.

  These are **not** E1 and have no targets.

E1 itself (P, R, F1) comes from the existing, unchanged harness::

    python scripts/evaluate_extraction.py --gold <benchmark> --run <run id> \\
        --actor <analyst id> --adjudications <verdicts.jsonl> --report e1_report.md

Usage::

    python scripts/run_e1.py extract --benchmark data/gold/e1_synthetic_v1 \\
        --out docs/evaluation/e1-synthetic-v1
    python scripts/run_e1.py diagnostics --benchmark data/gold/e1_synthetic_v1 \\
        --run <run id> --adjudications <verdicts.jsonl> --out docs/evaluation/e1-synthetic-v1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.classification import normalise_category
from reqpilot.domain.enums import (
    ActorKind,
    DataSensitivity,
    GraphRunStatus,
    Role,
    SourceDocumentType,
)
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.extraction import (
    RequirementClassification,
    ReviewItem,
    SourceDocument,
)
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.models.runs import AgentRun
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import LLMGateway, build_gateway
from reqpilot.retrieval.rules import load_retrieval_rules
from reqpilot.rules.extraction import load_extraction_rules
from reqpilot.services.evaluation.extraction_eval import (
    MANIFEST,
    GoldSet,
    load_adjudications,
    load_gold_set,
    maximum_matching,
    normalise,
    propose_pairs,
)
from reqpilot.services.evaluation.extraction_predictions import predictions_for_run
from reqpilot.services.extraction import SourceDocumentService

#: The identifier domain token for the loan-origination case study.
DOMAIN = "LOAN"


def benchmark_transcripts(directory: Path) -> dict[str, str]:
    """The transcript texts of a frozen benchmark - the only thing ReqPilot is given."""
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    return {
        relative.removeprefix("transcripts/"): normalise(
            (directory / relative).read_text(encoding="utf-8")
        )
        for relative in sorted(manifest["files"])
        if relative.startswith("transcripts/")
    }


@dataclass(frozen=True)
class ExtractionResult:
    gold: GoldSet
    project_id: uuid.UUID
    actor: Actor
    run_id: uuid.UUID
    status: GraphRunStatus
    summary: dict[str, Any]


def extract_benchmark(
    session: Session,
    gateway: LLMGateway,
    settings: Settings,
    directory: Path,
    *,
    analyst_email: str,
) -> ExtractionResult:
    """Run the unchanged P3 pipeline on the benchmark's transcript(s)."""
    gold = load_gold_set(directory)  # refuses an unfrozen or modified benchmark
    transcripts = benchmark_transcripts(directory)

    project = Project(name=f"E1 evaluation: {gold.name} v{gold.version}", domain="loan_origination")
    session.add(project)
    session.flush()
    user = User(email=analyst_email, display_name="E1 evaluation analyst")
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

    rules = load_extraction_rules(settings.rules_dir)
    sources = SourceDocumentService(
        session,
        actor,
        retrieval_rules=load_retrieval_rules(settings.rules_dir),
        extraction_rules=rules,
    )
    document_ids = []
    for name, text in transcripts.items():
        document, _created = sources.add_text(
            project_id=project_id,
            doc_type=SourceDocumentType.TRANSCRIPT,
            title=name,
            text=text,
            sensitivity=DataSensitivity.SYNTHETIC,
        )
        document_ids.append(document.id)

    summary = AnalysisRunner(session, gateway, rules, settings=settings).extract(
        actor=actor, project_id=project_id, source_ids=document_ids, domain=DOMAIN
    )
    return ExtractionResult(
        gold=gold,
        project_id=project.id,
        actor=actor,
        run_id=summary.run_id,
        status=summary.status,
        summary={
            "status": str(summary.status),
            "accepted": summary.accepted,
            "merged": summary.merged,
            "rejected": summary.rejected,
            "classified": len(summary.classified_version_ids),
            "review_items": len(summary.review_item_ids),
            "provider_calls": summary.provider_calls,
            "tokens_in": summary.tokens_in,
            "tokens_out": summary.tokens_out,
            "cost_estimate": summary.cost_estimate,
            "errors": list(summary.errors),
        },
    )


def export_run(session: Session, result: ExtractionResult, settings: Settings, out: Path) -> None:
    """Write run.json, predictions.jsonl and pairs.jsonl for adjudication."""
    predictions, facts = predictions_for_run(
        session, result.actor, ProjectId(result.project_id), result.run_id, result.gold
    )
    pairs = propose_pairs(result.gold, predictions)
    prompts = sorted(
        {
            (row.prompt_template_id or "")
            for row in session.scalars(
                select(AgentRun).where(AgentRun.graph_run_id == result.run_id)
            )
        }
        - {""}
    )
    out.mkdir(parents=True, exist_ok=True)
    run_record = {
        "benchmark": {
            "name": result.gold.name,
            "version": result.gold.version,
            "manifest_sha256": result.gold.manifest_sha256,
            "reference_count": len(result.gold.requirements),
        },
        "run_id": str(result.run_id),
        "project_id": str(result.project_id),
        "analyst_actor_id": str(result.actor.actor_id),
        "executed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "facts": asdict(facts),
        "prompt_templates": _prompt_details(session, prompts),
        "configuration": {
            "provider": str(settings.llm_provider),
            "model": settings.llm_model_default,
            "temperature": settings.llm_temperature,
            "max_retries": settings.llm_max_retries,
            "timeout_seconds": settings.llm_timeout_seconds,
            "fixture_mode": settings.llm_fixture_mode,
            "domain": DOMAIN,
            "random_seed": None,
        },
        "summary": result.summary,
        "predicted_count": len(predictions),
        "candidate_pairs": len(pairs),
    }
    (out / "run.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(asdict(p)) + "\n" for p in predictions), encoding="utf-8"
    )
    (out / "pairs.jsonl").write_text(
        "".join(json.dumps(asdict(p)) + "\n" for p in pairs), encoding="utf-8"
    )


def _prompt_details(session: Session, template_ids: list[str]) -> list[dict[str, str]]:
    from reqpilot.domain.models.extraction import PromptTemplate

    details = []
    for template_id in template_ids:
        template = session.get(PromptTemplate, uuid.UUID(template_id))
        if template is not None:
            details.append(
                {
                    "name": template.name,
                    "version": template.version,
                    "sha256": template.template_sha256,
                }
            )
    return sorted(details, key=lambda d: d["name"])


def diagnostics(
    session: Session,
    directory: Path,
    gold: GoldSet,
    run_id: uuid.UUID,
    adjudication_path: Path,
) -> dict[str, Any]:
    """Supplementary descriptive figures for a run. Not E1; no targets."""
    runs = list(session.scalars(select(AgentRun).where(AgentRun.graph_run_id == run_id)))
    llm_runs = [r for r in runs if r.model_version_id]
    by_node = Counter(r.node for r in llm_runs)
    structured = {
        "llm_invocations": len(llm_runs),
        "by_node": dict(by_node),
        "ok": sum(1 for r in llm_runs if str(r.status) == "ok"),
        "failed": [
            {"node": r.node, "error_code": r.error_code} for r in llm_runs if str(r.status) != "ok"
        ],
        "invocations_needing_more_than_one_call": sum(1 for r in llm_runs if (r.attempts or 0) > 1),
    }

    # Every requirement version the run created, via its accepted candidates.
    from reqpilot.domain.models.extraction import ExtractionCandidate

    candidates = list(
        session.scalars(
            select(ExtractionCandidate).where(ExtractionCandidate.graph_run_id == run_id)
        )
    )
    versions: dict[str, RequirementVersion] = {}
    for candidate in candidates:
        if candidate.requirement_version_id is None:
            continue
        version = session.get(RequirementVersion, candidate.requirement_version_id)
        requirement = session.get(Requirement, version.requirement_id) if version else None
        if version is not None and requirement is not None:
            versions[requirement.human_id] = version

    # Provenance: does every stored span reproduce its quote from the stored document?
    spans_total = spans_exact = 0
    for version in versions.values():
        for ref in version.source_refs or []:
            document = session.get(SourceDocument, uuid.UUID(str(ref["document"])))
            start, end = int(ref["span"][0]), int(ref["span"][1])
            spans_total += 1
            if document is not None and document.text[start:end] == ref["quote"]:
                spans_exact += 1

    adjudications = load_adjudications(adjudication_path)
    matches = maximum_matching(
        (a.prediction_ref, a.gold_id) for a in adjudications if a.verdict == "match"
    )
    reference = {g.gold_id: g for g in gold.requirements}
    reference_categories = _reference_categories(directory)

    overlap_hits = 0
    label_tp = label_fp = label_fn = exact_sets = 0
    for prediction_ref, gold_id in matches:
        version = versions.get(prediction_ref)
        item = reference[gold_id]
        if version is None:
            continue
        if any(
            max(0, min(int(r["span"][1]), item.char_end) - max(int(r["span"][0]), item.char_start))
            for r in version.source_refs or []
        ):
            overlap_hits += 1
        predicted = _current_categories(session, version.id)
        expected = reference_categories.get(gold_id, set())
        label_tp += len(predicted & expected)
        label_fp += len(predicted - expected)
        label_fn += len(expected - predicted)
        exact_sets += int(predicted == expected)

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    precision = ratio(label_tp, label_tp + label_fp)
    recall = ratio(label_tp, label_tp + label_fn)
    label_f1 = (
        round(2 * precision * recall / (precision + recall), 4) if precision and recall else None
    )
    matched_refs = {p for p, _ in matches}
    signals_matched = [
        v.review_signal
        for k, v in versions.items()
        if k in matched_refs and v.review_signal is not None
    ]
    signals_unmatched = [
        v.review_signal
        for k, v in versions.items()
        if k not in matched_refs and v.review_signal is not None
    ]
    review_items = list(
        session.scalars(select(ReviewItem).where(ReviewItem.graph_run_id == run_id))
    )
    return {
        "note": "Supplementary descriptive figures. Not E1, not approved metrics, no targets.",
        "structured_output": structured,
        "provenance": {
            "stored_spans": spans_total,
            "spans_reproducing_their_quote_exactly": spans_exact,
            "matched_items": len(matches),
            "matched_items_whose_spans_overlap_the_reference_span": overlap_hits,
        },
        "classification_on_matched_items": {
            "items": len(matches),
            "label_precision": precision,
            "label_recall": recall,
            "label_f1": label_f1,
            "exact_label_set_agreement": ratio(exact_sets, len(matches)),
            "basis": "current machine labels vs the reference 'categories', both normalised",
        },
        "review_signals": {
            "review_items_by_reason": dict(Counter(str(i.reason) for i in review_items)),
            "mean_extraction_signal_matched": _mean(signals_matched),
            "mean_extraction_signal_unmatched": _mean(signals_unmatched),
        },
    }


def _reference_categories(directory: Path) -> dict[str, set[Any]]:
    """The reference ``categories`` field, which the harness itself ignores."""
    path = directory / "requirements.jsonl"
    categories: dict[str, set[Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            labels = {normalise_category(label) for label in row.get("categories", [])}
            categories[str(row["gold_id"])] = {label for label in labels if label is not None}
    return categories


def _current_categories(session: Session, version_id: uuid.UUID) -> set[Any]:
    rows = list(
        session.scalars(
            select(RequirementClassification).where(
                RequirementClassification.requirement_version_id == version_id
            )
        )
    )
    if not rows:
        return set()
    latest = max(r.revision_no for r in rows)
    return {r.category for r in rows if r.revision_no == latest}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="run ReqPilot on the benchmark (billable)")
    extract.add_argument("--benchmark", required=True, type=Path)
    extract.add_argument("--out", required=True, type=Path)
    extract.add_argument("--analyst-email", default="e1-analyst@example.test")
    diag = commands.add_parser("diagnostics", help="supplementary figures after adjudication")
    diag.add_argument("--benchmark", required=True, type=Path)
    diag.add_argument("--run", required=True, type=uuid.UUID)
    diag.add_argument("--adjudications", required=True, type=Path)
    diag.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    settings = get_settings()
    from reqpilot.repositories.database import get_session_factory

    session = get_session_factory()()
    try:
        if args.command == "extract":
            gateway = build_gateway(settings)
            if not gateway.is_model:
                print("refusing: the configured provider is not a model", file=sys.stderr)
                return 2
            result = extract_benchmark(
                session, gateway, settings, args.benchmark, analyst_email=args.analyst_email
            )
            session.commit()
            export_run(session, result, settings, args.out)
            print(json.dumps({"run_id": str(result.run_id), **result.summary}, indent=2))
            print(f"analyst actor id: {result.actor.actor_id}")
            return 0 if result.status is GraphRunStatus.COMPLETED else 1
        gold = load_gold_set(args.benchmark)
        figures = diagnostics(session, args.benchmark, gold, args.run, args.adjudications)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "diagnostics.json").write_text(
            json.dumps(figures, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(figures, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
