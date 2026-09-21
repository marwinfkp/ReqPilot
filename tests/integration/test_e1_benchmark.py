"""The frozen E1 synthetic reference benchmark (E1-SYNTHETIC-v1), and the E1 runner, offline.

The benchmark is a synthetic reference benchmark: AI-generated and reviewed by the
sole project author, not an independently annotated gold standard
(``data/gold/e1_synthetic_v1/BENCHMARK.md``). These tests check that it is valid,
frozen and loadable by the unchanged harness. They also check that the runner gives
ReqPilot the transcript and nothing else. A scripted provider plays the model; no
test here calls OpenAI.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS, scripted_gateway, segment_id

from reqpilot.domain.classification import normalise_category
from reqpilot.domain.enums import GraphRunStatus, RequirementCategory
from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.llm import LLMRequest
from reqpilot.retrieval.chunking import segment_transcript_document
from reqpilot.services.evaluation.extraction_eval import (
    GOLD_TRANSCRIPT_ONE,
    Adjudication,
    compute_e1,
    load_gold_set,
)
from reqpilot.services.evaluation.extraction_predictions import predictions_for_run

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = REPO_ROOT / "data" / "gold" / "e1_synthetic_v1"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_e1", REPO_ROOT / "scripts" / "run_e1.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_e1"] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def reference_rows() -> list[dict]:
    lines = (BENCHMARK / "requirements.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# --- the benchmark itself ----------------------------------------------------------------


def test_the_benchmark_is_frozen_and_loads_through_the_unchanged_harness() -> None:
    gold = load_gold_set(BENCHMARK)
    assert (gold.name, gold.version, gold.role) == ("e1_synthetic", "1", GOLD_TRANSCRIPT_ONE)
    assert len(gold.requirements) == 60, "the approved plan sizes E1 at about 60 items"
    manifest = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark_id"] == "E1-SYNTHETIC-v1"
    assert manifest["kind"] == "synthetic_reference_benchmark"
    assert "NOT an independently annotated gold standard" in manifest["role_note"]


def test_every_reference_span_is_exactly_its_quote_and_every_statement_is_well_formed() -> None:
    gold = load_gold_set(BENCHMARK)
    (name, _digest), *_ = gold.transcript_hashes.items()
    text = (BENCHMARK / "transcripts" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    rows = reference_rows()
    assert [r["gold_id"] for r in rows] == [f"R{n:02d}" for n in range(1, 61)]
    for row in rows:
        assert text[row["char_start"] : row["char_end"]] == row["quote"], row["gold_id"]
        assert row["statement"].startswith("The system shall ")
        assert row["kind"] in {"FR", "NFR"}
        assert row["phenomena"]


def test_reference_categories_are_the_taxonomy_and_cover_all_thirteen() -> None:
    seen: set[RequirementCategory] = set()
    for row in reference_rows():
        assert 1 <= len(row["categories"]) <= 2
        for label in row["categories"]:
            category = normalise_category(label)
            assert category is not None, label
            seen.add(category)
    assert seen == set(RequirementCategory)


def test_the_difficult_cases_the_method_promises_are_present() -> None:
    tags = {tag for row in reference_rows() for tag in row["phenomena"]}
    assert {
        "explicit",
        "implicit",
        "ambiguous",
        "incomplete",
        "duplicate_stated_twice",
        "near_duplicate",
        "conflict",
        "security_sensitive",
        "regulatory",
        "performance",
        "availability",
        "audit",
    } <= tags


def test_the_transcript_segments_by_speaker_and_carries_the_injection_distractor() -> None:
    text = next((BENCHMARK / "transcripts").iterdir()).read_text(encoding="utf-8")
    turns = segment_transcript_document(text)
    assert len(turns) == 71
    (injected,) = [t for t in turns if "ignore your previous instructions" in t.chunk.text]
    assert not any(row["quote"] in injected.chunk.text for row in reference_rows()), (
        "the injected line is a distractor, never a reference requirement"
    )


def test_a_modified_benchmark_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "e1_synthetic_v1"
    shutil.copytree(BENCHMARK, copy)
    rows = (copy / "requirements.jsonl").read_text(encoding="utf-8")
    (copy / "requirements.jsonl").write_text(rows.replace("R01", "R00", 1), encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="frozen hash"):
        load_gold_set(copy)


def test_the_benchmark_contains_nothing_that_looks_like_real_data_or_secrets() -> None:
    import re

    corpus = "".join(p.read_text(encoding="utf-8") for p in BENCHMARK.rglob("*") if p.is_file())
    assert not re.search(r"\b\d{8,}\b", corpus), "no account-number-like digit runs"
    assert not re.search(r"sk-[A-Za-z0-9]{10,}|@(?!example\.test)[a-z0-9-]+\.[a-z]{2,}", corpus)


# --- the runner: transcript in, reference never ------------------------------------------------


def test_the_runner_gives_reqpilot_the_transcript_and_never_the_reference(
    db_session: Session, tmp_path: Path
) -> None:
    runner = load_runner()
    quote = "Every applicant also has to be screened against the sanctions lists."

    def responder(request: LLMRequest) -> str:
        if request.role == "classification":
            return json.dumps({"labels": [{"category": "regulatory", "review_signal": 0.9}]})
        if (
            "segments" not in request.untrusted_content
            or quote not in request.untrusted_content["segments"]
        ):
            return json.dumps({"requirements": []})
        return json.dumps(
            {
                "requirements": [
                    {
                        "candidate_key": "c1",
                        "statement": (
                            "The system shall screen every applicant against sanctions lists."
                        ),
                        "requirement_type": "functional",
                        "evidence": [{"segment_id": segment_id(request, quote), "quote": quote}],
                        "review_signal": 0.9,
                    }
                ]
            }
        )

    gateway, provider = scripted_gateway(responder)
    result = runner.extract_benchmark(
        db_session, gateway, TEST_SETTINGS, BENCHMARK, analyst_email="e1-test@example.test"
    )
    assert result.status is GraphRunStatus.COMPLETED

    # Nothing from the reference ever reached a provider request.
    sent = "".join(
        r.instructions + "".join(r.untrusted_content.values()) for r in provider.requests
    )
    rows = reference_rows()
    assert provider.requests
    for row in rows:
        assert row["statement"] not in sent, row["gold_id"]
    assert not any(marker in sent for marker in ("R01", "gold_id", "phenomena", "E1-SYNTHETIC"))

    # The harness recognises the stored transcript and sees the one prediction.
    predictions, facts = predictions_for_run(
        db_session, result.actor, result.project_id, result.run_id, result.gold
    )
    (prediction,) = predictions
    assert prediction.transcript == "loan_origination_interview_e1_synthetic.md"
    r49 = next(r for r in rows if r["gold_id"] == "R49")
    assert any(
        start < r49["char_end"] and end > r49["char_start"] for start, end in prediction.spans
    )
    assert facts.all_real_models is False, "a scripted run is never a real model"

    runner.export_run(db_session, result, TEST_SETTINGS, tmp_path)
    pairs = [json.loads(line) for line in (tmp_path / "pairs.jsonl").read_text().splitlines()]
    assert {"prediction_ref": prediction.ref, "gold_id": "R49"}.items() <= next(
        p for p in pairs if p["gold_id"] == "R49"
    ).items()
    report = compute_e1(
        result.gold,
        predictions,
        [
            Adjudication(
                p["prediction_ref"],
                p["gold_id"],
                "match" if p["gold_id"] == "R49" else "no_match",
                "test",
            )
            for p in pairs
        ],
        facts,
    )
    assert not report.counts_as_e1 and report.f1 is None, "a scripted run never yields E1"
