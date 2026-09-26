"""The explanation consistency check (``[DESIGN] D9``; architecture L.5; ``FR-SDL-006``, ``-007``).

The model explains a ranking that already exists. Its draft carries
*non-authoritative* assertions - the candidate it believes is first and the
scores it believes were computed - precisely so this module can compare them,
field by field, with the persisted result. It also checks that the explanation
cites only what it was given:

* every factor it cites is one of the thirteen derived factors;
* every evidence reference it cites was supplied to it;
* it addresses the computed runner-up and says what would reverse the ranking;
* it does not claim that the model chose the SDLC;
* every percentage it states is one the computation produced.

Citations are read from the structured fields *and* from inline markers in the
prose (``[factor:<id>]``, ``[ref:<kind>:<uuid>]``), so a claim cannot slip past
by living only in the narrative.

A discrepancy never changes the ranking and is never silently repaired: the
caller regenerates once, then stores the explanation with its discrepancies and
shows them (architecture L.5: "a discrepancy is never hidden").
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from reqpilot.domain.sdlc.config import SdlcConfig
from reqpilot.domain.sdlc.factors import factor_id
from reqpilot.domain.sdlc.scoring import ScoringResult

FACTOR_MARKER = re.compile(r"\[factor:([a-z_]+)\]")
REF_MARKER = re.compile(r"\[ref:([a-z_]+:[0-9A-Za-z._:-]+)\]")
PERCENT = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s?%")


@dataclass(frozen=True)
class CounterArgumentClaim:
    candidate: str
    why_not_selected: str
    reversal_condition: str
    cited_factors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplanationClaims:
    """The model's draft, as plain values (the domain does not know the contract)."""

    narrative: str
    counter_arguments: tuple[CounterArgumentClaim, ...]
    asserted_top_candidate: str
    asserted_scores: Mapping[str, float]
    cited_factors: tuple[str, ...] = ()
    cited_evidence_refs: tuple[str, ...] = ()

    def texts(self) -> list[str]:
        out = [self.narrative]
        for claim in self.counter_arguments:
            out.extend([claim.why_not_selected, claim.reversal_condition])
        return out

    def all_cited_factors(self) -> list[str]:
        cited = list(self.cited_factors)
        for claim in self.counter_arguments:
            cited.extend(claim.cited_factors)
        for text in self.texts():
            cited.extend(FACTOR_MARKER.findall(text))
        return list(dict.fromkeys(cited))

    def all_cited_refs(self) -> list[str]:
        cited = list(self.cited_evidence_refs)
        for text in self.texts():
            cited.extend(REF_MARKER.findall(text))
        return list(dict.fromkeys(cited))


@dataclass(frozen=True)
class Discrepancy:
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def check_explanation(
    claims: ExplanationClaims,
    result: ScoringResult,
    supplied_refs: Iterable[str],
    config: SdlcConfig,
    *,
    allowed_percentages: Iterable[float] = (),
) -> tuple[Discrepancy, ...]:
    """Every way the draft disagrees with the computed result or its evidence."""
    out: list[Discrepancy] = []
    tolerance = config.consistency.score_tolerance
    keys = set(config.candidate_keys)
    top = result.top
    runner_up = result.runner_up

    if claims.asserted_top_candidate != top.key:
        out.append(
            Discrepancy(
                "TOP_MISMATCH",
                f"the explanation asserts {claims.asserted_top_candidate!r} first; the computed "
                f"first is {top.key!r}",
            )
        )
    if top.key not in claims.asserted_scores:
        out.append(Discrepancy("TOP_SCORE_MISSING", f"no score asserted for {top.key!r}"))
    for key, asserted in sorted(claims.asserted_scores.items()):
        if key not in keys:
            out.append(Discrepancy("UNKNOWN_CANDIDATE", f"{key!r} is not an SDLC candidate"))
            continue
        computed = result.by_key(key)
        assert computed is not None
        if abs(float(asserted) - computed.score) > tolerance:
            out.append(
                Discrepancy(
                    "SCORE_MISMATCH",
                    f"{key}: asserted {float(asserted):g}, computed {computed.score:g} "
                    f"(tolerance {tolerance:g})",
                )
            )

    for claim in claims.counter_arguments:
        if claim.candidate not in keys:
            out.append(
                Discrepancy("UNKNOWN_CANDIDATE", f"a counter-argument names {claim.candidate!r}")
            )
        elif claim.candidate == top.key:
            out.append(
                Discrepancy(
                    "COUNTER_ARGUMENT_ON_TOP",
                    "a counter-argument explains why the computed first was not selected",
                )
            )
    if runner_up is not None:
        addressed = [c for c in claims.counter_arguments if c.candidate == runner_up.key]
        if not addressed:
            out.append(
                Discrepancy(
                    "RUNNER_UP_NOT_ADDRESSED",
                    f"no counter-argument explains why the runner-up {runner_up.key!r} was not "
                    "selected",
                )
            )
        elif not any(c.reversal_condition.strip() for c in addressed):
            out.append(
                Discrepancy(
                    "REVERSAL_MISSING",
                    "the runner-up counter-argument does not say what would reverse the ranking",
                )
            )

    factors = claims.all_cited_factors()
    if not factors:
        out.append(Discrepancy("NO_FACTOR_CITED", "the justification cites no derived factor"))
    for cited in factors:
        if factor_id(cited) is None:
            out.append(
                Discrepancy("UNKNOWN_FACTOR_CITED", f"{cited!r} is not one of the 13 factors")
            )
    supplied = set(supplied_refs)
    for cited in claims.all_cited_refs():
        if cited not in supplied:
            out.append(
                Discrepancy(
                    "UNSUPPLIED_EVIDENCE_CITED", f"{cited!r} was not in the evidence supplied"
                )
            )

    for pattern in config.consistency.forbidden_claims:
        regex = re.compile(pattern, re.IGNORECASE)
        for text in claims.texts():
            if regex.search(text):
                out.append(
                    Discrepancy(
                        "MODEL_SELECTION_CLAIM",
                        "the explanation claims the model, not the computation, made the choice",
                    )
                )
                break

    allowed = [c.score for c in result.candidates] + [c.mcda_score for c in result.candidates]
    allowed.extend(float(v) for v in allowed_percentages)
    for text in claims.texts():
        for number in PERCENT.findall(text):
            value = float(number)
            if not any(abs(value - a) <= tolerance for a in allowed):
                out.append(
                    Discrepancy(
                        "UNSUPPORTED_NUMBER",
                        f"{value:g}% matches no computed score or derivation figure",
                    )
                )

    # One entry per distinct problem, in a stable order.
    unique: dict[tuple[str, str], Discrepancy] = {}
    for item in out:
        unique.setdefault((item.code, item.detail), item)
    return tuple(unique.values())
