"""P9 scoring, by hand (roadmap P9 exit: "scoring unit tests pass on hand-computed cases").

Every expected number below is worked out in the comment beside it, from the
formula of architecture L.3::

    raw(c)  = sum_f w[f] * S[c][f] * norm(score[f])      norm(s) = (s - 3) / 2
    max(c)  = sum_f w[f] * |S[c][f]|
    mcda(c) = 50 * (1 + raw(c) / max(c))                  (50 when max(c) = 0)

and the rule pass of L.4 (boost -> sort -> veto -> require_top_n).
"""

from __future__ import annotations

import pytest

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.sdlc.config import (
    Band,
    CandidateDefinition,
    Condition,
    ConsistencyConfig,
    DerivationConfig,
    FactorDefinition,
    RuleEffect,
    SdlcConfig,
    SdlcRule,
    band_value,
    round_half_up,
)
from reqpilot.domain.sdlc.factors import FACTOR_ORDER, FactorId, norm
from reqpilot.domain.sdlc.scoring import reversal_conditions, score_candidates
from reqpilot.rules.sdlc import packaged_sdlc_rules

pytestmark = pytest.mark.unit

F = FactorId
ZERO = dict.fromkeys(FACTOR_ORDER, 0)
BANDS = {
    name: (Band(0, 1),)
    for name in (
        "stability_churn",
        "normative_sources",
        "open_compliance_gaps",
        "system_size",
        "integration_requirements",
        "legacy_requirements",
        "change_ratio",
        "delivery_signals",
        "interviewed_ratio",
        "acceptance_criteria_ratio",
        "schedule_signals",
        "verification_signals",
    )
}


def config(rules: tuple[SdlcRule, ...] = (), weights: dict[FactorId, float] | None = None):  # type: ignore[no-untyped-def]
    weights = weights or {}
    return SdlcConfig(
        name="hand",
        version="0.0.1",
        ruleset_ref="hand@0.0.1",
        weights_version="0.0.1",
        weights_ref="hand.weights@0.0.1",
        factors={
            f: FactorDefinition(f, str(f), weights.get(f, 1.0), {1: "low", 3: "mid", 5: "high"})
            for f in FACTOR_ORDER
        },
        candidates=(
            # A likes stable requirements.
            CandidateDefinition("a", "A", frozenset({"a"}), {**ZERO, F.REQUIREMENT_STABILITY: 2}),
            # B likes change and security risk.
            CandidateDefinition(
                "b",
                "B",
                frozenset({"b"}),
                {**ZERO, F.EXPECTED_FREQUENCY_OF_CHANGE: 2, F.SECURITY_RISK: 1},
            ),
            # C is indifferent to everything.
            CandidateDefinition("c", "C", frozenset({"c"}), dict(ZERO)),
        ),
        rules=rules,
        derivation=DerivationConfig(
            bands=BANDS, params={"open_clarification_penalty_at": 3, "documentation_bonus_at": 2}
        ),
        consistency=ConsistencyConfig(1.0, 1, ()),
        max_proposal_deviation=1,
    )


def profile(**overrides: int) -> dict[FactorId, int]:
    out = dict.fromkeys(FACTOR_ORDER, 3)
    out.update({F(k): v for k, v in overrides.items()})
    return out


WEIGHTS = {F.SECURITY_RISK: 2.0}
#: stability 5 (norm +1), change 1 (norm -1), security 4 (norm +0.5).
P = profile(requirement_stability=5, expected_frequency_of_change=1, security_risk=4)


def test_norm_maps_the_scale_onto_minus_one_to_one() -> None:
    assert [norm(s) for s in (1, 2, 3, 4, 5)] == [-1.0, -0.5, 0.0, 0.5, 1.0]
    for bad in (0, 6, 2.5, True, "3"):
        with pytest.raises(ValueError):
            norm(bad)  # type: ignore[arg-type]


def test_mcda_by_hand() -> None:
    result = score_candidates(P, config(weights=WEIGHTS))
    a, b, c = (result.by_key(k) for k in "abc")
    assert a and b and c
    # A: raw = 1.0 * 2 * (+1) = 2;  max = 1.0 * 2 = 2;  50 * (1 + 2/2) = 100
    assert (a.raw, a.max_raw, a.mcda_score) == (2.0, 2.0, 100.0)
    # B: raw = 1.0 * 2 * (-1) + 2.0 * 1 * (+0.5) = -2 + 1 = -1
    #    max = 1.0 * 2 + 2.0 * 1 = 4;  50 * (1 - 1/4) = 37.5
    assert (b.raw, b.max_raw, b.mcda_score) == (-1.0, 4.0, 37.5)
    # C: max = 0 -> neutral 50
    assert (c.raw, c.max_raw, c.mcda_score) == (0.0, 0.0, 50.0)
    assert result.ranking == ("a", "c", "b")
    assert [x.rank for x in result.candidates] == [1, 2, 3]
    assert b.contributions[str(F.EXPECTED_FREQUENCY_OF_CHANGE)] == -2.0
    assert b.contributions[str(F.SECURITY_RISK)] == 1.0
    assert result.rules == ()


