"""E1 - extraction precision, recall and F1 (approved Phase 0 O.1; architecture R).

The approved definition: *"Extracted requirements matched against a gold set
(semantic match, adjudicated)"*, measured on the primary case study
(≈ 60 gold requirements). P3's roadmap exit is *"E1 computed against gold
transcript #1"*, and that run re-baselines ET-07 (extraction F1 ≥ 0.75,
explicitly **provisional**, H.2).

This module implements the protocol, and refuses to report a number that the
protocol does not support:

1. **The gold set is frozen.** It is read only through its manifest, and every
   file's sha256 must match before anything is computed (R.3, D16). A modified
   or unlisted file refuses the evaluation.
2. **Code proposes pairs; humans adjudicate.** "Semantic match" is a human
   judgement. :func:`propose_pairs` lists candidate (prediction, gold) pairs -
   any source-span overlap on the same transcript, or statement similarity at or
   above :data:`PAIR_PROPOSAL_SIMILARITY` - for adjudicators to decide. Only an
   adjudicated ``match`` counts. Adjudicators may also add pairs code did not
   propose.
3. **Matching is one-to-one.** From the adjudicated matches, a maximum
   bipartite matching is taken, deterministically, so one prediction can never
   satisfy two gold items or the reverse.
4. **P = matched / predicted, R = matched / gold, F1 = 2PR / (P + R).**
5. **It counts as E1 only if** the gold set is declared gold transcript #1,
   every proposed pair is adjudicated, and every extraction call was made by a
   real model - never the stub or scripted provider. Otherwise the report says
   exactly which condition failed, and gives no E1 figure.

Nothing here reads ``data/gold/`` on its own: the caller names the gold set.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from reqpilot.domain.errors import EvaluationError, GoldSetIntegrityError
from reqpilot.domain.similarity import token_jaccard

#: ET-07 (approved Phase 0 H.2): "Extraction F1 >= 0.75 - Placeholder only -
#: Re-baseline after P3". Reported beside E1 for information, never as a pass mark.
ET07_PROVISIONAL_TARGET = 0.75

#: Similarity at or above which code *proposes* a pair for adjudication. It only
#: widens what adjudicators are shown; it never decides a match.
PAIR_PROPOSAL_SIMILARITY = 0.5

#: The manifest ``role`` that declares a gold set to be gold transcript #1.
GOLD_TRANSCRIPT_ONE = "gold_transcript_1"

MANIFEST = "manifest.json"
REQUIREMENTS_FILE = "requirements.jsonl"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalise(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class GoldRequirement:
    gold_id: str
    transcript: str
    char_start: int
    char_end: int
    statement: str
    kind: str


@dataclass(frozen=True)
class GoldSet:
    name: str
    version: str
    role: str | None
    frozen_at: str
    frozen_by: str
    manifest_sha256: str
    #: transcript file name -> sha256 of its normalised text (how a stored source
    #: document is recognised as this transcript).
    transcript_hashes: dict[str, str]
    requirements: tuple[GoldRequirement, ...]

    @property
    def is_gold_transcript_one(self) -> bool:
        return self.role == GOLD_TRANSCRIPT_ONE


def load_gold_set(directory: Path) -> GoldSet:
    """Load a frozen gold set, verifying every file against its manifest first."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        raise GoldSetIntegrityError(f"{directory} has no {MANIFEST}; a gold set must be frozen")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files: dict[str, str] = dict(manifest["files"])
        name, version = str(manifest["name"]), str(manifest["version"])
        frozen_at, frozen_by = str(manifest["frozen_at"]), str(manifest["frozen_by"])
    except (ValueError, KeyError, TypeError) as exc:
        raise GoldSetIntegrityError(f"{manifest_path} is malformed: {exc}") from exc

    present = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != MANIFEST
    }
    if present != set(files):
        raise GoldSetIntegrityError(
            "the gold set's files differ from its manifest: "
            f"unlisted {sorted(present - set(files))}, missing {sorted(set(files) - present)}"
        )
    for relative, expected in sorted(files.items()):
        if _sha256_file(directory / relative) != expected:
            raise GoldSetIntegrityError(
                f"{relative} does not match its frozen hash; a gold set is never edited in "
                "place - corrections are a new version (data/gold/README.md)"
            )
    if REQUIREMENTS_FILE not in files:
        raise GoldSetIntegrityError(f"the gold set has no {REQUIREMENTS_FILE}")

    transcripts: dict[str, str] = {}
    for relative in files:
        if relative.startswith("transcripts/"):
            text = normalise((directory / relative).read_text(encoding="utf-8"))
            transcripts[relative.removeprefix("transcripts/")] = text

    requirements: list[GoldRequirement] = []
    seen: set[str] = set()
    for number, line in enumerate(
        (directory / REQUIREMENTS_FILE).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            gold = GoldRequirement(
                gold_id=str(row["gold_id"]),
                transcript=str(row["transcript"]),
                char_start=int(row["char_start"]),
                char_end=int(row["char_end"]),
                statement=str(row["statement"]),
                kind=str(row["kind"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise GoldSetIntegrityError(f"{REQUIREMENTS_FILE} line {number}: {exc}") from exc
        transcript_text = transcripts.get(gold.transcript)
        if transcript_text is None:
            raise GoldSetIntegrityError(
                f"{gold.gold_id} cites unknown transcript {gold.transcript}"
            )
        if not 0 <= gold.char_start < gold.char_end <= len(transcript_text):
            raise GoldSetIntegrityError(f"{gold.gold_id} has a span outside its transcript")
        if gold.gold_id in seen:
            raise GoldSetIntegrityError(f"gold id {gold.gold_id} appears twice")
        seen.add(gold.gold_id)
        requirements.append(gold)
    if not requirements:
        raise GoldSetIntegrityError("the gold set has no requirements")

    return GoldSet(
        name=name,
        version=version,
        role=manifest.get("role"),
        frozen_at=frozen_at,
        frozen_by=frozen_by,
        manifest_sha256=_sha256_file(manifest_path),
        transcript_hashes={
            key: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for key, text in transcripts.items()
        },
        requirements=tuple(requirements),
    )


@dataclass(frozen=True)
class Prediction:
    """One extracted requirement, as the evaluation sees it."""

    ref: str
    statement: str
    transcript: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class CandidatePair:
    prediction_ref: str
    gold_id: str
    span_overlap: int
    similarity: float


def propose_pairs(gold: GoldSet, predictions: Sequence[Prediction]) -> list[CandidatePair]:
    """Pairs for human adjudication. A proposal, never a match."""
    pairs: list[CandidatePair] = []
    for prediction in predictions:
        for item in gold.requirements:
            overlap = 0
            if prediction.transcript == item.transcript:
                for start, end in prediction.spans:
                    overlap += max(0, min(end, item.char_end) - max(start, item.char_start))
            similarity = token_jaccard(prediction.statement, item.statement)
            if overlap > 0 or similarity >= PAIR_PROPOSAL_SIMILARITY:
                pairs.append(
                    CandidatePair(prediction.ref, item.gold_id, overlap, round(similarity, 4))
                )
    return sorted(pairs, key=lambda p: (p.prediction_ref, p.gold_id))


Verdict = Literal["match", "no_match"]


@dataclass(frozen=True)
class Adjudication:
    prediction_ref: str
    gold_id: str
    verdict: Verdict
    adjudicator: str


def load_adjudications(path: Path) -> list[Adjudication]:
    rows: list[Adjudication] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            verdict = str(row["verdict"])
            if verdict not in ("match", "no_match"):
                raise ValueError(f"verdict must be match or no_match, not {verdict!r}")
            rows.append(
                Adjudication(
                    prediction_ref=str(row["prediction_ref"]),
                    gold_id=str(row["gold_id"]),
                    verdict=verdict,  # type: ignore[arg-type]
                    adjudicator=str(row["adjudicator"]),
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise EvaluationError(f"{path.name} line {number}: {exc}") from exc
    return rows


def maximum_matching(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """A maximum one-to-one matching (Kuhn's algorithm), deterministic in order."""
    adjacency: dict[str, list[str]] = {}
    for prediction, gold_id in sorted(set(pairs)):
        adjacency.setdefault(prediction, []).append(gold_id)
    owner: dict[str, str] = {}

    def augment(prediction: str, visited: set[str]) -> bool:
        for gold_id in adjacency[prediction]:
            if gold_id in visited:
                continue
            visited.add(gold_id)
            if gold_id not in owner or augment(owner[gold_id], visited):
                owner[gold_id] = prediction
                return True
        return False

    for prediction in sorted(adjacency):
        augment(prediction, set())
    return sorted((prediction, gold_id) for gold_id, prediction in owner.items())


@dataclass(frozen=True)
class RunFacts:
    """What produced the predictions - needed to decide whether it can count."""

    run_id: str
    providers: tuple[str, ...]
    models: tuple[str, ...]
    prompt_versions: tuple[str, ...]
    #: Every extraction call was made by a real model (not stub, not scripted).
    all_real_models: bool


@dataclass(frozen=True)
class E1Report:
    gold_name: str
    gold_version: str
    gold_manifest_sha256: str
    run: RunFacts
    gold_count: int
    predicted_count: int
    proposed_pairs: int
    adjudicated_pairs: int
    matched: int | None
    precision: float | None
    recall: float | None
    f1: float | None
    counts_as_e1: bool
    reasons_not_e1: tuple[str, ...]
    matching_rule: str = (
        "semantic match adjudicated by humans; code proposes candidate pairs (span overlap on "
        f"the same transcript, or token-set Jaccard >= {PAIR_PROPOSAL_SIMILARITY}); a maximum "
        "one-to-one matching is taken over pairs adjudicated 'match'"
    )
    et07_provisional_target: float = ET07_PROVISIONAL_TARGET
    limitations: tuple[str, ...] = field(
        default=(
            "the gold set is produced by the team that built the system (Phase 0 O.3)",
            "case studies are synthetic (Phase 0 O.3)",
            "ET-07 is provisional and is re-baselined by this measurement, not tested by it",
            "model output varies between live runs; recorded replays are byte-identical",
        )
    )

    def to_markdown(self) -> str:
        def fmt(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.3f}"

        status = "E1" if self.counts_as_e1 else "NOT E1"
        lines = [
            f"# Extraction evaluation - {status}",
            "",
            f"- Gold set: `{self.gold_name}` v{self.gold_version} "
            f"(manifest sha256 `{self.gold_manifest_sha256[:16]}...`)",
            f"- Run: `{self.run.run_id}`; providers {list(self.run.providers)}; "
            f"models {list(self.run.models)}; prompts {list(self.run.prompt_versions)}",
            f"- Gold items: {self.gold_count}; predicted items: {self.predicted_count}",
            f"- Candidate pairs proposed: {self.proposed_pairs}; adjudicated: "
            f"{self.adjudicated_pairs}",
            f"- Matching rule: {self.matching_rule}",
            f"- Matched: {self.matched if self.matched is not None else 'n/a'}",
            f"- Precision {fmt(self.precision)}, recall {fmt(self.recall)}, F1 {fmt(self.f1)}",
            f"- ET-07 provisional target: F1 >= {self.et07_provisional_target} "
            "(to be re-baselined from this measurement; not a pass mark)",
        ]
        if self.reasons_not_e1:
            lines += ["", "## Why this is not E1", *[f"- {r}" for r in self.reasons_not_e1]]
        lines += ["", "## Limitations", *[f"- {item}" for item in self.limitations]]
        return "\n".join(lines) + "\n"


def compute_e1(
    gold: GoldSet,
    predictions: Sequence[Prediction],
    adjudications: Sequence[Adjudication],
    run: RunFacts,
) -> E1Report:
    """Compute E1 as the protocol defines it, or report why it cannot count."""
    pred_refs = {p.ref for p in predictions}
    gold_ids = {g.gold_id for g in gold.requirements}
    if len(pred_refs) != len(predictions):
        raise EvaluationError("prediction references must be unique")
    for adjudication in adjudications:
        if adjudication.prediction_ref not in pred_refs or adjudication.gold_id not in gold_ids:
            raise EvaluationError(
                f"adjudication ({adjudication.prediction_ref}, {adjudication.gold_id}) names an "
                "unknown prediction or gold item"
            )
    verdicts: dict[tuple[str, str], set[str]] = {}
    for adjudication in adjudications:
        verdicts.setdefault((adjudication.prediction_ref, adjudication.gold_id), set()).add(
            adjudication.verdict
        )
    conflicting = sorted(pair for pair, v in verdicts.items() if len(v) > 1)
    if conflicting:
        raise EvaluationError(
            f"adjudicators disagree on {len(conflicting)} pair(s); resolve before computing E1"
        )

    proposed = propose_pairs(gold, predictions)
    undecided = [p for p in proposed if (p.prediction_ref, p.gold_id) not in verdicts]

    reasons: list[str] = []
    if not gold.is_gold_transcript_one:
        reasons.append(f"the gold set is not declared as gold transcript #1 (role={gold.role!r})")
    if not run.all_real_models:
        reasons.append(
            "the predictions were not all produced by a real model (stub or scripted provider)"
        )
    if undecided:
        reasons.append(f"{len(undecided)} proposed pair(s) are not yet adjudicated")

    # Numbers are computed only over complete adjudication of a real model's
    # output. A stub's or a script's text measures nothing, so it gets none.
    matched: int | None = None
    precision = recall = f1 = None
    if not undecided and run.all_real_models:
        matches = maximum_matching(
            pair for pair, verdict in verdicts.items() if verdict == {"match"}
        )
        matched = len(matches)
        precision = matched / len(predictions) if predictions else 0.0
        recall = matched / len(gold.requirements)
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return E1Report(
        gold_name=gold.name,
        gold_version=gold.version,
        gold_manifest_sha256=gold.manifest_sha256,
        run=run,
        gold_count=len(gold.requirements),
        predicted_count=len(predictions),
        proposed_pairs=len(proposed),
        adjudicated_pairs=len(verdicts),
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        counts_as_e1=not reasons,
        reasons_not_e1=tuple(reasons),
    )
