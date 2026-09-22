"""The P5 E2/E3 harness and the frozen benchmark it reads.

These tests check that the benchmark is intact and that the harness counts what
its protocol says. They do not assert any E2 or E3 value: a test that pinned a
score would be pressure to tune the system - or the benchmark - to it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from tests.p3_helpers import TEST_SETTINGS
from tests.p5_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.llm import build_gateway
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.evaluation.quality_eval import (
    compute_e2,
    compute_e3,
    load_quality_benchmark,
)

pytestmark = pytest.mark.integration

BENCHMARK = REPO_ROOT / "data" / "gold" / "p5_quality_conflict_synthetic_v1"
#: The manifest's canonical (UTF-8, LF) sha256 - the same on every platform. It was
#: first recorded as 0b0dde3f..., the hash of the Windows CRLF working-tree bytes of
#: this same, unchanged manifest (see docs/08 "Cross-platform benchmark hashing").
FROZEN_MANIFEST_SHA256 = "5dd8fd66a2300a87ec2fe8ff0de81da2ed687f974872ab1058153d6569fd62b0"


def test_the_frozen_benchmark_is_intact() -> None:
    benchmark = load_quality_benchmark(BENCHMARK)
    assert benchmark.manifest_sha256 == FROZEN_MANIFEST_SHA256
    assert benchmark.benchmark_id == "P5-QC-SYNTHETIC-v1"
    assert len(benchmark.requirements) == 40
    assert len(benchmark.conflict_pairs) == 12
    assert len(benchmark.distractor_pairs) == 14
    assert len(benchmark.ambiguity) == 40 and len(benchmark.ambiguous_ids) == 20


def test_a_modified_benchmark_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "bench"
    shutil.copytree(BENCHMARK, copy)
    path = copy / "conflicts.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8").replace("conflict", "no_conflict", 1), encoding="utf-8"
    )
    with pytest.raises(GoldSetIntegrityError, match="frozen hash"):
        load_quality_benchmark(copy)
    (copy / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="differ from its manifest"):
        load_quality_benchmark(copy)


def _tiny(tmp_path: Path) -> Path:
    directory = tmp_path / "tiny"
    directory.mkdir()
    rows = {
        "requirements.jsonl": [
            {"id": "A", "statement": "a"},
            {"id": "B", "statement": "b"},
            {"id": "C", "statement": "c"},
            {"id": "D", "statement": "d"},
        ],
        "conflicts.jsonl": [
            {"pair_id": "K1", "a": "A", "b": "B", "label": "conflict", "kind": "numeric"},
            {"pair_id": "K2", "a": "C", "b": "D", "label": "conflict", "kind": "timing"},
            {"pair_id": "D1", "a": "A", "b": "C", "label": "no_conflict", "distractor": "near"},
        ],
        "ambiguity.jsonl": [
            {"id": "X", "statement": "x", "ambiguous": True},
            {"id": "Y", "statement": "y", "ambiguous": False},
            {"id": "Z", "statement": "z", "ambiguous": True},
            {"id": "W", "statement": "w", "ambiguous": False},
        ],
    }
    files = {}
    for name, lines in rows.items():
        (directory / name).write_text(
            "\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8"
        )
        files[name] = file_canonical_sha256(directory / name)
    (directory / "manifest.json").write_text(
        json.dumps({"name": "tiny", "version": "1", "files": files}), encoding="utf-8"
    )
    return directory


def test_e3_counts_pairs_as_the_protocol_says(tmp_path: Path) -> None:
    benchmark = load_quality_benchmark(_tiny(tmp_path))
    report = compute_e3(
        benchmark, [("A", "B", "definite"), ("A", "C", "potential"), ("B", "D", "potential")]
    )
    c = report.confusion
    assert (c.tp, c.fp, c.fn, c.tn) == (1, 2, 1, 2)  # 6 pairs in all
    assert c.precision == 0.3333 and c.recall == 0.5 and c.false_positive_rate == 0.5
    assert report.distractor_false_positives == ("D1",)
    assert report.distractor_false_positive_rate == 1.0
    assert report.unlabelled_false_positives == (("B", "D"),)
    assert report.missed == ("K2",)
    assert report.definite_only.tp == 1 and report.definite_only.fp == 0
    assert report.recall_by_kind == {"numeric": 1.0, "timing": 0.0}
    assert not report.meets_exit_bar
    with pytest.raises(ValueError):
        compute_e3(benchmark, [("A", "Q", "definite")])


def test_e2_counts_statements(tmp_path: Path) -> None:
    benchmark = load_quality_benchmark(_tiny(tmp_path))
    report = compute_e2(benchmark, {"X", "Y"})
    c = report.confusion
    assert (c.tp, c.fp, c.fn, c.tn) == (1, 1, 1, 1)
    assert report.false_positives == ("Y",) and report.missed == ("Z",)
    assert report.as_dict()["target"] is None


def _script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "run_p5_eval", REPO_ROOT / "scripts" / "run_p5_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_p5_eval"] = module
    spec.loader.exec_module(module)
    return module


def test_the_deterministic_evaluation_runs_offline_on_the_frozen_benchmark() -> None:
    """The script end to end, offline: no model, the stub gateway, hashing embeddings."""
    script = _script()
    report = script.evaluate(
        load_quality_benchmark(BENCHMARK),
        settings=TEST_SETTINGS,
        gateway=build_gateway(TEST_SETTINGS),
        embedder=HashingEmbeddingProvider(),
        mode="deterministic",
    )
    assert report["mode"] == "deterministic" and report["model_calls"] == {}
    assert report["counts_for_roadmap_exit"] is False
    assert report["manifest_sha256"] == FROZEN_MANIFEST_SHA256
    e3 = report["e3"]["confusion"]
    assert e3["tp"] + e3["fn"] == 12 and e3["tp"] + e3["fp"] + e3["fn"] + e3["tn"] == 780
    e2 = report["e2"]["confusion"]
    assert e2["tp"] + e2["fn"] == 20 and e2["tp"] + e2["fp"] + e2["fn"] + e2["tn"] == 40
