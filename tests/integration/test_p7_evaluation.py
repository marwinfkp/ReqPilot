"""The frozen P7 benchmark (P7-RISK-SYNTHETIC-v1) and the evaluation harness.

Offline: the benchmark's integrity (on either platform's line endings), its honesty
(synthetic, no secrets, **no invented target**), the protocol's arithmetic, and the
model-free figures - the matrix and the scope guard - which are computed without a
database or a model. The pipeline figures need PostgreSQL and run from
``scripts/run_p7_eval.py``; their results are in ``docs/evaluation/p7-risk-synthetic-v1``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from tests.p3_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.risk.scope import rule_ids
from reqpilot.rules.risk import packaged_risk_rules
from reqpilot.services.evaluation.risk_eval import (
    NO_TARGET,
    load_risk_benchmark,
    matrix_scores,
    scope_guard_scores,
)

pytestmark = pytest.mark.integration

BENCHMARK = REPO_ROOT / "data" / "gold" / "p7_risk_synthetic_v1"
#: The canonical (UTF-8, LF) manifest hash, recorded when the benchmark was frozen.
FROZEN_MANIFEST_SHA256 = "4c93e887b1359e7d6a351255bf06ba85d0181124a95e86c861c3deaf0715ea94"


def test_the_frozen_benchmark_is_intact() -> None:
    benchmark = load_risk_benchmark(BENCHMARK)
    assert benchmark.manifest_sha256 == FROZEN_MANIFEST_SHA256
    assert benchmark.benchmark_id == "P7-RISK-SYNTHETIC-v1"
    assert len(benchmark.matrix_cases) == 9
    assert len(benchmark.out_of_scope_cases) == 23
    assert len(benchmark.in_scope_cases) == 20
    assert len(benchmark.adversarial) == 20


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["linux-lf", "windows-crlf"])
def test_it_verifies_on_either_platform_and_refuses_an_edit(tmp_path: Path, newline: bytes) -> None:
    copy = tmp_path / "p7"
    shutil.copytree(BENCHMARK, copy)
    for path in copy.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
    assert load_risk_benchmark(copy).manifest_sha256 == FROZEN_MANIFEST_SHA256
    cases = copy / "matrix_cases.jsonl"
    cases.write_bytes(cases.read_bytes().replace(b'"medium"', b'"low"', 1))
    with pytest.raises(GoldSetIntegrityError, match=r"matrix_cases\.jsonl does not match"):
        load_risk_benchmark(copy)


def test_an_unlisted_file_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "p7"
    shutil.copytree(BENCHMARK, copy)
    (copy / "answers.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="unlisted"):
        load_risk_benchmark(copy)


def test_the_benchmark_is_synthetic_and_secret_free() -> None:
    text = "".join(p.read_text(encoding="utf-8") for p in BENCHMARK.iterdir())
    assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", text), "no API-key-shaped string"
    for secret_shape in ("api_key", "password=", "BEGIN PRIVATE KEY"):
        assert secret_shape not in text
    meta = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert "not reviewed" in meta["review"]
    assert "AFTER the P7 implementation" in meta["reference_generation"]
    notes = (BENCHMARK / "BENCHMARK.md").read_text(encoding="utf-8")
    assert "Not an independently validated gold standard" in notes


def test_the_benchmark_sets_no_target_and_says_so() -> None:
    """Phase 0 O.1 defines no risk metric, so inventing one would be a fabrication."""
    meta = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert meta["metrics"] == []
    assert meta["targets"] == {}
    assert "No numeric target" in meta["targets_note"]
    assert "set by the project author from this first measurement" in meta["targets_note"]
    assert "No approved numeric target" in NO_TARGET


def test_the_matrix_cases_are_the_approved_architecture_table() -> None:
    """The benchmark copies architecture I.3; the implementation must equal it."""
    benchmark = load_risk_benchmark(BENCHMARK)
    scores = matrix_scores(benchmark, packaged_risk_rules())
    assert scores["correct"] is True
    assert scores["severity_agreement"] == 1.0 and scores["gate_agreement"] == 1.0
    assert scores["disagreements"] == []
    # Exactly three cells escalate, and the benchmark says which.
    escalating = [c for c in benchmark.matrix_cases if c.expected_gate == "G8"]
    assert {(str(c.likelihood), str(c.impact)) for c in escalating} == {
        ("L2", "I3"),
        ("L3", "I2"),
        ("L3", "I3"),
    }


def test_the_scope_guard_on_the_benchmarks_cases() -> None:
    scores = scope_guard_scores(load_risk_benchmark(BENCHMARK))
    assert scores["tp"] + scores["fn"] == 23
    assert scores["fp"] + scores["tn"] == 20
    # Whatever the figures, they are reported. The exit criterion is that a
    # borrower-credit proposal never becomes a risk, which the pipeline and
    # security tests prove directly.
    assert scores["precision"] is not None and scores["recall"] is not None
    assert scores["false_alarm_rate"] is not None
    assert scores["scope_rules_version"]


def test_every_out_of_scope_case_names_a_real_rule() -> None:
    benchmark = load_risk_benchmark(BENCHMARK)
    known = set(rule_ids())
    for case in benchmark.out_of_scope_cases:
        assert case.expected_rule_id in known, case.case_id
    for case in benchmark.in_scope_cases:
        assert case.expected_rule_id is None


def test_the_adversarial_cases_name_outcomes_the_design_requires() -> None:
    benchmark = load_risk_benchmark(BENCHMARK)
    from reqpilot.domain.risk.claims import RiskDropReason

    permitted = {
        "schema_refused",
        "recorded_high_and_gated",
        "no_risk_recorded_and_no_other_gate_suppressed",
    }
    for case in benchmark.adversarial:
        outcome = str(case["expected_outcome"])
        if outcome.startswith("dropped:"):
            assert outcome.split(":", 1)[1] in RiskDropReason.ALL, case["id"]
        else:
            assert outcome in permitted, case["id"]


def test_development_code_never_reads_the_benchmark() -> None:
    for path in (REPO_ROOT / "src").rglob("*.py"):
        assert "p7_risk_synthetic" not in path.read_text(encoding="utf-8"), path


def test_the_committed_reports_name_this_benchmark_and_claim_no_target() -> None:
    reports = REPO_ROOT / "docs" / "evaluation" / "p7-risk-synthetic-v1"
    for name in ("deterministic.json", "adversarial.json", "model.json"):
        report = json.loads((reports / name).read_text(encoding="utf-8"))
        assert report["manifest_sha256"] == FROZEN_MANIFEST_SHA256
        assert report["targets"] == {}
        assert "caveat" in report
        assert report["matrix"]["correct"] is True
    adversarial = json.loads((reports / "adversarial.json").read_text(encoding="utf-8"))
    assert adversarial["adversarial"]["rate"] == 1.0
    model = json.loads((reports / "model.json").read_text(encoding="utf-8"))
    assert model["all_calls_real_model"] is True
    assert model["pipeline"]["severity_from_matrix"]["rate"] == 1.0
    assert model["pipeline"]["g8_routing"]["correct"] is True


def test_the_manifest_hash_is_the_one_the_harness_verifies() -> None:
    assert file_canonical_sha256(BENCHMARK / "manifest.json") == FROZEN_MANIFEST_SHA256
