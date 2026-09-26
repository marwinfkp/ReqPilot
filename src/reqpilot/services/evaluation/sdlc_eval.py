"""The P9 E9 evaluation (P9-SDLC-SYNTHETIC-v1).

**E9 measures agreement between ReqPilot and a synthetic AI-generated
expert-panel simulation. It is an engineering benchmark, not independent
human-expert validation.** Approved Phase 0 O.1 defines E9 as "top-ranked model
vs a blind expert panel (>= 3 experts judging before seeing system output)". No
human panel is available to this project; the benchmark therefore holds the
independent judgements of five AI-generated expert personas, each produced in a
fresh context that saw only the case, the persona, the instructions and the
candidate list - never ReqPilot's rules, scores, ranking or explanation, and
never another panellist's answer. It says nothing about real-world SDLC
recommendation accuracy.

**No target exists and none is invented** (O.1: targets for E2-E9 are set from
measured behaviour). The result is a first measurement.

What this module does, deterministically:

1. **Verify** the frozen benchmark against its manifest (every file's canonical
   sha256, R.3/D16) before anything is read.
2. **Validate** each panel response: known case, every candidate key exactly
   once, ``preferred`` equal to the ranking's first entry, confidence 1-5.
3. **Aggregate** the panel without asking any model "which wins": Borda count
   over the full rankings, then first-place votes, then the fixed candidate
   order; plus the plurality preference, consensus strength and Kendall's W.
4. **Compute ReqPilot's ranking** for each case with the product's own pure
   pipeline - the P7 matrix and I.6 aggregates over the case's risks, the P9
   derivation over the case's facts, the MCDA and the rule pass - with no model
   call (the deterministic path; the model's bounded factor proposals and the
   explanation are not part of E9).
5. **Compare**: top-1 agreement (the E9 figure), top-1 within the panel's top
   two, agreement with the plurality, rank correlations, pairwise order
   agreement, and every disagreement, case by case.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from reqpilot.domain.enums import RiskCategory, RiskImpact, RiskLikelihood
from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.sdlc.derivation import derive_profile
from reqpilot.domain.sdlc.facts import FactorFacts, RiskAggregate, Signal
from reqpilot.domain.sdlc.scoring import ScoringResult, score_candidates
from reqpilot.rules.risk import RiskRules
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.services.risk.register import compute_factor_inputs

MANIFEST = "manifest.json"
REQUIRED_FILES = (
    "BENCHMARK.md",
    "REVIEW_SHEET.md",
    "cases.jsonl",
    "panel_personas.json",
    "panel_responses.jsonl",
    "panel_aggregate.json",
    "generation_prompts.md",
)

NO_TARGET = (
    "No approved numeric target exists for E9 (Phase 0 O.1 says targets for E2-E9 are set "
    "from measured behaviour, not guessed). This is a first measurement against a synthetic "
    "AI-generated panel - not a threshold that was met, and not human-expert validation."
)

#: The required framing of every E9 result (P9 brief; stated verbatim wherever E9 is reported).
E9_STATEMENT = (
    "E9 measures agreement between ReqPilot and a synthetic AI-generated expert-panel "
    "simulation. It is an engineering benchmark, not independent human-expert validation."
)

VALIDATION_STATUS = (
    "synthetic; the panel is AI-generated personas, not real experts; not independently "
    "validated; not expert-validated; an engineering benchmark only"
)

#: Signal fields a case's facts carry, in the order FactorFacts declares them.
COUNT_FIELDS = (
    "requirements",
    "revised_requirements",
    "conflicts",
    "integration_requirements",
    "legacy_requirements",
    "change_signals",
    "delivery_signals",
    "verification_signals",
    "documentation_signals",
    "schedule_signals",
    "acceptance_criteria",
    "stakeholders",
    "stakeholders_interviewed",
    "open_clarifications",
    "normative_sources",
    "open_compliance_gaps",
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise GoldSetIntegrityError(f"{path.name} line {number}: {exc}") from exc
    return rows


@dataclass(frozen=True)
class CaseRisk:
    id: str
    category: RiskCategory
    likelihood: RiskLikelihood
    impact: RiskImpact
    title: str


@dataclass(frozen=True)
class SdlcCase:
    case_id: str
    title: str
    domain: str
    counts: Mapping[str, int]
    risks: tuple[CaseRisk, ...]


@dataclass(frozen=True)
class PanelResponse:
    persona_id: str
    case_id: str
    ranking: tuple[str, ...]
    preferred: str
    confidence: int
    key_factors: tuple[str, ...]


@dataclass(frozen=True)
class SdlcBenchmark:
    benchmark_id: str
    manifest_sha256: str
    manifest: Mapping[str, Any]
    candidates: tuple[str, ...]
    personas: tuple[str, ...]
    cases: tuple[SdlcCase, ...]
    responses: tuple[PanelResponse, ...]
    frozen_aggregate: Mapping[str, Any]


def _verify(directory: Path) -> dict[str, Any]:
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
    unlisted = sorted(present - set(files))
    if unlisted:
        raise GoldSetIntegrityError(
            f"{directory} contains unlisted file(s): {unlisted}; a frozen benchmark is exactly "
            "what its manifest names"
        )
    missing = [name for name in REQUIRED_FILES if name not in files]
    if missing:
        raise GoldSetIntegrityError(f"the manifest does not name {missing}")
    for name, expected in sorted(files.items()):
        path = directory / name
        if not path.is_file():
            raise GoldSetIntegrityError(f"{name} is named by the manifest but missing")
        actual = file_canonical_sha256(path)
        if actual != expected:
            raise GoldSetIntegrityError(
                f"{name} does not match its frozen hash ({actual} != {expected}); a benchmark "
                "is never edited - corrections become a new version"
            )
    return dict(manifest)


def parse_case(row: Mapping[str, Any]) -> SdlcCase:
    try:
        facts = row["facts"]
        counts = {name: int(facts[name]) for name in COUNT_FIELDS}
        risks = tuple(
            CaseRisk(
                id=f"{row['case_id']}-R{index:02d}",
                category=RiskCategory(str(r["category"])),
                likelihood=RiskLikelihood(str(r["likelihood"])),
                impact=RiskImpact(str(r["impact"])),
                title=str(r.get("title", "")),
            )
            for index, r in enumerate(facts.get("risks") or (), start=1)
        )
        case = SdlcCase(
            case_id=str(row["case_id"]),
            title=str(row["title"]),
            domain=str(row["domain"]),
            counts=counts,
            risks=risks,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GoldSetIntegrityError(f"case {row.get('case_id')!r} is malformed: {exc}") from exc
    n = counts["requirements"]
    for name in COUNT_FIELDS:
        if counts[name] < 0:
            raise GoldSetIntegrityError(f"case {case.case_id}: {name} is negative")
    for name in (
        "revised_requirements",
        "integration_requirements",
        "legacy_requirements",
        "change_signals",
        "delivery_signals",
        "verification_signals",
        "documentation_signals",
        "schedule_signals",
        "acceptance_criteria",
    ):
        if counts[name] > n:
            raise GoldSetIntegrityError(f"case {case.case_id}: {name} exceeds requirements")
    if counts["stakeholders_interviewed"] > counts["stakeholders"]:
        raise GoldSetIntegrityError(f"case {case.case_id}: more interviewed than stakeholders")
    return case


def parse_response(
    row: Mapping[str, Any], candidates: Sequence[str], case_ids: set[str]
) -> PanelResponse:
    """Validate one panellist's judgement of one case. Nothing is repaired silently."""
    try:
        response = PanelResponse(
            persona_id=str(row["persona_id"]),
            case_id=str(row["case_id"]),
            ranking=tuple(str(c) for c in row["ranking"]),
            preferred=str(row["preferred"]),
            confidence=int(row["confidence"]),
            key_factors=tuple(str(f) for f in row.get("key_factors") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GoldSetIntegrityError(f"a panel response is malformed: {exc}") from exc
    where = f"{response.persona_id}/{response.case_id}"
    if response.case_id not in case_ids:
        raise GoldSetIntegrityError(f"{where}: unknown case")
    if sorted(response.ranking) != sorted(candidates) or len(set(response.ranking)) != len(
        candidates
    ):
        raise GoldSetIntegrityError(f"{where}: the ranking is not a permutation of the candidates")
    if response.preferred != response.ranking[0]:
        raise GoldSetIntegrityError(f"{where}: 'preferred' is not the ranking's first entry")
    if not 1 <= response.confidence <= 5:
        raise GoldSetIntegrityError(f"{where}: confidence outside 1..5")
    return response


def load_sdlc_benchmark(directory: Path) -> SdlcBenchmark:
    """Load P9-SDLC-SYNTHETIC-v1, verifying every file against its manifest first."""
    manifest = _verify(directory)
    candidates = tuple(str(c) for c in manifest["candidates"])
    cases = tuple(parse_case(row) for row in _jsonl(directory / "cases.jsonl"))
    case_ids = {c.case_id for c in cases}
    if len(case_ids) != len(cases):
        raise GoldSetIntegrityError("duplicate case ids")
    personas_raw = json.loads((directory / "panel_personas.json").read_text(encoding="utf-8"))
    personas = tuple(str(p["persona_id"]) for p in personas_raw["personas"])
    responses = tuple(
        parse_response(row, candidates, case_ids)
        for row in _jsonl(directory / "panel_responses.jsonl")
    )
    seen = Counter((r.persona_id, r.case_id) for r in responses)
    expected = {(p, c) for p in personas for c in case_ids}
    if set(seen) != expected or any(v != 1 for v in seen.values()):
        raise GoldSetIntegrityError("every persona must judge every case exactly once")
    aggregate = json.loads((directory / "panel_aggregate.json").read_text(encoding="utf-8"))
    return SdlcBenchmark(
        benchmark_id=str(manifest["benchmark_id"]),
        manifest_sha256=file_canonical_sha256(directory / MANIFEST),
        manifest=manifest,
        candidates=candidates,
        personas=personas,
        cases=cases,
        responses=responses,
        frozen_aggregate=aggregate,
    )


# ---------------------------------------------------------------------------
# the panel, aggregated deterministically
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PanelAggregate:
    case_id: str
    borda: Mapping[str, int]
    first_place_votes: Mapping[str, int]
    ranking: tuple[str, ...]
    plurality: tuple[str, ...]
    consensus_strength: float
    kendall_w: float
    mean_confidence: float

    @property
    def top(self) -> str:
        return self.ranking[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "borda": dict(self.borda),
            "first_place_votes": dict(self.first_place_votes),
            "ranking": list(self.ranking),
            "plurality": list(self.plurality),
            "consensus_strength": self.consensus_strength,
            "kendall_w": self.kendall_w,
            "mean_confidence": self.mean_confidence,
        }


def aggregate_panel(
    case_id: str, responses: Sequence[PanelResponse], candidates: Sequence[str]
) -> PanelAggregate:
    """Borda over full rankings; ties by first-place votes, then the fixed candidate order."""
    size = len(candidates)
    borda = dict.fromkeys(candidates, 0)
    firsts = dict.fromkeys(candidates, 0)
    ranks: dict[str, list[int]] = {c: [] for c in candidates}
    for response in responses:
        for position, candidate in enumerate(response.ranking):
            borda[candidate] += size - 1 - position
            ranks[candidate].append(position + 1)
        firsts[response.ranking[0]] += 1
    order = {c: i for i, c in enumerate(candidates)}
    ranking = tuple(sorted(candidates, key=lambda c: (-borda[c], -firsts[c], order[c])))
    top_votes = max(firsts.values())
    plurality = tuple(c for c in candidates if firsts[c] == top_votes)
    m = len(responses)
    consensus = round(firsts[ranking[0]] / m, 4) if m else 0.0
    # Kendall's coefficient of concordance W (no ties within a ranking).
    if m > 1 and size > 1:
        totals = [sum(ranks[c]) for c in candidates]
        mean = sum(totals) / size
        s = sum((t - mean) ** 2 for t in totals)
        w = round(12 * s / (m**2 * (size**3 - size)), 4)
    else:
        w = 1.0
    confidence = round(sum(r.confidence for r in responses) / m, 3) if m else 0.0
    return PanelAggregate(case_id, borda, firsts, ranking, plurality, consensus, w, confidence)


def aggregate_all(benchmark: SdlcBenchmark) -> dict[str, PanelAggregate]:
    return {
        case.case_id: aggregate_panel(
            case.case_id,
            [r for r in benchmark.responses if r.case_id == case.case_id],
            benchmark.candidates,
        )
        for case in benchmark.cases
    }


# ---------------------------------------------------------------------------
# ReqPilot on a case - the product's own pure pipeline, no model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ViewRisk:
    id: str
    category: RiskCategory
    severity: Any
    impact: str


def case_facts(case: SdlcCase, risk_rules: RiskRules) -> FactorFacts:
    """A case's facts as the product's :class:`FactorFacts`, with synthetic refs.

    Severities come from the approved P7 matrix and the four risk aggregates from
    the P7 I.6 formulas (:func:`compute_factor_inputs`) - the same code the
    product runs over persisted rows.
    """
    risks = [
        _ViewRisk(
            r.id, r.category, risk_rules.matrix.severity(r.likelihood, r.impact), str(r.impact)
        )
        for r in case.risks
    ]
    inputs = {i.key: i for i in compute_factor_inputs(risk_rules, risks)}

    def aggregate(key: str) -> RiskAggregate:
        item = inputs[key]
        return RiskAggregate(
            value=item.value,
            refs=tuple(f"risk:{rid}" for rid in item.evidence_risk_ids),
            counts=dict(item.counts),
            description=item.description,
        )

    def signal(name: str) -> Signal:
        count = case.counts[name]
        return Signal(count, tuple(f"case:{case.case_id}:{name}:{i}" for i in range(1, count + 1)))

    return FactorFacts(
        scope_ref=f"case:{case.case_id}",
        scope_label=case.title,
        requirements=signal("requirements"),
        revised_requirements=signal("revised_requirements"),
        conflicts=signal("conflicts"),
        integration_requirements=signal("integration_requirements"),
        legacy_requirements=signal("legacy_requirements"),
        change_signals=signal("change_signals"),
        delivery_signals=signal("delivery_signals"),
        verification_signals=signal("verification_signals"),
        documentation_signals=signal("documentation_signals"),
        schedule_signals=signal("schedule_signals"),
        acceptance_criteria=signal("acceptance_criteria"),
        stakeholders=signal("stakeholders"),
        stakeholders_interviewed=signal("stakeholders_interviewed"),
        open_clarifications=signal("open_clarifications"),
        normative_sources=signal("normative_sources"),
        open_compliance_gaps=signal("open_compliance_gaps"),
        security_risk=aggregate("security_risk"),
        consequences_of_failure=aggregate("consequences_of_failure"),
        regulatory_risk=aggregate("regulatory_criticality"),
        technical_risk=aggregate("project_complexity"),
    )


def reqpilot_result(case: SdlcCase, risk_rules: RiskRules, sdlc_rules: SdlcRules) -> ScoringResult:
    profile = derive_profile(case_facts(case, risk_rules), sdlc_rules.config)
    return score_candidates({f: d.score for f, d in profile.items()}, sdlc_rules.config)


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def _rank_positions(ranking: Sequence[str]) -> dict[str, int]:
    return {c: i + 1 for i, c in enumerate(ranking)}


def kendall_tau(a: Sequence[str], b: Sequence[str]) -> float:
    """Kendall's tau between two full rankings of the same items (no ties)."""
    pa, pb = _rank_positions(a), _rank_positions(b)
    concordant = discordant = 0
    for x, y in combinations(list(a), 2):
        s = (pa[x] - pa[y]) * (pb[x] - pb[y])
        if s > 0:
            concordant += 1
        elif s < 0:
            discordant += 1
    total = concordant + discordant
    return round((concordant - discordant) / total, 4) if total else 1.0


def spearman_rho(a: Sequence[str], b: Sequence[str]) -> float:
    pa, pb = _rank_positions(a), _rank_positions(b)
    n = len(a)
    d2 = sum((pa[c] - pb[c]) ** 2 for c in a)
    return round(1 - 6 * d2 / (n * (n * n - 1)), 4) if n > 1 else 1.0


def pairwise_agreement(a: Sequence[str], b: Sequence[str]) -> float:
    pa, pb = _rank_positions(a), _rank_positions(b)
    pairs = list(combinations(list(a), 2))
    same = sum(1 for x, y in pairs if (pa[x] < pa[y]) == (pb[x] < pb[y]))
    return round(same / len(pairs), 4) if pairs else 1.0


@dataclass(frozen=True)
class CaseComparison:
    case_id: str
    reqpilot_ranking: tuple[str, ...]
    reqpilot_scores: Mapping[str, float]
    rules_triggered: tuple[str, ...]
    panel: PanelAggregate
    panellists_preferring_reqpilot_top: int
    kendall_tau: float
    spearman_rho: float
    pairwise: float
    profile: Mapping[str, int] = field(default_factory=dict)

    @property
    def top1_agrees(self) -> bool:
        return self.reqpilot_ranking[0] == self.panel.top

    @property
    def top1_in_panel_top2(self) -> bool:
        return self.reqpilot_ranking[0] in self.panel.ranking[:2]

    @property
    def panel_top_in_reqpilot_top2(self) -> bool:
        return self.panel.top in self.reqpilot_ranking[:2]

    @property
    def agrees_with_plurality(self) -> bool:
        return self.reqpilot_ranking[0] in self.panel.plurality

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "reqpilot_top": self.reqpilot_ranking[0],
            "panel_top": self.panel.top,
            "top1_agrees": self.top1_agrees,
            "top1_in_panel_top2": self.top1_in_panel_top2,
            "panel_top_in_reqpilot_top2": self.panel_top_in_reqpilot_top2,
            "agrees_with_plurality": self.agrees_with_plurality,
            "panellists_preferring_reqpilot_top": self.panellists_preferring_reqpilot_top,
            "kendall_tau": self.kendall_tau,
            "spearman_rho": self.spearman_rho,
            "pairwise_agreement": self.pairwise,
            "reqpilot_ranking": list(self.reqpilot_ranking),
            "reqpilot_scores": dict(self.reqpilot_scores),
            "rules_triggered": list(self.rules_triggered),
            "reqpilot_profile": dict(self.profile),
            "panel": self.panel.as_dict(),
        }


def compare(
    benchmark: SdlcBenchmark, risk_rules: RiskRules, sdlc_rules: SdlcRules
) -> list[CaseComparison]:
    aggregates = aggregate_all(benchmark)
    out = []
    for case in benchmark.cases:
        result = reqpilot_result(case, risk_rules, sdlc_rules)
        panel = aggregates[case.case_id]
        ranking = result.ranking
        preferring = sum(
            1
            for r in benchmark.responses
            if r.case_id == case.case_id and r.preferred == ranking[0]
        )
        out.append(
            CaseComparison(
                case_id=case.case_id,
                reqpilot_ranking=ranking,
                reqpilot_scores={c.key: c.score for c in result.candidates},
                rules_triggered=tuple(dict.fromkeys(a.rule_id for a in result.rules)),
                panel=panel,
                panellists_preferring_reqpilot_top=preferring,
                kendall_tau=kendall_tau(ranking, panel.ranking),
                spearman_rho=spearman_rho(ranking, panel.ranking),
                pairwise=pairwise_agreement(ranking, panel.ranking),
                profile={str(f): s for f, s in result.profile.items()},
            )
        )
    return out


def summarise(comparisons: Sequence[CaseComparison]) -> dict[str, Any]:
    n = len(comparisons)

    def share(flag: str) -> dict[str, Any]:
        k = sum(1 for c in comparisons if getattr(c, flag))
        return {"count": k, "of": n, "rate": round(k / n, 4) if n else None}

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "cases": n,
        "e9_top1_agreement": share("top1_agrees"),
        "top1_in_panel_top2": share("top1_in_panel_top2"),
        "panel_top_in_reqpilot_top2": share("panel_top_in_reqpilot_top2"),
        "agreement_with_panel_plurality": share("agrees_with_plurality"),
        "top1_supported_by_at_least_one_panellist": {
            "count": sum(1 for c in comparisons if c.panellists_preferring_reqpilot_top >= 1),
            "of": n,
        },
        "mean_kendall_tau": mean([c.kendall_tau for c in comparisons]),
        "mean_spearman_rho": mean([c.spearman_rho for c in comparisons]),
        "mean_pairwise_agreement": mean([c.pairwise for c in comparisons]),
        "mean_panel_consensus_strength": mean([c.panel.consensus_strength for c in comparisons]),
        "mean_panel_kendall_w": mean([c.panel.kendall_w for c in comparisons]),
        "disagreement_cases": [c.case_id for c in comparisons if not c.top1_agrees],
        "top1_distribution": {
            "reqpilot": dict(Counter(c.reqpilot_ranking[0] for c in comparisons)),
            "panel": dict(Counter(c.panel.top for c in comparisons)),
        },
    }


