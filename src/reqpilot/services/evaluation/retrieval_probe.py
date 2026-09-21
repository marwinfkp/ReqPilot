"""The ET-06 retrieval probe: recall@5 on a 20-question probe set (P2 exit gate).

Approved Phase 0 H.2 sets **ET-06: retrieval recall@5 >= 0.80 on a 20-question
probe set**, marked "re-baseline after P2". This module computes it,
deterministically, through the real retrieval service - so the probe exercises
exactly the allowlist, jurisdiction, date and status predicates production uses.

It cannot manufacture the measurement. A result counts toward ET-06 only when:

* the probe has at least :data:`ET06_MIN_QUESTIONS` questions,
* retrieval used the approved embedding model (ADR-005), and
* the probe is not marked synthetic.

A probe over synthetic development fixtures is useful for testing this harness
and is reported as such, with ``counts_toward_et06 = False``. The real probe
needs the curated corpus and a question set written against it, both of which
are human curation work (approved Phase 0 D.2, Q2).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from reqpilot.retrieval.contracts import QueryClassification, RetrievalOutcome, RetrievalQuery
from reqpilot.retrieval.embeddings import APPROVED_EMBEDDING_MODEL
from reqpilot.services.knowledge.retrieval import RetrievalService

#: ET-06 target and probe size, from approved Phase 0 H.2.
ET06_TARGET = 0.80
ET06_MIN_QUESTIONS = 20
ET06_K = 5


class ProbeQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str
    query: str = Field(min_length=1)
    #: Item keys any one of which counts as a hit. Keys, not ids: item ids are
    #: generated at curation time, keys are stable across seedings and versions.
    expected_item_keys: frozenset[str] = Field(min_length=1)
    classification: QueryClassification = QueryClassification()


class RetrievalProbe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    synthetic: bool
    questions: tuple[ProbeQuestion, ...] = Field(min_length=1)

    @property
    def digest(self) -> str:
        """sha256 of the probe's canonical form, recorded with every report.

        Sets are sorted explicitly: their iteration order is hash-randomised per
        process, and a digest that changed between runs would be worthless.
        """
        canonical = {
            "name": self.name,
            "synthetic": self.synthetic,
            "questions": [
                {
                    "question_id": q.question_id,
                    "query": q.query,
                    "expected_item_keys": sorted(q.expected_item_keys),
                    "source_types": sorted(str(t) for t in q.classification.source_types),
                    "applicability": sorted(q.classification.applicability),
                    "requirement_category": (
                        str(q.classification.requirement_category)
                        if q.classification.requirement_category
                        else None
                    ),
                }
                for q in self.questions
            ],
        }
        encoded = json.dumps(canonical, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ProbeQuestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    hit: bool
    first_hit_rank: int | None
    retrieved_item_keys: tuple[str, ...]
    outcome: RetrievalOutcome


class ProbeReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    probe_name: str
    probe_digest: str
    k: int
    questions: int
    hits: int
    recall_at_k: float
    embedding_model: str
    ruleset_version: str
    kb_version: int
    synthetic: bool
    target: float = ET06_TARGET
    counts_toward_et06: bool
    meets_target: bool
    not_counted_because: tuple[str, ...] = ()
    results: tuple[ProbeQuestionResult, ...]


def run_probe(
    probe: RetrievalProbe,
    service: RetrievalService,
    *,
    project_id: uuid.UUID,
    k: int = ET06_K,
) -> ProbeReport:
    """Run every probe question through retrieval and compute recall@k."""
    results: list[ProbeQuestionResult] = []
    models: set[str] = set()
    rulesets: set[str] = set()
    kb_versions: set[int] = set()
    for question in probe.questions:
        outcome = service.retrieve(
            RetrievalQuery(
                project_id=project_id,
                text=question.query,
                classification=question.classification,
                top_k=k,
            )
        )
        models.add(outcome.embedding_model)
        rulesets.add(outcome.ruleset_version)
        kb_versions.add(outcome.kb_version)
        keys = tuple(c.item_key for c in outcome.chunks[:k])
        first = next(
            (c.rank for c in outcome.chunks[:k] if c.item_key in question.expected_item_keys),
            None,
        )
        results.append(
            ProbeQuestionResult(
                question_id=question.question_id,
                hit=first is not None,
                first_hit_rank=first,
                retrieved_item_keys=keys,
                outcome=outcome.outcome,
            )
        )

    model = _single(models, "embedding model")
    hits = sum(r.hit for r in results)
    recall = hits / len(results)

    reasons: list[str] = []
    if probe.synthetic:
        reasons.append("probe is synthetic")
    if len(results) < ET06_MIN_QUESTIONS:
        reasons.append(f"probe has {len(results)} questions; ET-06 needs {ET06_MIN_QUESTIONS}")
    if model != APPROVED_EMBEDDING_MODEL:
        reasons.append(f"embedding model {model} is not the approved {APPROVED_EMBEDDING_MODEL}")
    if k != ET06_K:
        reasons.append(f"k={k}; ET-06 is defined at k={ET06_K}")

    counts = not reasons
    return ProbeReport(
        probe_name=probe.name,
        probe_digest=probe.digest,
        k=k,
        questions=len(results),
        hits=hits,
        recall_at_k=recall,
        embedding_model=model,
        ruleset_version=_single(rulesets, "ruleset version"),
        kb_version=max(kb_versions),
        synthetic=probe.synthetic,
        counts_toward_et06=counts,
        meets_target=counts and recall >= ET06_TARGET,
        not_counted_because=tuple(reasons),
        results=tuple(results),
    )


def _single(values: Sequence[str] | set[str], what: str) -> str:
    if len(values) != 1:
        raise ValueError(f"a probe run must use one {what}; saw {sorted(values)}")
    return next(iter(values))
