"""The P9 E9 evaluation harness on the frozen P9-SDLC-SYNTHETIC-v1 benchmark.

What is asserted is the harness's honesty, not a score: the manifest is enforced
(an edit is refused), the panel aggregate is the deterministic aggregation of
the frozen responses, the system under test is the ruleset frozen with the
benchmark, the product's own deterministic path is what is scored, no target is
invented, the synthetic status and the required E9 statement travel with every
report, and the measurement is reproducible.

E9 measures agreement between ReqPilot and a synthetic AI-generated expert-panel
simulation. It is an engineering benchmark, not independent human-expert
validation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tests.p3_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.rules.risk import packaged_risk_rules
from reqpilot.rules.sdlc import packaged_sdlc_rules
from reqpilot.services.evaluation.sdlc_eval import (
    E9_STATEMENT,
    aggregate_panel,
    check_frozen_aggregate,
    compare,
    evaluation_report,
    kendall_tau,
    load_sdlc_benchmark,
    pairwise_agreement,
    reqpilot_result,
)

pytestmark = pytest.mark.integration

BENCHMARK = REPO_ROOT / "data" / "gold" / "p9_sdlc_synthetic_v1"
FROZEN_RULES_SHA = "41730a927957ecabd369f2ee6dc3aca3da242b1e5201d83a7fb6685643340904"


def test_the_frozen_benchmark_is_intact_and_honestly_labelled() -> None:
    benchmark = load_sdlc_benchmark(BENCHMARK)
    assert benchmark.benchmark_id == "P9-SDLC-SYNTHETIC-v1"
    assert len(benchmark.cases) == 12 and len(benchmark.personas) == 5
    assert len(benchmark.responses) == 60
    manifest = benchmark.manifest
    assert manifest["targets"] == {}
    assert "not real experts" in manifest["validation_status"]
    assert "not independently validated" in manifest["validation_status"]
    assert manifest["system_under_test"]["sdlc_rules_sha256"] == FROZEN_RULES_SHA
    text = (BENCHMARK / "BENCHMARK.md").read_text(encoding="utf-8")
    assert E9_STATEMENT.split(". ")[0] in " ".join(text.split())
    assert "AI-generated" in text


def test_it_refuses_an_edited_or_padded_benchmark(tmp_path: Path) -> None:
    copy = tmp_path / "bench"
    shutil.copytree(BENCHMARK, copy)
    responses = copy / "panel_responses.jsonl"
    responses.write_bytes(responses.read_bytes().replace(b'"confidence": 3', b'"confidence": 4', 1))
    with pytest.raises(GoldSetIntegrityError, match="frozen hash"):
        load_sdlc_benchmark(copy)
    shutil.rmtree(copy)
    shutil.copytree(BENCHMARK, copy)
    (copy / "extra_case.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="unlisted"):
        load_sdlc_benchmark(copy)


def test_the_panel_aggregate_is_deterministic_and_frozen() -> None:
    benchmark = load_sdlc_benchmark(BENCHMARK)
    check_frozen_aggregate(benchmark)
    case = benchmark.cases[0].case_id
    answers = [r for r in benchmark.responses if r.case_id == case]
    once = aggregate_panel(case, answers, benchmark.candidates)
    twice = aggregate_panel(case, list(reversed(answers)), benchmark.candidates)
    assert once.as_dict() == twice.as_dict(), "the order answers arrive in changes nothing"
    assert sorted(once.ranking) == sorted(benchmark.candidates)


def test_rank_measures_by_hand() -> None:
    a = ["x", "y", "z"]
    assert kendall_tau(a, a) == 1.0 and pairwise_agreement(a, a) == 1.0
    assert kendall_tau(a, list(reversed(a))) == -1.0
    # One adjacent swap: 2 of 3 pairs agree -> pairwise 0.6667, tau (2 - 1) / 3.
    assert pairwise_agreement(a, ["y", "x", "z"]) == pytest.approx(0.6667, abs=1e-4)
    assert kendall_tau(a, ["y", "x", "z"]) == pytest.approx(1 / 3, abs=1e-4)


def test_the_report_is_reproducible_and_honest() -> None:
    benchmark = load_sdlc_benchmark(BENCHMARK)
    rules = packaged_sdlc_rules()
    risk_rules = packaged_risk_rules()
    first = evaluation_report(benchmark, compare(benchmark, risk_rules, rules), rules)
    second = evaluation_report(benchmark, compare(benchmark, risk_rules, rules), rules)
    first.pop("evaluated_at")
    second.pop("evaluated_at")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["statement"] == E9_STATEMENT
    assert first["target"] is None and "No approved numeric target" in first["target_note"]
    assert "not real experts" in first["validation_status"]
    sut = first["system_under_test"]
    assert sut["matches_ruleset_frozen_with_the_benchmark"] is True
    assert sut["sdlc_rules_sha256"] == FROZEN_RULES_SHA
    summary = first["summary"]
    assert summary["cases"] == 12
    assert set(summary["disagreement_cases"]) == {
        c["case_id"] for c in first["cases"] if not c["top1_agrees"]
    }
    # The product's own deterministic path is what was scored.
    case = benchmark.cases[0]
    result = reqpilot_result(case, risk_rules, rules)
    assert first["cases"][0]["reqpilot_ranking"] == list(result.ranking)