def evaluation_report(
    benchmark: SdlcBenchmark,
    comparisons: Sequence[CaseComparison],
    sdlc_rules: SdlcRules,
) -> dict[str, Any]:
    frozen_ruleset = str(benchmark.manifest.get("system_under_test", {}).get("sdlc_rules_sha256"))
    return {
        "benchmark_id": benchmark.benchmark_id,
        "manifest_sha256": benchmark.manifest_sha256,
        "evaluated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "system_under_test": {
            "ruleset_ref": sdlc_rules.ruleset_ref,
            "sdlc_rules_sha256": sdlc_rules.content_sha256,
            "matches_ruleset_frozen_with_the_benchmark": sdlc_rules.content_sha256
            == frozen_ruleset,
            "path": "deterministic: P7 matrix + I.6 aggregates, P9 derivation, MCDA, rule pass; "
            "no model call",
        },
        "statement": E9_STATEMENT,
        "target": None,
        "target_note": NO_TARGET,
        "validation_status": VALIDATION_STATUS,
        "summary": summarise(comparisons),
        "cases": [c.as_dict() for c in comparisons],
    }


def panel_aggregate_document(benchmark_like: Any, aggregates: Mapping[str, PanelAggregate]) -> dict:
    """The frozen ``panel_aggregate.json`` content (computed before any comparison)."""
    return {
        "method": (
            "Borda count over each panellist's full ranking (6 points for first ... 0 for "
            "seventh); ties broken by first-place votes, then the fixed candidate order. "
            "Plurality = the candidate(s) with most first-place votes. Consensus strength = "
            "share of panellists whose first choice is the Borda winner. Kendall's W = "
            "concordance of the five rankings."
        ),
        "cases": [aggregates[k].as_dict() for k in sorted(aggregates)],
    }


def check_frozen_aggregate(benchmark: SdlcBenchmark) -> None:
    """The frozen aggregate must equal a fresh deterministic aggregation of the responses."""
    fresh = panel_aggregate_document(benchmark, aggregate_all(benchmark))
    if fresh["cases"] != benchmark.frozen_aggregate.get("cases"):
        raise GoldSetIntegrityError(
            "panel_aggregate.json does not equal the deterministic aggregation of the frozen "
            "responses"
        )
