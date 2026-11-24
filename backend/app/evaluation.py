"""Evaluation module: runs the full agent pipeline on the synthetic benchmark
(evaluation/benchmark.json) and computes metrics against its ground truth.

Run from the command line:   python -m app.evaluation
or from the Evaluation page in the web app.

IMPORTANT LIMITATIONS (also shown in the UI and written to the results file):
* The benchmark is synthetic and its ground truth was written by the developer;
  it has not been validated by domain experts or real financial institutions.
* The rule keywords and the benchmark were written by the same author, so
  rule-mode results may be optimistic.
* Stakeholder satisfaction and time saved versus manual analysis need human
  studies and are not measured.
"""
import json
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import select

from app import config, llm
from app.database import SessionLocal, init_db
from app.models import DocumentChunk, EvaluationRun
from app.pipeline import new_context, run_pipeline

UNCLEAR_TYPES = {"ambiguity", "incompleteness", "undefined_term", "untestable", "infeasibility"}
MATCH_THRESHOLD = 0.6

LIMITATIONS = [
    "Synthetic benchmark: 4 case studies written for this project; ground truth assigned by the developer, "
    "not validated by domain experts or real financial institutions.",
    "Rule keywords and benchmark were authored by the same person, so rule-mode scores may be optimistic.",
    "Expected SDLC labels are the author's judgement (with acceptable alternatives), not decisions of "
    "experienced architects.",
    "Hallucination rate counts LLM references (requirement IDs, chunk IDs, quotes) that did not exist; "
    "in demonstration mode no LLM output exists, so it is reported as not applicable.",
    "Stakeholder satisfaction, human correction rate and time saved versus manual analysis require user "
    "studies and are not measured.",
]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(" ."), b.lower().strip(" .")).ratio()


def match_requirements(ground_truth: list[dict], extracted: list[dict]) -> dict[str, str]:
    """Greedy one-to-one matching of ground-truth to extracted requirements."""
    pairs = sorted(((_similarity(g["statement"], e["statement"]), g["id"], e["req_id"])
                    for g in ground_truth for e in extracted), reverse=True)
    matched, used = {}, set()
    for score, gid, eid in pairs:
        if score < MATCH_THRESHOLD:
            break
        if gid not in matched and eid not in used:
            matched[gid] = eid
            used.add(eid)
    return matched


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn}


def _pairs_from_findings(findings: list[dict], types: set[str]) -> set[frozenset]:
    pairs = set()
    for f in findings:
        ids = f["requirement_ids"]
        if f["issue_type"] in types and len(ids) >= 2:
            pairs |= {frozenset((a, b)) for i, a in enumerate(ids) for b in ids[i + 1:]}
    return pairs


