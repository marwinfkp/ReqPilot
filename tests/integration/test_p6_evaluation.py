"""The frozen P6 benchmark (P6-CS-SYNTHETIC-v1) and the E5 harness.

Offline: the benchmark's integrity (on either platform's line endings), its
honesty (synthetic, fictional, no secrets), the protocol's arithmetic, and the
language detector against the benchmark's labelled cases. The full pipeline
evaluation needs PostgreSQL (P2 hybrid retrieval) and runs from
``scripts/run_p6_eval.py``; its results are in ``docs/evaluation/p6-cs-synthetic-v1``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from tests.p3_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.rules.compliance import load_compliance_rules, load_security_rules
from reqpilot.services.evaluation.compliance_eval import (
    E5_BAR,
    RunObservation,
    compute_e5,
    compute_supplementary,
    language_detector_scores,
    load_compliance_benchmark,
)
from reqpilot.services.knowledge.seed import load_manifest

pytestmark = pytest.mark.integration

BENCHMARK = REPO_ROOT / "data" / "gold" / "p6_compliance_security_synthetic_v1"
#: The canonical (UTF-8, LF) manifest hash, recorded when the benchmark was frozen.
FROZEN_MANIFEST_SHA256 = "2120466972db56da32ee3990033bdc0eefc080519f1e888feb5168bc9a7c3277"
RULES = REPO_ROOT / "src" / "reqpilot" / "rules" / "data"


def test_the_frozen_benchmark_is_intact() -> None:
    benchmark = load_compliance_benchmark(BENCHMARK)
    assert benchmark.manifest_sha256 == FROZEN_MANIFEST_SHA256
    assert benchmark.benchmark_id == "P6-CS-SYNTHETIC-v1"
    assert len(benchmark.requirements) == 20 and len(benchmark.references) == 16
    assert len(benchmark.expected_mappings) == 10 and len(benchmark.expected_gaps) == 4
    assert len(benchmark.adversarial) == 18 and len(benchmark.language_cases) == 30


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["linux-lf", "windows-crlf"])
def test_it_verifies_on_either_platform_and_refuses_an_edit(tmp_path: Path, newline: bytes) -> None:
    copy = tmp_path / "p6"
    shutil.copytree(BENCHMARK, copy)
    for path in copy.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
    assert load_compliance_benchmark(copy).manifest_sha256 == FROZEN_MANIFEST_SHA256
    gaps = copy / "expected_gaps.jsonl"
    gaps.write_bytes(gaps.read_bytes().replace(b"LO-RET-DISPOSAL", b"LO-RET-DISPOSAL-X", 1))
    with pytest.raises(GoldSetIntegrityError, match=r"expected_gaps\.jsonl does not match"):
        load_compliance_benchmark(copy)


def test_an_unlisted_file_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "p6"
    shutil.copytree(BENCHMARK, copy)
    (copy / "answers.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="unlisted"):
        load_compliance_benchmark(copy)


def test_the_benchmark_is_synthetic_fictional_and_secret_free() -> None:
    manifest = load_manifest(BENCHMARK / "kb_manifest.yaml")
    assert manifest.synthetic
    for source in manifest.sources:
        assert source.licence_class.value == "synthetic"
        assert source.source_type.value in ("org_policy", "best_practice")
        assert "(fictional)" in source.title and "(fictional)" in source.issuing_body
    text = "".join(p.read_text(encoding="utf-8") for p in BENCHMARK.iterdir())
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", text), "no API-key-shaped string"
    for secret_shape in ("api_key", "password=", "BEGIN PRIVATE KEY"):
        assert secret_shape not in text
    meta = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert (
        "not reviewed" in meta["review"]
        and "AFTER the P6 implementation" in meta["reference_generation"]
    )
    notes = (BENCHMARK / "BENCHMARK.md").read_text(encoding="utf-8")
    assert "Not an independently validated gold standard" in notes


def test_the_reference_crosswalk_names_real_checklist_controls() -> None:
    benchmark = load_compliance_benchmark(BENCHMARK)
    (checklist,) = load_compliance_rules(RULES).checklists_for("loan_origination", ["IN"])
    keys = {c.key for c in checklist.controls}
    for reference in benchmark.references:
        assert reference.checklist_key is None or reference.checklist_key in keys
    assert {k for _r, k in benchmark.expected_mappings} | benchmark.expected_gaps == keys
    families = {f.family.value for f in load_security_rules(RULES).families}
    assert {f for _r, f in benchmark.security_pairs} <= families
    items = {
        item.item_key
        for source in load_manifest(BENCHMARK / "kb_manifest.yaml").sources
        for item in source.items
    }
    assert set(benchmark.expected_mappings.values()) <= items
    assert {r.clause for r in benchmark.references} <= items


def test_development_code_never_reads_the_benchmark() -> None:
    for path in (REPO_ROOT / "src").rglob("*.py"):
        assert "p6_compliance_security_synthetic" not in path.read_text(encoding="utf-8"), path
    for path in (REPO_ROOT / "data" / "dev").rglob("*"):
        if path.is_file():
            assert "Fabrikam" not in path.read_text(encoding="utf-8"), path


def test_e5_is_the_share_of_reference_controls_mapped_or_gapped() -> None:
    benchmark = load_compliance_benchmark(BENCHMARK)
    nothing = compute_e5(benchmark, RunObservation())
    assert nothing["value"] == 0.0 and not nothing["meets_bar"]
    everything = RunObservation(
        covering={("PR-01", "LO-AUTH-MFA-PRIVILEGED")},
        gap_keys={r.checklist_key for r in benchmark.references if r.checklist_key}
        - {"LO-AUTH-MFA-PRIVILEGED"},
    )
    e5 = compute_e5(benchmark, everything)
    assert e5["covered"] == 14 and e5["value"] == round(14 / 16, 4)
    assert e5["missed_reference_ids"] == ["R-15", "R-16"]
    assert e5["covered_by_mapping"] == 1 and e5["bar"] == E5_BAR == 0.75


def test_the_supplementary_figures_count_as_the_protocol_says() -> None:
    benchmark = load_compliance_benchmark(BENCHMARK)
    obs = RunObservation(
        covering={("PR-01", "LO-AUTH-MFA-PRIVILEGED"), ("PR-12", "LO-PRV-CONSENT")},
        mappings=[
            ("PR-01", "LO-AUTH-MFA-PRIVILEGED", False, False),
            ("PR-12", "LO-PRV-CONSENT", True, True),
        ],
        gap_keys={"LO-RET-DISPOSAL", "LO-CRY-DATA-PROTECTION"},
        citations_total=2,
        citations_resolved=2,
        findings=[
            ("PR-01", "authentication", "high", "high", "low", "agent", True),
            ("PR-14", "session_management", "low", "low", "low", "agent", False),
        ],
    )
    figures = compute_supplementary(benchmark, obs)
    assert figures["mappings"]["tp"] == 1 and figures["mappings"]["fp"] == 1
    assert figures["mappings"]["fn"] == 9
    assert figures["gaps"]["tp"] == 1 and figures["gaps"]["fp"] == 1
    assert figures["citation_resolution"]["rate"] == 1.0
    assert figures["g2_routing"]["rate"] == 1.0
    assert figures["g3_routing"]["rate"] == 1.0 and figures["g3_routing"]["expected_g3_routed"] == 1


def test_the_language_detector_on_the_benchmarks_cases() -> None:
    scores = language_detector_scores(load_compliance_benchmark(BENCHMARK))
    assert scores["tp"] + scores["fn"] == 18 and scores["fp"] + scores["tn"] == 12
    # Whatever the figures, they are reported; the exit criterion is that nothing
    # prohibited survives validation, which the pipeline tests prove directly.
    assert scores["accuracy"] is not None


def test_the_committed_reports_name_this_benchmark() -> None:
    reports = REPO_ROOT / "docs" / "evaluation" / "p6-cs-synthetic-v1"
    for name in ("deterministic.json", "adversarial.json"):
        report = json.loads((reports / name).read_text(encoding="utf-8"))
        assert report["manifest_sha256"] == FROZEN_MANIFEST_SHA256
        assert report["e5"]["value"] >= 0.0 and "caveat" in report
    adversarial = json.loads((reports / "adversarial.json").read_text(encoding="utf-8"))
    assert adversarial["adversarial"]["all_cases_as_expected"] == 1.0


def test_the_kb_manifest_parses_with_the_p2_loader() -> None:
    raw = yaml.safe_load((BENCHMARK / "kb_manifest.yaml").read_text(encoding="utf-8"))
    assert raw["manifest_version"] == 1 and len(raw["sources"]) == 4
    assert file_canonical_sha256(BENCHMARK / "kb_manifest.yaml")
