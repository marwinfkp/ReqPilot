"""The E1 harness (Phase 0 O.1, R.3): frozen gold sets, adjudicated matching, honest reports.

Tiny synthetic gold sets are written into a temporary directory by the tests.
They are fixtures for checking arithmetic - not gold transcript #1, and never
presented as E1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reqpilot.domain.errors import EvaluationError, GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.services.evaluation.extraction_eval import (
    GOLD_TRANSCRIPT_ONE,
    Adjudication,
    Prediction,
    RunFacts,
    compute_e1,
    load_adjudications,
    load_gold_set,
    maximum_matching,
    propose_pairs,
)

pytestmark = pytest.mark.unit

TRANSCRIPT = (
    "Priya: Applicants must upload income documents.\n"
    "Sam: Pages must load within two seconds.\n"
    "Meera: Every change must be logged.\n"
)
REAL = RunFacts("run-1", ("provider-x",), ("model-x",), ("requirement_extraction@1.0.0",), True)
SCRIPTED = RunFacts("run-2", ("scripted",), ("scripted",), ("requirement_extraction@1.0.0",), False)


def span(phrase: str) -> tuple[int, int]:
    start = TRANSCRIPT.index(phrase)
    return start, start + len(phrase)


def write_gold(root: Path, *, role: str | None = GOLD_TRANSCRIPT_ONE) -> Path:
    directory = root / "tiny_v1"
    (directory / "transcripts").mkdir(parents=True)
    (directory / "transcripts" / "t1.txt").write_text(TRANSCRIPT, encoding="utf-8")
    rows = [
        (
            "G1",
            "Applicants must upload income documents",
            "The system shall accept income documents.",
        ),
        (
            "G2",
            "Pages must load within two seconds",
            "The system shall load pages within two seconds.",
        ),
        ("G3", "Every change must be logged", "The system shall log every change."),
    ]
    lines = []
    for gold_id, phrase, statement in rows:
        start, end = span(phrase)
        lines.append(
            json.dumps(
                {
                    "gold_id": gold_id,
                    "transcript": "t1.txt",
                    "char_start": start,
                    "char_end": end,
                    "statement": statement,
                    "kind": "FR",
                }
            )
        )
    (directory / "requirements.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = {
        path.relative_to(directory).as_posix(): file_canonical_sha256(path)
        for path in directory.rglob("*")
        if path.is_file()
    }
    manifest = {
        "name": "tiny",
        "version": "1",
        "frozen_at": "2026-09-21",
        "frozen_by": "test",
        "files": files,
    }
    if role is not None:
        manifest["role"] = role
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


PREDICTIONS = [
    Prediction(
        "FR-X-001",
        "The system shall accept income documents.",
        "t1.txt",
        (span("upload income documents"),),
    ),
    Prediction(
        "NFR-X-001",
        "The system shall load pages within two seconds.",
        "t1.txt",
        (span("within two seconds"),),
    ),
    Prediction("FR-X-002", "The system shall send marketing emails.", "t1.txt", ()),
    Prediction(
        "FR-X-003",
        "The system shall upload documents.",
        "t1.txt",
        (span("Applicants must upload"),),
    ),
]


def full_adjudication(predictions=PREDICTIONS, gold=None) -> list[Adjudication]:
    pairs = propose_pairs(gold, predictions)
    matches = {("FR-X-001", "G1"), ("NFR-X-001", "G2"), ("FR-X-003", "G1")}
    return [
        Adjudication(
            p.prediction_ref,
            p.gold_id,
            "match" if (p.prediction_ref, p.gold_id) in matches else "no_match",
            "tester",
        )
        for p in pairs
    ]


def test_a_frozen_gold_set_loads_and_is_verified(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    assert (gold.name, gold.version, gold.is_gold_transcript_one) == ("tiny", "1", True)
    assert [g.gold_id for g in gold.requirements] == ["G1", "G2", "G3"]


def test_a_modified_gold_file_is_refused(tmp_path: Path) -> None:
    directory = write_gold(tmp_path)
    path = directory / "requirements.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8").replace("log every", "record every"), encoding="utf-8"
    )
    with pytest.raises(GoldSetIntegrityError, match="frozen hash"):
        load_gold_set(directory)


def test_an_unlisted_file_is_refused(tmp_path: Path) -> None:
    directory = write_gold(tmp_path)
    (directory / "extra.jsonl").write_text("{}", encoding="utf-8")
    with pytest.raises(GoldSetIntegrityError, match="differ from its manifest"):
        load_gold_set(directory)


def test_an_unfrozen_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GoldSetIntegrityError, match="must be frozen"):
        load_gold_set(tmp_path)


def test_pairs_are_proposed_by_overlap_or_similarity_only(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    pairs = {(p.prediction_ref, p.gold_id) for p in propose_pairs(gold, PREDICTIONS)}
    assert ("FR-X-001", "G1") in pairs and ("NFR-X-001", "G2") in pairs
    assert not any(ref == "FR-X-002" for ref, _ in pairs), "unrelated text is not proposed"


def test_matching_is_one_to_one() -> None:
    assert maximum_matching([("a", "G1"), ("b", "G1"), ("b", "G2")]) == [("a", "G1"), ("b", "G2")]
    assert maximum_matching([("a", "G1"), ("b", "G1")]) == [("a", "G1")]


def test_complete_adjudication_of_a_real_model_run_gives_hand_computed_p_r_f1(
    tmp_path: Path,
) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    report = compute_e1(gold, PREDICTIONS, full_adjudication(gold=gold), REAL)
    # G1 matched once (FR-X-001 and FR-X-003 cannot both have it), G2 matched: 2 of
    # 4 predictions and 2 of 3 gold items.
    assert report.matched == 2
    assert report.precision == pytest.approx(0.5)
    assert report.recall == pytest.approx(2 / 3)
    assert report.f1 == pytest.approx(2 * 0.5 * (2 / 3) / (0.5 + 2 / 3))
    assert report.counts_as_e1 and report.reasons_not_e1 == ()
    assert "# Extraction evaluation - E1" in report.to_markdown()


def test_incomplete_adjudication_gives_no_number(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    report = compute_e1(gold, PREDICTIONS, full_adjudication(gold=gold)[:1], REAL)
    assert report.f1 is None and report.matched is None and not report.counts_as_e1
    assert any("not yet adjudicated" in r for r in report.reasons_not_e1)


def test_a_stub_or_scripted_run_never_produces_a_score(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    report = compute_e1(gold, PREDICTIONS, full_adjudication(gold=gold), SCRIPTED)
    assert report.precision is None and report.recall is None and report.f1 is None
    assert not report.counts_as_e1
    assert "NOT E1" in report.to_markdown()


def test_a_set_that_is_not_gold_transcript_one_is_not_e1(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path, role=None))
    report = compute_e1(gold, PREDICTIONS, full_adjudication(gold=gold), REAL)
    assert report.f1 is not None, "the arithmetic still runs"
    assert not report.counts_as_e1
    assert any("gold transcript #1" in r for r in report.reasons_not_e1)


def test_disagreeing_adjudicators_must_be_reconciled_first(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    conflicting = [
        Adjudication("FR-X-001", "G1", "match", "a"),
        Adjudication("FR-X-001", "G1", "no_match", "b"),
    ]
    with pytest.raises(EvaluationError, match="disagree"):
        compute_e1(gold, PREDICTIONS, conflicting, REAL)


def test_adjudications_must_name_known_items(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    with pytest.raises(EvaluationError, match="unknown"):
        compute_e1(gold, PREDICTIONS, [Adjudication("FR-NOPE", "G1", "match", "a")], REAL)


def test_adjudication_files_are_validated(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        json.dumps(
            {"prediction_ref": "a", "gold_id": "G1", "verdict": "maybe", "adjudicator": "x"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvaluationError, match="verdict"):
        load_adjudications(path)


def test_the_report_states_the_provisional_target_and_limitations(tmp_path: Path) -> None:
    gold = load_gold_set(write_gold(tmp_path))
    markdown = compute_e1(gold, PREDICTIONS, full_adjudication(gold=gold), REAL).to_markdown()
    assert "ET-07 provisional target: F1 >= 0.75" in markdown
    assert "not a pass mark" in markdown and "## Limitations" in markdown
