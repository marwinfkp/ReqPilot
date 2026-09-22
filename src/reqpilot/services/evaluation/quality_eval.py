"""E2 (ambiguity) and E3 (conflict) detection accuracy (approved Phase 0 O.1; architecture R).

The approved definitions:

* **E2** - precision and recall against deliberately ambiguous seeded statements
  (about 40 items, half ambiguous).
* **E3** - precision and recall on a corpus with about 10 planted conflicts plus
  near-miss distractors.

The P5 roadmap exit (analysis §P) is "E2 and E3 computed on the seeded corpora
with false-positive rates reported"; the P5 brief adds seeded conflict recall
>= 0.80. The counting protocol is fixed in the benchmark's ``BENCHMARK.md``
*before* evaluation, and implemented here without discretion:

1. **The benchmark is frozen.** It is read only through its manifest, and every
   file's sha256 must match before anything is computed (R.3, D16). The hash is
   of the canonical content - UTF-8 text with CRLF as LF - so the same frozen
   file verifies alike on Windows and Linux (``domain/integrity``).
2. **E3 counts unordered pairs.** Every pair of the corpus is labelled: the
   planted conflicts are positive; every other pair, the labelled distractors
   included, is negative. A predicted positive is a pair with a persisted
   conflict, definite or potential.
3. **E2 counts statements.** A predicted positive is a statement with at least
   one ``ambiguity`` finding.
4. **Rates.** P = TP / (TP + FP); R = TP / (TP + FN); F1 = 2PR / (P + R);
   false-positive rate = FP / (FP + TN). E3 also reports the false-positive
   rate over the labelled distractors alone, and a definite-only variant.
5. **No target is invented.** The E3 exit bar (recall >= 0.80) is the P5 brief's;
   E2 has none - its target is set from this first measurement (O.1).

Nothing here reads ``data/gold/`` on its own: the caller names the benchmark.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256

MANIFEST = "manifest.json"
#: The P5 brief's exit bar for E3 (not an approved Phase 0 target).
E3_RECALL_EXIT_BAR = 0.80


def _sha256(path: Path) -> str:
    """The platform-independent content hash (UTF-8 text with CRLF as LF)."""
    return file_canonical_sha256(path)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise GoldSetIntegrityError(f"{path.name} line {number}: {exc}") from exc
    return rows


@dataclass(frozen=True)
class CorpusItem:
    item_id: str
    statement: str
    stakeholder: str | None = None


@dataclass(frozen=True)
class LabelledPair:
    pair_id: str
    a: str
    b: str
    conflict: bool
    kind: str | None
    distractor: str | None

    @property
    def key(self) -> frozenset[str]:
        return frozenset({self.a, self.b})


@dataclass(frozen=True)
class QualityBenchmark:
    name: str
    version: str
    benchmark_id: str
    manifest_sha256: str
    requirements: tuple[CorpusItem, ...]
    pairs: tuple[LabelledPair, ...]
    ambiguity: tuple[CorpusItem, ...]
    ambiguous_ids: frozenset[str]

    @property
    def conflict_pairs(self) -> frozenset[frozenset[str]]:
        return frozenset(p.key for p in self.pairs if p.conflict)

    @property
    def distractor_pairs(self) -> frozenset[frozenset[str]]:
        return frozenset(p.key for p in self.pairs if not p.conflict)


def load_quality_benchmark(directory: Path) -> QualityBenchmark:
    """Load a frozen P5 benchmark, verifying every file against its manifest first."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        raise GoldSetIntegrityError(f"{directory} has no {MANIFEST}; a benchmark must be frozen")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files: dict[str, str] = dict(manifest["files"])
    except (ValueError, KeyError, TypeError) as exc:
        raise GoldSetIntegrityError(f"{manifest_path} is malformed: {exc}") from exc
    present = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != MANIFEST
    }
    if present != set(files):
        raise GoldSetIntegrityError(
            f"the benchmark's files differ from its manifest: unlisted "
            f"{sorted(present - set(files))}, missing {sorted(set(files) - present)}"
        )
    for relative, expected in sorted(files.items()):
        if _sha256(directory / relative) != expected:
            raise GoldSetIntegrityError(
                f"{relative} does not match its frozen hash; a benchmark is never edited in "
                "place - corrections are a new version"
            )
    for required in ("requirements.jsonl", "conflicts.jsonl", "ambiguity.jsonl"):
        if required not in files:
            raise GoldSetIntegrityError(f"the benchmark has no {required}")

    requirements = tuple(
        CorpusItem(str(r["id"]), str(r["statement"]), r.get("stakeholder"))
        for r in _jsonl(directory / "requirements.jsonl")
    )
    ids = {r.item_id for r in requirements}
    if len(ids) != len(requirements):
        raise GoldSetIntegrityError("requirement ids are not unique")
    pairs = []
    for row in _jsonl(directory / "conflicts.jsonl"):
        a, b = str(row["a"]), str(row["b"])
        if a not in ids or b not in ids or a == b:
            raise GoldSetIntegrityError(f"pair {row.get('pair_id')} names an unknown requirement")
        label = str(row["label"])
        if label not in ("conflict", "no_conflict"):
            raise GoldSetIntegrityError(f"pair {row.get('pair_id')} has label {label!r}")
        pairs.append(
            LabelledPair(
                str(row["pair_id"]),
                a,
                b,
                label == "conflict",
                row.get("kind"),
                row.get("distractor"),
            )
        )
    if len({p.key for p in pairs}) != len(pairs):
        raise GoldSetIntegrityError("a pair is labelled twice")
    ambiguity_rows = _jsonl(directory / "ambiguity.jsonl")
    return QualityBenchmark(
        name=str(manifest.get("name", directory.name)),
        version=str(manifest.get("version", "")),
        benchmark_id=str(manifest.get("benchmark_id", directory.name)),
        manifest_sha256=_sha256(manifest_path),
        requirements=requirements,
        pairs=tuple(pairs),
        ambiguity=tuple(CorpusItem(str(r["id"]), str(r["statement"])) for r in ambiguity_rows),
        ambiguous_ids=frozenset(str(r["id"]) for r in ambiguity_rows if r["ambiguous"]),
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@dataclass(frozen=True)
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return round(2 * p * r / (p + r), 4)

    @property
    def false_positive_rate(self) -> float | None:
        return _ratio(self.fp, self.fp + self.tn)

    def as_dict(self) -> dict:
        return {
            **asdict(self),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "false_positive_rate": self.false_positive_rate,
        }


@dataclass(frozen=True)
class E3Report:
    confusion: Confusion
    definite_only: Confusion
    planted_conflicts: int
    labelled_distractors: int
    negative_pairs: int
    distractor_false_positives: tuple[str, ...]
    distractor_false_positive_rate: float | None
    missed: tuple[str, ...]
    unlabelled_false_positives: tuple[tuple[str, str], ...]
    recall_by_kind: dict[str, float | None] = field(default_factory=dict)

    @property
    def meets_exit_bar(self) -> bool:
        recall = self.confusion.recall
        return recall is not None and recall >= E3_RECALL_EXIT_BAR

    def as_dict(self) -> dict:
        return {
            "confusion": self.confusion.as_dict(),
            "definite_only": self.definite_only.as_dict(),
            "planted_conflicts": self.planted_conflicts,
            "labelled_distractors": self.labelled_distractors,
            "negative_pairs": self.negative_pairs,
            "distractor_false_positives": list(self.distractor_false_positives),
            "distractor_false_positive_rate": self.distractor_false_positive_rate,
            "missed": list(self.missed),
            "unlabelled_false_positives": [list(p) for p in self.unlabelled_false_positives],
            "recall_by_kind": self.recall_by_kind,
            "recall_exit_bar": E3_RECALL_EXIT_BAR,
            "meets_recall_exit_bar": self.meets_exit_bar,
        }


def _confusion_pairs(
    universe: frozenset[frozenset[str]],
    positives: frozenset[frozenset[str]],
    predicted: frozenset[frozenset[str]],
) -> Confusion:
    tp = len(predicted & positives)
    fp = len(predicted - positives)
    fn = len(positives - predicted)
    tn = len(universe) - tp - fp - fn
    return Confusion(tp, fp, fn, tn)


def compute_e3(
    benchmark: QualityBenchmark,
    predicted: Iterable[tuple[str, str, str]],
) -> E3Report:
    """E3 from predicted conflicts ``(item_a, item_b, class)`` over corpus item ids."""
    ids = [r.item_id for r in benchmark.requirements]
    universe = frozenset(frozenset(p) for p in combinations(ids, 2))
    predicted_all: set[frozenset[str]] = set()
    predicted_definite: set[frozenset[str]] = set()
    for a, b, conflict_class in predicted:
        key = frozenset({a, b})
        if key not in universe:
            raise ValueError(f"a predicted pair is not a corpus pair: {a}, {b}")
        predicted_all.add(key)
        if conflict_class == "definite":
            predicted_definite.add(key)
    positives = benchmark.conflict_pairs
    confusion = _confusion_pairs(universe, positives, frozenset(predicted_all))
    definite = _confusion_pairs(universe, positives, frozenset(predicted_definite))
    by_pair = {p.key: p for p in benchmark.pairs}
    distractor_fps = sorted(
        by_pair[k].pair_id for k in predicted_all if k in by_pair and not by_pair[k].conflict
    )
    distractors = benchmark.distractor_pairs
    kinds: dict[str, list[bool]] = {}
    for pair in benchmark.pairs:
        if pair.conflict:
            kinds.setdefault(pair.kind or "unspecified", []).append(pair.key in predicted_all)
    return E3Report(
        confusion=confusion,
        definite_only=definite,
        planted_conflicts=len(positives),
        labelled_distractors=len(distractors),
        negative_pairs=len(universe) - len(positives),
        distractor_false_positives=tuple(distractor_fps),
        distractor_false_positive_rate=_ratio(len(distractor_fps), len(distractors)),
        missed=tuple(sorted(by_pair[k].pair_id for k in positives - predicted_all)),
        unlabelled_false_positives=tuple(
            sorted(tuple(sorted(k)) for k in predicted_all if k not in by_pair)  # type: ignore[misc]
        ),
        recall_by_kind={kind: _ratio(sum(hits), len(hits)) for kind, hits in sorted(kinds.items())},
    )


@dataclass(frozen=True)
class E2Report:
    confusion: Confusion
    items: int
    ambiguous: int
    false_positives: tuple[str, ...]
    missed: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "confusion": self.confusion.as_dict(),
            "items": self.items,
            "ambiguous": self.ambiguous,
            "false_positives": list(self.false_positives),
            "missed": list(self.missed),
            "target": None,
            "target_note": "set from this first measurement (Phase 0 O.1)",
        }