def score_case(case: dict, ctx: dict, db) -> dict:
    gt = case["ground_truth"]
    extracted = ctx["requirements"]
    matched = match_requirements(gt["requirements"], extracted)
    extraction = prf(len(matched), len(extracted) - len(matched), len(gt["requirements"]) - len(matched))

    # Completeness: share of extracted requirements with every structured field filled in.
    complete = sum(1 for r in extracted if r["statement"] and r["categories"] and r["priority"] and r["kind"]
                   and r["acceptance_criteria"] and r["user_story"] and r["test_recommendations"])

    # Ambiguity / unclear-requirement detection over matched requirements.
    flagged = {i for f in ctx["findings"] if f["issue_type"] in UNCLEAR_TYPES for i in f["requirement_ids"]}
    tp = fp = fn = tn = 0
    for g in gt["requirements"]:
        if g["id"] not in matched:
            continue
        predicted, actual = matched[g["id"]] in flagged, g["ambiguous"]
        tp += predicted and actual
        fp += predicted and not actual
        fn += actual and not predicted
        tn += not predicted and not actual
    ambiguity = prf(tp, fp, fn)
    ambiguity["accuracy"] = round((tp + tn) / max(1, tp + fp + fn + tn), 3)

    def pair_scores(gt_pairs: list[dict], types: set[str]) -> dict:
        expected = {frozenset(matched[i] for i in p["ids"]) for p in gt_pairs if all(i in matched for i in p["ids"])}
        predicted = _pairs_from_findings(ctx["findings"], types)
        hits = len(expected & predicted)
        result = prf(hits, len(predicted) - hits, len(gt_pairs) - hits)
        result["accuracy_note"] = f"{hits}/{len(gt_pairs)} expected pairs found"
        return result

    conflicts = pair_scores(gt["conflicts"], {"conflict", "stakeholder_conflict"})
    duplicates = pair_scores(gt["duplicates"], {"duplicate"})

    # Compliance control coverage: expected topics with >= 1 mapping to a KB passage on that topic.
    chunk_ids = {m["chunk_id"] for m in ctx["mappings"] if m["chunk_id"]}
    topics_by_chunk = {c.id: set(c.topics) for c in db.scalars(select(DocumentChunk)
                                                                .where(DocumentChunk.id.in_(chunk_ids)))}
    mapped_topics = set().union(*topics_by_chunk.values()) if topics_by_chunk else set()
    gap_topics = {f["issue_type"] for f in ctx["findings"] if f["kind"] == "compliance_gap"}
    expected_topics = set(gt["expected_compliance_topics"])
    mappings = ctx["mappings"]
    checks = ctx["reference_checks"]

    ranking = [r["model"] for r in ctx["sdlc"]["ranking"]] if ctx.get("sdlc") else []
    recommended = ranking[0] if ranking else None
    return {
        "case": case["key"], "title": case["title"], "ground_truth_requirements": len(gt["requirements"]),
        "extracted_requirements": len(extracted),
        "extraction": extraction,
        "completeness": round(complete / len(extracted), 3) if extracted else 0.0,
        "ambiguity_detection": ambiguity,
        "conflict_detection": conflicts,
        "duplicate_detection": duplicates,
        "compliance_coverage": round(len(expected_topics & mapped_topics) / len(expected_topics), 3),
        "compliance_addressed": round(len(expected_topics & (mapped_topics | gap_topics)) / len(expected_topics), 3),
        "citation_correctness": round(sum(m["citation_verified"] for m in mappings) / len(mappings), 3) if mappings else None,
        "hallucination_rate": round(sum(not c["valid"] for c in checks) / len(checks), 3) if checks else None,
        "llm_references_checked": len(checks),
        "sdlc": {"recommended": recommended, "expected": gt["expected_sdlc"], "acceptable": gt["acceptable_sdlc"],
                 "exact_match": recommended == gt["expected_sdlc"],
                 "acceptable_match": recommended in gt["acceptable_sdlc"],
                 "expected_rank": ranking.index(gt["expected_sdlc"]) + 1 if gt["expected_sdlc"] in ranking else None,
                 "score": ctx["sdlc"]["ranking"][0]["score"] if ranking else None},
        "traceability_coverage": ctx["metrics"].get("traceability_coverage", 0),
        "processing_seconds": ctx["metrics"].get("duration_seconds", 0),
        "agent_status": {r["agent"]: r["status"] for r in ctx["agent_runs"]},
    }


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def aggregate(cases: list[dict]) -> dict:
    return {
        "extraction_precision": _mean([c["extraction"]["precision"] for c in cases]),
        "extraction_recall": _mean([c["extraction"]["recall"] for c in cases]),
        "extraction_f1": _mean([c["extraction"]["f1"] for c in cases]),
        "completeness": _mean([c["completeness"] for c in cases]),
        "ambiguity_accuracy": _mean([c["ambiguity_detection"]["accuracy"] for c in cases]),
        "ambiguity_f1": _mean([c["ambiguity_detection"]["f1"] for c in cases]),
        "conflict_precision": _mean([c["conflict_detection"]["precision"] for c in cases]),
        "conflict_recall": _mean([c["conflict_detection"]["recall"] for c in cases]),
        "conflict_f1": _mean([c["conflict_detection"]["f1"] for c in cases]),
        "compliance_coverage": _mean([c["compliance_coverage"] for c in cases]),
        "citation_correctness": _mean([c["citation_correctness"] for c in cases]),
        "hallucination_rate": _mean([c["hallucination_rate"] for c in cases]),
        "sdlc_exact_accuracy": _mean([float(c["sdlc"]["exact_match"]) for c in cases]),
        "sdlc_acceptable_accuracy": _mean([float(c["sdlc"]["acceptable_match"]) for c in cases]),
        "traceability_coverage": _mean([c["traceability_coverage"] for c in cases]),
        "avg_processing_seconds": _mean([c["processing_seconds"] for c in cases]),
    }


def run_benchmark(db, progress=None) -> dict:
    benchmark = json.loads((config.EVALUATION_DIR / "benchmark.json").read_text(encoding="utf-8"))
    results = []
    for index, case in enumerate(benchmark["cases"]):
        if progress:
            progress(f"Evaluating case {index + 1}/{len(benchmark['cases'])}: {case['title']}")
        ctx = new_context(db, dict(case["project"]),
                          [{"name": "Benchmark requirements", "text": case["requirements_text"]}])
        run_pipeline(ctx)
        results.append(score_case(case, ctx, db))
    return {"label": "SYNTHETIC BENCHMARK - not validated against real financial institutions",
            "mode": "openai" if llm.enabled() else "demo",
            "model": config.OPENAI_MODEL if llm.enabled() else "rule-based agents",
            "generated_at": datetime.now(timezone.utc).isoformat(), "summary": aggregate(results),
            "cases": results, "limitations": LIMITATIONS}


def run_and_store(run_id: int) -> None:
    """Background job used by the API."""
    db = SessionLocal()
    try:
        run = db.get(EvaluationRun, run_id)
        run.results = run_benchmark(db)
        run.status, run.finished_at = "completed", datetime.now(timezone.utc)
        db.commit()
        save_results_file(run.results)
    except Exception as exc:
        db.rollback()
        run = db.get(EvaluationRun, run_id)
        run.status, run.error, run.finished_at = "failed", f"{type(exc).__name__}: {exc}", datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def save_results_file(results: dict) -> str:
    path = config.EVALUATION_DIR / f"results_{results['mode']}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    init_db()
    from app.seed import seed

    with SessionLocal() as session:
        seed(session)
        started = time.perf_counter()
        output = run_benchmark(session, progress=print)
    print(json.dumps(output["summary"], indent=2))
    for case in output["cases"]:
        print(f"{case['case']:16} F1={case['extraction']['f1']:.2f} conflicts R={case['conflict_detection']['recall']:.2f} "
              f"SDLC={case['sdlc']['recommended']} (expected {case['sdlc']['expected']})")
    print("Results written to", save_results_file(output), f"in {time.perf_counter() - started:.1f}s")
