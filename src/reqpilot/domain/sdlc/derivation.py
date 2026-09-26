"""Approved facts -> the 13-factor profile (``FR-SDL-001``, ``FR-SDL-002``; architecture L.2, I.6).

Every factor is derived by a small, stated formula over :class:`FactorFacts`,
with the bands and parameters taken from the versioned ruleset. The four
risk-derived factors use the P7 register aggregates (I.6) exactly as P7 computed
them over the approved rows; the other nine use counts over the approved
baseline. Each derived factor carries:

* its score on the 1-5 scale;
* a rationale that states the numbers and the band that produced the score;
* the evidence references of the rows behind it - or, when the approved scope
  holds no signal for the factor, the scope itself (``absent_in_scope``), or
  nothing and a neutral 3 flagged for review (``not_recorded``). Evidence is
  never invented.

This is the *derived* score. A model may propose a bounded adjustment to it
(role #10) and a human may override it (``FR-SDL-003``); both happen outside
this module and are recorded beside the derived value, never in place of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from reqpilot.domain.sdlc.config import SdlcConfig, band_value, round_half_up
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    SCORE_MAX,
    SCORE_MID,
    SCORE_MIN,
    EvidenceState,
    FactorId,
    FactorSource,
)
from reqpilot.domain.sdlc.facts import FactorFacts, RiskAggregate, Signal

F = FactorId


@dataclass(frozen=True)
class DerivedFactor:
    factor: FactorId
    score: int
    source: FactorSource
    rationale: str
    evidence_refs: tuple[str, ...]
    evidence_state: EvidenceState
    basis: Mapping[str, float | int] = field(default_factory=dict)


def _pct(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else round(100.0 * part / whole, 2)


def _refs(*groups: Signal | RiskAggregate) -> tuple[str, ...]:
    return tuple(dict.fromkeys(r for g in groups for r in g.refs))


def _supported(
    factor: FactorId,
    score: int,
    source: FactorSource,
    rationale: str,
    refs: tuple[str, ...],
    scope_ref: str,
    basis: dict[str, float | int],
) -> DerivedFactor:
    """Rows support the score, or the scope's lack of them does - never nothing."""
    if refs:
        return DerivedFactor(factor, score, source, rationale, refs, EvidenceState.SUPPORTED, basis)
    return DerivedFactor(
        factor,
        score,
        source,
        rationale
        + " No row in the approved scope carries this signal; the scope examined is cited.",
        (scope_ref,),
        EvidenceState.ABSENT_IN_SCOPE,
        basis,
    )


def _not_recorded(factor: FactorId, reason: str) -> DerivedFactor:
    return DerivedFactor(
        factor,
        SCORE_MID,
        FactorSource.DERIVED,
        f"{reason} A neutral {SCORE_MID} is used and flagged for human review; nothing is assumed.",
        (),
        EvidenceState.NOT_RECORDED,
        {},
    )