def test_ties_are_broken_by_the_candidate_file_order() -> None:
    # A neutral profile gives every candidate 50: the versioned order decides.
    result = score_candidates(profile(), config())
    assert [x.score for x in result.candidates] == [50.0, 50.0, 50.0]
    assert result.ranking == ("a", "b", "c")


def rule(effect: RuleEffect, target: str, *conditions: Condition, **extra):  # type: ignore[no-untyped-def]
    return SdlcRule(
        id=f"T-{effect}-{target}",
        effect=effect,
        conditions=conditions,
        target_attribute=target,
        description=f"test {effect} {target}",
        provenance="test",
        **extra,
    )


def test_a_veto_keeps_the_score_but_not_the_first_place() -> None:
    veto = rule(RuleEffect.VETO, "a", Condition(F.REQUIREMENT_STABILITY, ">=", 5))
    result = score_candidates(P, config((veto,), WEIGHTS))
    # MCDA: a 100, c 50, b 37.5 -> a is vetoed from first -> c, a, b
    assert result.ranking == ("c", "a", "b")
    assert result.by_key("a").score == 100.0  # type: ignore[union-attr]
    assert result.by_key("a").vetoed_by == (veto.id,)  # type: ignore[union-attr]
    (application,) = result.rules
    assert application.changed_ranking and application.affected_candidate == "a"
    assert application.trigger_values == {"requirement_stability": 5}


def test_a_veto_that_does_not_trigger_or_is_not_first_changes_nothing() -> None:
    not_triggered = rule(RuleEffect.VETO, "a", Condition(F.REQUIREMENT_STABILITY, "<=", 2))
    assert score_candidates(P, config((not_triggered,), WEIGHTS)).rules == ()
    not_first = rule(RuleEffect.VETO, "b", Condition(F.REQUIREMENT_STABILITY, ">=", 5))
    result = score_candidates(P, config((not_first,), WEIGHTS))
    assert result.ranking == ("a", "c", "b")
    assert result.rules[0].changed_ranking is False


def test_a_boost_is_bounded_and_capped() -> None:
    boost = rule(RuleEffect.BOOST, "b", Condition(F.SECURITY_RISK, ">=", 4), uplift=25)
    result = score_candidates(P, config((boost,), WEIGHTS))
    # b: 37.5 + 25 = 62.5 > c 50 -> a, b, c
    assert result.by_key("b").score == 62.5  # type: ignore[union-attr]
    assert result.by_key("b").mcda_score == 37.5  # type: ignore[union-attr]
    assert result.ranking == ("a", "b", "c")
    capped = rule(RuleEffect.BOOST, "a", Condition(F.SECURITY_RISK, ">=", 4), uplift=25)
    assert score_candidates(P, config((capped,), WEIGHTS)).by_key("a").score == 100.0  # type: ignore[union-attr]
    with pytest.raises(RuleConfigurationError):
        rule(RuleEffect.BOOST, "a", Condition(F.SECURITY_RISK, ">=", 4), uplift=40)


def test_require_top_n_moves_the_best_holder_to_position_n() -> None:
    top1 = rule(RuleEffect.REQUIRE_TOP_N, "b", Condition(F.SECURITY_RISK, "==", 4), n=1)
    result = score_candidates(P, config((top1,), WEIGHTS))
    assert result.ranking == ("b", "a", "c")
    top2 = rule(RuleEffect.REQUIRE_TOP_N, "b", Condition(F.SECURITY_RISK, "==", 4), n=2)
    assert score_candidates(P, config((top2,), WEIGHTS)).ranking == ("a", "b", "c")
    satisfied = rule(RuleEffect.REQUIRE_TOP_N, "c", Condition(F.SECURITY_RISK, "==", 4), n=2)
    result = score_candidates(P, config((satisfied,), WEIGHTS))
    assert result.ranking == ("a", "c", "b") and result.rules[0].changed_ranking is False


def test_require_top_one_never_overrides_a_veto() -> None:
    veto = rule(RuleEffect.VETO, "b", Condition(F.SECURITY_RISK, ">=", 4))
    top1 = rule(RuleEffect.REQUIRE_TOP_N, "b", Condition(F.SECURITY_RISK, ">=", 4), n=1)
    result = score_candidates(P, config((veto, top1), WEIGHTS))
    assert result.ranking[0] != "b" and result.ranking[1] == "b"