def compute_e2(benchmark: QualityBenchmark, predicted_ambiguous: Iterable[str]) -> E2Report:
    ids = {item.item_id for item in benchmark.ambiguity}
    predicted = set(predicted_ambiguous)
    if predicted - ids:
        raise ValueError(f"predicted items are not in the corpus: {sorted(predicted - ids)}")
    positives = set(benchmark.ambiguous_ids)
    tp = len(predicted & positives)
    fp = len(predicted - positives)
    fn = len(positives - predicted)
    tn = len(ids) - tp - fp - fn
    return E2Report(
        confusion=Confusion(tp, fp, fn, tn),
        items=len(ids),
        ambiguous=len(positives),
        false_positives=tuple(sorted(predicted - positives)),
        missed=tuple(sorted(positives - predicted)),
    )


def evaluation_report(
    benchmark: QualityBenchmark,
    *,
    mode: str,
    e2: E2Report | None,
    e3: E3Report | None,
    model_calls: Mapping[str, int],
    all_calls_real_model: bool,
    notes: Iterable[str] = (),
) -> dict:
    """The record of one evaluation run; whether it may stand for the P5 exit."""
    counts = mode == "model" and all_calls_real_model
    return {
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "manifest_sha256": benchmark.manifest_sha256,
        "mode": mode,
        "e2": e2.as_dict() if e2 else None,
        "e3": e3.as_dict() if e3 else None,
        "model_calls": dict(model_calls),
        "all_calls_real_model": all_calls_real_model,
        "counts_for_roadmap_exit": counts,
        "counts_note": (
            "configured pipeline with a real model on every semantic call"
            if counts
            else "ablation or offline run: reported, not the roadmap-exit figure"
        ),
        "notes": list(notes),
    }