def derive_profile(facts: FactorFacts, config: SdlcConfig) -> dict[FactorId, DerivedFactor]:
    """All thirteen derived factors, keyed by factor, in ``[PS §13]`` order."""
    d = config.derivation
    n = facts.requirements.count
    out: dict[FactorId, DerivedFactor] = {}
    scope = facts.scope_ref

    # 1. requirement stability - churn in the approved set.
    if n == 0:
        out[F.REQUIREMENT_STABILITY] = _not_recorded(
            F.REQUIREMENT_STABILITY, "The approved scope holds no requirement version."
        )
    else:
        churn_items = (
            facts.revised_requirements.count + facts.conflicts.count + facts.change_signals.count
        )
        churn = _pct(churn_items, n)
        score = band_value(d.band("stability_churn"), churn)
        out[F.REQUIREMENT_STABILITY] = _supported(
            F.REQUIREMENT_STABILITY,
            score,
            FactorSource.DERIVED,
            f"{facts.revised_requirements.count} revised requirement(s), "
            f"{facts.conflicts.count} conflict(s) and {facts.change_signals.count} "
            f"change-signalling statement(s) over {n} in-force requirement(s): churn "
            f"{churn:g}% -> {score}.",
            _refs(facts.revised_requirements, facts.conflicts, facts.change_signals),
            scope,
            {"churn_pct": churn, "requirements": n},
        )

    # 2. regulatory criticality - I.6 blend: the strongest of the compliance-risk
    # aggregate, the normative sources mapped and the open compliance gaps.
    r = facts.regulatory_risk.value
    s = band_value(d.band("normative_sources"), facts.normative_sources.count)
    g = band_value(d.band("open_compliance_gaps"), facts.open_compliance_gaps.count)
    score = max(r, s, g)
    out[F.REGULATORY_CRITICALITY] = _supported(
        F.REGULATORY_CRITICALITY,
        score,
        FactorSource.RISK_AGGREGATE,
        f"max(compliance-risk aggregate {r}, {facts.normative_sources.count} normative "
        f"source(s) mapped -> {s}, {facts.open_compliance_gaps.count} open compliance "
        f"gap(s) -> {g}) = {score}.",
        _refs(facts.regulatory_risk, facts.normative_sources, facts.open_compliance_gaps),
        scope,
        {"risk_component": r, "sources_component": s, "gaps_component": g},
    )

    # 3. security risk - I.6 exactly (the P7 formula over the approved register).
    sec = facts.security_risk
    out[F.SECURITY_RISK] = _supported(
        F.SECURITY_RISK,
        sec.value,
        FactorSource.RISK_AGGREGATE,
        f"I.6 security-risk aggregate over the approved register ({_counts(sec)}) = {sec.value}.",
        sec.refs,
        scope,
        {"aggregate": sec.value},
    )

    # 4. project complexity - I.6: technical risks blended with size and integration.
    size = band_value(d.band("system_size"), n)
    integration = band_value(
        d.band("integration_requirements"), facts.integration_requirements.count
    )
    tech = facts.technical_risk.value
    score = min(SCORE_MAX, max(SCORE_MIN, round_half_up((tech + size + integration) / 3)))
    out[F.PROJECT_COMPLEXITY] = DerivedFactor(
        F.PROJECT_COMPLEXITY,
        score,
        FactorSource.RISK_AGGREGATE,
        f"round((technical-risk aggregate {tech} + size band {size} for {n} requirement(s) + "
        f"integration band {integration} for {facts.integration_requirements.count} "
        f"integration requirement(s)) / 3) = {score}.",
        (*_refs(facts.technical_risk, facts.integration_requirements), scope),
        EvidenceState.SUPPORTED,
        {"technical_component": tech, "size_component": size, "integration_component": integration},
    )

    # 5. system size.
    out[F.SYSTEM_SIZE] = (
        DerivedFactor(
            F.SYSTEM_SIZE,
            size,
            FactorSource.DERIVED,
            f"{n} in-force requirement(s) in the approved baseline -> {size}.",
            (scope, *facts.requirements.refs),
            EvidenceState.SUPPORTED,
            {"requirements": n},
        )
        if n
        else _not_recorded(F.SYSTEM_SIZE, "The approved scope holds no requirement version.")
    )

    # 6. legacy-system dependence.
    legacy = facts.legacy_requirements
    score = band_value(d.band("legacy_requirements"), legacy.count)
    out[F.LEGACY_SYSTEM_DEPENDENCE] = _supported(
        F.LEGACY_SYSTEM_DEPENDENCE,
        score,
        FactorSource.DERIVED,
        f"{legacy.count} requirement(s) depend on a legacy or existing system -> {score}.",
        legacy.refs,
        scope,
        {"legacy_requirements": legacy.count},
    )

    # 7. expected frequency of change.
    if n == 0:
        out[F.EXPECTED_FREQUENCY_OF_CHANGE] = _not_recorded(
            F.EXPECTED_FREQUENCY_OF_CHANGE, "The approved scope holds no requirement version."
        )
    else:
        ratio = _pct(facts.change_signals.count + facts.revised_requirements.count, n)
        score = band_value(d.band("change_ratio"), ratio)
        out[F.EXPECTED_FREQUENCY_OF_CHANGE] = _supported(
            F.EXPECTED_FREQUENCY_OF_CHANGE,
            score,
            FactorSource.DERIVED,
            f"{facts.change_signals.count} change-signalling statement(s) and "
            f"{facts.revised_requirements.count} revised requirement(s) over {n}: "
            f"{ratio:g}% -> {score}.",
            _refs(facts.change_signals, facts.revised_requirements),
            scope,
            {"change_pct": ratio},
        )

    # 8. need for continuous delivery.
    delivery = facts.delivery_signals
    score = band_value(d.band("delivery_signals"), delivery.count)
    out[F.NEED_FOR_CONTINUOUS_DELIVERY] = _supported(
        F.NEED_FOR_CONTINUOUS_DELIVERY,
        score,
        FactorSource.DERIVED,
        f"{delivery.count} requirement(s) ask for frequent or continuous release -> {score}.",
        delivery.refs,
        scope,
        {"delivery_signals": delivery.count},
    )

    # 9. stakeholder availability.
    people = facts.stakeholders.count
    if people == 0:
        out[F.STAKEHOLDER_AVAILABILITY] = _not_recorded(
            F.STAKEHOLDER_AVAILABILITY, "The project records no stakeholder."
        )
    else:
        ratio = _pct(facts.stakeholders_interviewed.count, people)
        base = band_value(d.band("interviewed_ratio"), ratio)
        penalty_at = int(d.param("open_clarification_penalty_at"))
        open_questions = facts.open_clarifications.count
        penalty = 1 if penalty_at > 0 and open_questions >= penalty_at else 0
        score = max(SCORE_MIN, base - penalty)
        out[F.STAKEHOLDER_AVAILABILITY] = DerivedFactor(
            F.STAKEHOLDER_AVAILABILITY,
            score,
            FactorSource.DERIVED,
            f"{facts.stakeholders_interviewed.count} of {people} stakeholder(s) completed an "
            f"interview ({ratio:g}%) -> {base}"
            + (
                f"; {open_questions} clarification(s) still unanswered (>= {penalty_at}) -> -1"
                if penalty
                else ""
            )
            + f" = {score}.",
            _refs(facts.stakeholders, facts.stakeholders_interviewed, facts.open_clarifications),
            EvidenceState.SUPPORTED,
            {"interviewed_pct": ratio, "open_clarifications": open_questions, "penalty": penalty},
        )

    # 10. testing and documentation requirements.
    if n == 0:
        out[F.TESTING_AND_DOCUMENTATION_REQUIREMENTS] = _not_recorded(
            F.TESTING_AND_DOCUMENTATION_REQUIREMENTS,
            "The approved scope holds no requirement version.",
        )
    else:
        ratio = _pct(facts.acceptance_criteria.count, n)
        base = band_value(d.band("acceptance_criteria_ratio"), ratio)
        bonus_at = int(d.param("documentation_bonus_at"))
        docs = facts.documentation_signals.count
        bonus = 1 if bonus_at > 0 and docs >= bonus_at else 0
        score = min(SCORE_MAX, base + bonus)
        out[F.TESTING_AND_DOCUMENTATION_REQUIREMENTS] = _supported(
            F.TESTING_AND_DOCUMENTATION_REQUIREMENTS,
            score,
            FactorSource.DERIVED,
            f"{facts.acceptance_criteria.count} of {n} requirement(s) carry acceptance criteria "
            f"({ratio:g}%) -> {base}"
            + (
                f"; {docs} audit/documentation requirement(s) (>= {bonus_at}) -> +1"
                if bonus
                else ""
            )
            + f" = {score}.",
            _refs(facts.acceptance_criteria, facts.documentation_signals),
            scope,
            {"criteria_pct": ratio, "documentation_signals": docs, "bonus": bonus},
        )

    # 11. budget and schedule constraints - only what the requirements state.
    schedule = facts.schedule_signals
    if schedule.count == 0:
        out[F.BUDGET_AND_SCHEDULE_CONSTRAINTS] = _not_recorded(
            F.BUDGET_AND_SCHEDULE_CONSTRAINTS,
            "No approved requirement states a budget or schedule constraint.",
        )
    else:
        score = band_value(d.band("schedule_signals"), schedule.count)
        out[F.BUDGET_AND_SCHEDULE_CONSTRAINTS] = DerivedFactor(
            F.BUDGET_AND_SCHEDULE_CONSTRAINTS,
            score,
            FactorSource.DERIVED,
            f"{schedule.count} requirement(s) state a budget or schedule constraint -> {score}.",
            schedule.refs,
            EvidenceState.SUPPORTED,
            {"schedule_signals": schedule.count},
        )

    # 12. need for formal verification.
    verification = facts.verification_signals
    score = band_value(d.band("verification_signals"), verification.count)
    out[F.NEED_FOR_FORMAL_VERIFICATION] = _supported(
        F.NEED_FOR_FORMAL_VERIFICATION,
        score,
        FactorSource.DERIVED,
        f"{verification.count} requirement(s) demand exact verification, reconciliation or "
        f"independent control -> {score}.",
        verification.refs,
        scope,
        {"verification_signals": verification.count},
    )

    # 13. consequences of system failure - I.6 exactly.
    consequence = facts.consequences_of_failure
    out[F.CONSEQUENCES_OF_FAILURE] = _supported(
        F.CONSEQUENCES_OF_FAILURE,
        consequence.value,
        FactorSource.RISK_AGGREGATE,
        f"I.6: the highest impact among the approved security, compliance and operational "
        f"risks ({_counts(consequence)}) = {consequence.value}.",
        consequence.refs,
        scope,
        {"aggregate": consequence.value},
    )

    return {f: out[f] for f in FACTOR_ORDER}


def _counts(aggregate: RiskAggregate) -> str:
    if not aggregate.counts:
        return "no risk"
    return ", ".join(f"{k} {v}" for k, v in sorted(aggregate.counts.items())) or "no risk"