def test_the_reversal_condition_by_hand() -> None:
    result = score_candidates(P, config(weights=WEIGHTS))
    # Runner-up is c (50). Only stability moves a (c and b never pass a otherwise):
    #   stability 4 -> a = 50 * (1 + 0.5) = 75   (a still first)
    #   stability 3 -> a = 50, ties c, file order keeps a first
    #   stability 2 -> a = 25 -> c 50 first     => the smallest reversing change is 5 -> 2
    (condition,) = reversal_conditions(P, config(weights=WEIGHTS), result)
    assert (condition.factor, condition.from_score, condition.to_score) == (
        F.REQUIREMENT_STABILITY,
        5,
        2,
    )
    assert condition.new_top == "c"


def test_a_profile_must_score_all_thirteen_factors_on_the_scale() -> None:
    bad = dict(P)
    del bad[F.SYSTEM_SIZE]
    with pytest.raises(ValueError, match="all 13"):
        score_candidates(bad, config())
    with pytest.raises(ValueError):
        score_candidates({**P, F.SYSTEM_SIZE: 6}, config())


def test_bands_and_rounding_by_hand() -> None:
    bands = (Band(0, 5), Band(1, 4), Band(11, 3), Band(26, 2), Band(51, 1))
    assert [band_value(bands, x) for x in (0, 0.5, 1, 10.99, 11, 25, 26, 50, 51, 100)] == [
        5, 5, 4, 4, 3, 3, 2, 2, 1, 1,
    ]  # fmt: skip
    assert [round_half_up(x) for x in (2.5, 3.49, 3.5, 11 / 3, 7 / 3)] == [3, 3, 4, 4, 2]


# --- the packaged ruleset, by hand -------------------------------------------


def test_the_packaged_ruleset_computes_one_candidate_by_hand() -> None:
    cfg = packaged_sdlc_rules().config
    p = profile(
        requirement_stability=1,
        expected_frequency_of_change=5,
        stakeholder_availability=5,
        regulatory_criticality=1,
        security_risk=2,
        need_for_formal_verification=1,
        consequences_of_failure=2,
        testing_and_documentation_requirements=2,
        system_size=2,
    )
    agile = score_candidates(p, cfg).by_key("agile")
    assert agile is not None
    # Agile coefficients x weights x norm, factor by factor (norm: 1 -> -1, 2 -> -0.5, 5 -> +1):
    #   stability        1.5 * -2 * -1   = +3.0
    #   regulatory       1.5 * -1 * -1   = +1.5
    #   security         1.5 * -1 * -0.5 = +0.75
    #   complexity       1.0 *  0 *  0   =  0
    #   size             0.5 * -1 * -0.5 = +0.25
    #   legacy           0.75* -1 *  0   =  0
    #   change           1.5 * +2 * +1   = +3.0
    #   delivery         1.0 * +1 *  0   =  0
    #   stakeholders     0.75* +2 * +1   = +1.5
    #   testing/docs     1.0 * -1 * -0.5 = +0.5
    #   budget/schedule  0.5 * +1 *  0   =  0
    #   verification     1.25* -2 * -1   = +2.5
    #   consequences     1.25* -1 * -0.5 = +0.625
    #   raw = 13.625
    #   max = 1.5*2 + 1.5*1 + 1.5*1 + 0 + 0.5*1 + 0.75*1 + 1.5*2 + 1.0*1 + 0.75*2 + 1.0*1
    #         + 0.5*1 + 1.25*2 + 1.25*1
    #       = 3 + 1.5 + 1.5 + 0.5 + 0.75 + 3 + 1 + 1.5 + 1 + 0.5 + 2.5 + 1.25 = 18.0
    #   mcda = 50 * (1 + 13.625 / 18) = 50 * 1.756944... = 87.85 (to 2 dp)
    assert agile.raw == 13.625 and agile.max_raw == 18.0
    assert agile.mcda_score == 87.85


def test_the_packaged_ruleset_is_complete_and_versioned() -> None:
    rules = packaged_sdlc_rules()
    cfg = rules.config
    assert cfg.version == "1.0.0" and cfg.ruleset_ref.startswith("sdlc_rules@1.0.0#")
    assert cfg.candidate_keys == (
        "waterfall",
        "v_model",
        "spiral",
        "agile",
        "devsecops",
        "agile_v_model_hybrid",
        "agile_devsecops_hybrid",
    )
    assert {r.effect for r in cfg.rules} == set(RuleEffect)
    assert len(rules.content_sha256) == 64
