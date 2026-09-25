"""The P8 E6 evaluation harness on the frozen P8-TRACE-SYNTHETIC-v1 benchmark.

What is asserted is the harness's honesty - the manifest is enforced, the
product's own coverage function is what is scored, a disagreement is reported
rather than averaged, no target is invented - and the definition agreement
itself, which is a correctness check (only 100% is acceptable).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
from tests.p3_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.services.evaluation.traceability_eval import (
    NO_TARGET,
    case_graph,
    evaluate_aggregates,
    evaluate_definitions,
    evaluation_report,
    load_trace_benchmark,
)

pytestmark = pytest.mark.integration

BENCHMARK = REPO_ROOT / "data" / "gold" / "p8_traceability_synthetic_v1"


def test_the_frozen_benchmark_is_intact_and_honestly_labelled() -> None:
    benchmark = load_trace_benchmark(BENCHMARK, repo_root=REPO_ROOT)
    assert benchmark.benchmark_id == "P8-TRACE-SYNTHETIC-v1"
    assert len(benchmark.definitions) == 27 and len(benchmark.aggregates) == 5
    assert sum(1 for c in benchmark.definitions if c.expected_fully_traced) == 9
    manifest = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["targets"] == {}
    assert "not independently reviewed" in manifest["review"]
    assert "not expert validated" in manifest["review"]
    text = (BENCHMARK / "BENCHMARK.md").read_text(encoding="utf-8")
    assert "Synthetic" in text and "No target is set here" in text


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_it_verifies_on_either_platform_and_refuses_an_edit(tmp_path: Path, newline: bytes) -> None:
    copy = tmp_path / "bench"
    shutil.copytree(BENCHMARK, copy)
    for path in copy.iterdir():
        if path.suffix in {".jsonl", ".md", ".json"} and path.name != "manifest.json":
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
    load_trace_benchmark(copy)  # line endings do not change the canonical hash
    cases = copy / "definition_cases.jsonl"
    cases.write_bytes(cases.read_bytes().replace(b'"D01"', b'"D01x"', 1))
    with pytest.raises(GoldSetIntegrityError, match="frozen hash"):
        load_trace_benchmark(copy)


def test_an_unlisted_file_or_a_changed_fixture_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "bench"
    shutil.copytree(BENCHMARK, copy)
    (copy / "extra.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="unlisted"):
        load_trace_benchmark(copy)
    (copy / "extra.jsonl").unlink()
    fake_root = tmp_path / "repo"
    (fake_root / "tests").mkdir(parents=True)
    (fake_root / "tests" / "p8_helpers.py").write_text("# not the frozen fixture\n")
    with pytest.raises(GoldSetIntegrityError, match="scenario fixture"):
        load_trace_benchmark(copy, repo_root=fake_root)


def test_the_product_computes_e6_exactly_as_labelled() -> None:
    benchmark = load_trace_benchmark(BENCHMARK)
    definitions, coverages = evaluate_definitions(benchmark)
    assert definitions["disagreements"] == [], definitions["disagreements"]
    assert (definitions["agreed"], definitions["cases"]) == (27, 27)
    aggregates = evaluate_aggregates(benchmark, coverages)
    assert aggregates["agreed"] == aggregates["cases"] == 5
    empty = next(r for r in aggregates["rows"] if r["case_id"] == "A03")
    assert empty["observed"] == [0, 0, None], "an empty scope has no E6, never 1.0"


def test_a_disagreement_is_reported_not_averaged() -> None:
    benchmark = load_trace_benchmark(BENCHMARK)
    wrong = dataclasses.replace(
        benchmark.definitions[0],
        expected_missing=frozenset({"SOURCES"}),
        expected_fully_traced=False,
    )
    broken = dataclasses.replace(benchmark, definitions=(wrong, *benchmark.definitions[1:]))
    definitions, _coverages = evaluate_definitions(broken)
    assert definitions["agreed"] == 26
    assert definitions["disagreements"] == [
        {"case_id": "D01", "expected_missing": ["SOURCES"], "observed_missing": []}
    ]
    assert "DEFECT OR LABEL ERROR" in definitions["verdict"]


def test_the_case_graphs_obey_the_allowlist_and_persist_nothing() -> None:
    benchmark = load_trace_benchmark(BENCHMARK)
    for case in benchmark.definitions:
        graph, version = case_graph(case)
        for link in graph.links:
            assert link.id is None, "transient: never added to a session"
        assert version.state is case.state


def test_the_report_states_there_is_no_target(tmp_path: Path) -> None:
    benchmark = load_trace_benchmark(BENCHMARK)
    definitions, coverages = evaluate_definitions(benchmark)
    report = evaluation_report(
        benchmark, definitions, evaluate_aggregates(benchmark, coverages), None
    )
    assert report["target"] is None and report["target_note"] == NO_TARGET
    assert "not expert validated" in report["validation_status"]


def test_the_script_runs_the_scenario_end_to_end(tmp_path: Path) -> None:
    from scripts.run_p8_eval import main

    assert main(["--benchmark", str(BENCHMARK), "--out", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    steps = [m["step"] for m in summary["scenario"]]
    assert steps == ["S0", "S1", "S2", "S3", "S4", "S4-project"]
    by_step = {m["step"]: m for m in summary["scenario"]}
    # Before any artefact exists no baselined version has a RENDERED_IN path.
    assert by_step["S1"]["counts"]["baselined_with_rendered_path"] == 0
    assert by_step["S2"]["counts"]["baselined_with_rendered_path"] == by_step["S2"]["total"]
    for m in summary["scenario"]:
        assert m["e6"] is None or 0.0 <= m["e6"] <= 1.0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "There is no target here" in readme
