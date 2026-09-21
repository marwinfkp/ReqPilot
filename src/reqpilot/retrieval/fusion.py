"""Reciprocal rank fusion (architecture J.4, ADR-004).

Hybrid retrieval produces two rankings of the *same, already authorised*
candidate set - one by vector distance, one by full-text rank - and fuses them::

    score(d) = sum over rankings r of  weight_r / (k + rank_r(d))

with ranks starting at 1 and ``k`` from the ruleset (conventionally 60). A
document absent from a ranking contributes nothing from it.

Fusion only orders candidates; it never adds one. Every candidate reaching this
module came out of a query that already carried the allowlist join and the
jurisdiction, date and status predicates, so fusion cannot widen what retrieval
may return.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FusedCandidate:
    """One fused candidate, with the rank it held in each input ranking."""

    candidate_id: Any
    score: float
    ranks: Mapping[str, int] = field(default_factory=dict)

    @property
    def best_rank(self) -> int:
        return min(self.ranks.values())


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[Any]],
    *,
    weights: Mapping[str, float],
    k: int,
) -> list[FusedCandidate]:
    """Fuse ranked id lists into one deterministic ranking.

    Ties are broken by the best single rank, then by the id's string form, so the
    output never depends on dictionary or set iteration order.
    """
    if k < 1:
        raise ValueError("rrf k must be at least 1")
    unknown = set(rankings) - set(weights)
    if unknown:
        raise ValueError(f"no fusion weight for ranking(s): {sorted(unknown)}")

    scores: dict[Any, float] = {}
    ranks: dict[Any, dict[str, int]] = {}
    for name, ranked in rankings.items():
        if len(set(ranked)) != len(ranked):
            raise ValueError(f"ranking {name!r} contains a duplicate id")
        weight = weights[name]
        for position, candidate in enumerate(ranked, start=1):
            scores[candidate] = scores.get(candidate, 0.0) + weight / (k + position)
            ranks.setdefault(candidate, {})[name] = position

    fused = [FusedCandidate(candidate_id=c, score=scores[c], ranks=dict(ranks[c])) for c in scores]
    fused.sort(key=lambda f: (-f.score, f.best_rank, str(f.candidate_id)))
    return fused
