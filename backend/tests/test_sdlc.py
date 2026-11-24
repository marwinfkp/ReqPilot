"""Tests for the deterministic SDLC scoring engine (app/agents/sdlc_selection.py).

Formula being tested (from the module docstring):
    x      = (rating - 1) / 4
    fit    = x if affinity > 0 else 1 - x
    score  = 100 * sum(weight * |affinity| * fit) / sum(weight * |affinity|)
then decision-rule penalties, then ranking (highest score = recommendation).
"""
import random

import pytest

from app.agents import sdlc_selection as sdlc
from tests.helpers import CASES, run_until

# Factor ratings produced by the rules agents for two benchmark cases (copied here so the
# scoring tests do not depend on the other agents).
LOAN_FACTORS = {"requirement_stability": 4, "regulatory_criticality": 4, "security_risk": 3,
                "project_complexity": 3, "system_size": 3, "legacy_dependence": 2, "expected_change": 2,
                "continuous_delivery": 3, "stakeholder_availability": 4, "testing_documentation": 4,
                "budget_schedule_constraints": 3, "formal_verification": 4, "failure_consequence": 4}
FRAUD_FACTORS = {"requirement_stability": 2, "regulatory_criticality": 3, "security_risk": 4,
                 "project_complexity": 4, "system_size": 3, "legacy_dependence": 1, "expected_change": 4,
                 "continuous_delivery": 2, "stakeholder_availability": 2, "testing_documentation": 3,
                 "budget_schedule_constraints": 3, "formal_verification": 2, "failure_consequence": 4}


def all_factors(value: int) -> dict:
    return {fid: value for fid in sdlc.FACTOR_IDS}


def scores(ranking: list[dict]) -> dict[str, float]:
    return {r["model"]: r["score"] for r in ranking}


# --------------------------------------------------------------------------
# score_model: the arithmetic
# --------------------------------------------------------------------------
def test_score_model_hand_computed_two_factor_example():
    """Only two factors carry weight, so the score can be computed by hand.

    Waterfall: requirement_stability affinity +2, expected_change affinity -2.
      stability = 4 -> x = 0.75, fit = 0.75, points = 1 * 2 * 0.75 = 1.5 of 2
      change    = 2 -> x = 0.25, fit = 1 - 0.25 = 0.75, points = 2 * 2 * 0.75 = 3.0 of 4
      score = 100 * (1.5 + 3.0) / (2 + 4) = 75.0
    """
    weights = {fid: 0.0 for fid in sdlc.FACTOR_IDS}
    weights.update(requirement_stability=1.0, expected_change=2.0)
    values = all_factors(3)
    values.update(requirement_stability=4, expected_change=2)

    score, breakdown = sdlc.score_model("Waterfall", values, weights)

    assert score == pytest.approx(75.0)
    by_factor = {b["factor"]: b for b in breakdown}
    assert by_factor["requirement_stability"]["fit"] == 0.75
    assert by_factor["requirement_stability"]["points"] == 1.5
    assert by_factor["requirement_stability"]["max_points"] == 2.0
    assert by_factor["expected_change"]["fit"] == 0.75
    assert by_factor["expected_change"]["points"] == 3.0
    assert by_factor["expected_change"]["max_points"] == 4.0
    # Factors with affinity 0 for Waterfall are not part of the breakdown at all.
    assert "security_risk" not in by_factor and "system_size" not in by_factor


def test_score_model_with_default_weights_hand_computed():
    """Agile with every factor rated 5 (x = 1): only factors with a POSITIVE affinity score.

    positive: expected_change 1.2*2 + continuous_delivery 1.0*1 + stakeholder 0.7*2 + budget 0.7*1 = 5.5
    all non-zero affinities: 2.0+1.5+0.8+0.8+2.4+1.0+1.4+1.0+0.7+2.4+1.3 = 15.3
    """
    score, _ = sdlc.score_model("Agile", all_factors(5), sdlc.DEFAULT_WEIGHTS)
    assert score == pytest.approx(100 * 5.5 / 15.3)


@pytest.mark.parametrize("model", sdlc.MODELS)
def test_neutral_ratings_give_exactly_fifty(model):
    """Rating 3 means x = 0.5, so fit = 0.5 for every factor whatever the affinity sign."""
    score, _ = sdlc.score_model(model, all_factors(3), sdlc.DEFAULT_WEIGHTS)
    assert score == pytest.approx(50.0)


def test_all_zero_weights_do_not_divide_by_zero():
    score, _ = sdlc.score_model("Agile", all_factors(4), {fid: 0.0 for fid in sdlc.FACTOR_IDS})
    assert score == 0.0


def test_scores_always_within_0_and_100():
    rng = random.Random(42)  # seeded: the test is repeatable
    for _ in range(300):
        values = {fid: rng.randint(1, 5) for fid in sdlc.FACTOR_IDS}
        weights = {fid: rng.uniform(0, 5) for fid in sdlc.FACTOR_IDS}
        ranking, _ = sdlc.rank_models(values, weights)
        for entry in ranking:
            assert 0 <= entry["base_score"] <= 100
            assert 0 <= entry["score"] <= 100


# --------------------------------------------------------------------------
# rank_models: ranking, decision rules, recommendation
# --------------------------------------------------------------------------
def test_ranking_is_sorted_and_numbered():
    ranking, _ = sdlc.rank_models(LOAN_FACTORS, sdlc.DEFAULT_WEIGHTS)
    assert [r["rank"] for r in ranking] == list(range(1, len(sdlc.MODELS) + 1))
    assert sorted(r["model"] for r in ranking) == sorted(sdlc.MODELS)
    assert all(a["score"] >= b["score"] for a, b in zip(ranking, ranking[1:]))
    for entry in ranking:
        assert entry["suitable"] == (entry["score"] >= sdlc.SUITABLE_THRESHOLD)
        assert entry["is_hybrid"] == entry["model"].startswith("Hybrid")


def test_rule_r1_volatile_requirements_penalise_waterfall_and_v_model():
    stable = all_factors(3)
    volatile = {**stable, "requirement_stability": 2}

    ranking, rules = sdlc.rank_models(volatile, sdlc.DEFAULT_WEIGHTS)
    assert [r["rule"] for r in rules] == ["R1"]
    by_model = {r["model"]: r for r in ranking}
    assert by_model["Waterfall"]["penalties"] == [{"rule": "R1", "description": sdlc.DECISION_RULES[0][1],
                                                  "points": -10}]
    assert by_model["Waterfall"]["score"] == pytest.approx(by_model["Waterfall"]["base_score"] - 10, abs=0.11)
    assert by_model["V-Model"]["score"] == pytest.approx(by_model["V-Model"]["base_score"] - 5, abs=0.11)
    assert by_model["Agile"]["penalties"] == []

    # Boundary: stability 3 does NOT fire the rule.
    _, rules = sdlc.rank_models(stable, sdlc.DEFAULT_WEIGHTS)
    assert rules == []


@pytest.mark.parametrize("factor, value, rule, model, points", [
    ("regulatory_criticality", 4, "R2", "Agile", -10),
    ("continuous_delivery", 4, "R3", "Waterfall", -10),
    ("continuous_delivery", 4, "R3", "V-Model", -5),
    ("security_risk", 4, "R4", "Waterfall", -5),
])
def test_each_decision_rule_fires_at_its_threshold(factor, value, rule, model, points):
    values = {**all_factors(3), factor: value}
    ranking, rules = sdlc.rank_models(values, sdlc.DEFAULT_WEIGHTS)
    assert rule in [r["rule"] for r in rules]
    penalties = {r["model"]: r["penalties"] for r in ranking}[model]
    assert {"rule": rule, "points": points} in [{"rule": p["rule"], "points": p["points"]} for p in penalties]
    # One step below the threshold the rule does not fire.
    _, rules_below = sdlc.rank_models({**values, factor: value - 1}, sdlc.DEFAULT_WEIGHTS)
    assert rule not in [r["rule"] for r in rules_below]


def test_penalty_never_makes_a_score_negative():
    weights = {fid: 0.0 for fid in sdlc.FACTOR_IDS}  # every base score is 0
    values = {**all_factors(3), "requirement_stability": 1, "continuous_delivery": 5, "security_risk": 5}
    ranking, _ = sdlc.rank_models(values, weights)
    assert {r["model"]: r["score"] for r in ranking}["Waterfall"] == 0.0


def test_scoring_is_deterministic():
    first, rules1 = sdlc.rank_models(FRAUD_FACTORS, sdlc.DEFAULT_WEIGHTS)
    second, rules2 = sdlc.rank_models(dict(FRAUD_FACTORS), dict(sdlc.DEFAULT_WEIGHTS))
    assert first == second and rules1 == rules2


def test_weights_change_the_recommendation():
    """Same project, different factor weights -> a different top model."""
    values = {**all_factors(3), "expected_change": 5, "formal_verification": 5}
    change_heavy, _ = sdlc.rank_models(values, {**sdlc.DEFAULT_WEIGHTS, "expected_change": 5.0})
    formal_heavy, _ = sdlc.rank_models(values, {**sdlc.DEFAULT_WEIGHTS, "formal_verification": 5.0})

    assert change_heavy[0]["model"] == "Hybrid (Agile + DevSecOps)"
    assert formal_heavy[0]["model"] == "Hybrid (Agile + V-Model)"
    # The winners are the models that "like" the heavily weighted factor.
    assert sdlc.AFFINITY[change_heavy[0]["model"]]["expected_change"] > 0
    assert sdlc.AFFINITY[formal_heavy[0]["model"]]["formal_verification"] > 0
    assert scores(change_heavy)["Agile"] > scores(formal_heavy)["Agile"]


def test_hybrid_is_recommended_only_when_it_scores_best():
    rng = random.Random(7)
    hybrid_wins = 0
    for _ in range(300):
        values = {fid: rng.randint(1, 5) for fid in sdlc.FACTOR_IDS}
        ranking, _ = sdlc.rank_models(values, sdlc.DEFAULT_WEIGHTS)
        best_score = max(r["score"] for r in ranking)
        assert ranking[0]["score"] == best_score  # the recommendation is always the best score
        if ranking[0]["is_hybrid"]:
            hybrid_wins += 1
            assert all(ranking[0]["score"] >= r["score"] for r in ranking if not r["is_hybrid"])
    assert hybrid_wins > 0  # the random sample did exercise the hybrid case


def test_explanations_for_hybrid_and_single_model_recommendations():
    hybrid_ranking, _ = sdlc.rank_models(FRAUD_FACTORS, sdlc.DEFAULT_WEIGHTS)
    assert hybrid_ranking[0]["model"] == "Hybrid (Agile + DevSecOps)"
    factors = [{"id": fid, "value": v} for fid, v in FRAUD_FACTORS.items()]
    text, trade_offs, hybrid_text = sdlc.explain(hybrid_ranking, factors)
    assert text.startswith("Hybrid (Agile + DevSecOps) ranks first")
    assert "Combined components" in hybrid_text
    assert "margin" in text  # 62.0 vs 61.1 is within 5 points
    assert trade_offs  # always lists the disadvantages at least

    single_ranking, _ = sdlc.rank_models(LOAN_FACTORS, sdlc.DEFAULT_WEIGHTS)
    assert single_ranking[0]["model"] == "V-Model"
    _, _, hybrid_text = sdlc.explain(single_ranking, [{"id": f, "value": v} for f, v in LOAN_FACTORS.items()])
    assert hybrid_text == ""


def test_low_scores_are_flagged_as_low_confidence():
    weights = {fid: 0.0 for fid in sdlc.FACTOR_IDS}
    weights["requirement_stability"] = 1.0
    ranking, _ = sdlc.rank_models({**all_factors(3)}, weights)  # every score = 50 < 60
    text, _, _ = sdlc.explain(ranking, [{"id": f, "value": 3} for f in sdlc.FACTOR_IDS])
    assert "no model reached the suitability threshold" in text


# --------------------------------------------------------------------------
# The agent as a whole (factor derivation + recommendation in the context)
# --------------------------------------------------------------------------
def test_agent_recommends_the_top_ranked_model(db):
    case = CASES["payment_system"]
    ctx = run_until(db, case["requirements_text"], "SDLC Selection", **case["project"])
    result = ctx["sdlc"]
    assert result["recommended_model"] == result["ranking"][0]["model"]
    assert result["is_hybrid"] == result["ranking"][0]["is_hybrid"]
    assert len(result["factors"]) == 13
    assert all(1 <= f["value"] <= 5 and f["value"] == f["rule_value"] for f in result["factors"])
    assert result["generated_by"] == "rules"


def test_agent_is_deterministic_end_to_end(db):
    case = CASES["digital_banking"]
    first = run_until(db, case["requirements_text"], "SDLC Selection", **case["project"])["sdlc"]
    second = run_until(db, case["requirements_text"], "SDLC Selection", **case["project"])["sdlc"]
    assert first == second


def test_agent_uses_configured_weights(db):
    case = CASES["loan_processing"]
    ctx = run_until(db, case["requirements_text"], "Risk", **case["project"])
    ctx["sdlc_weights"] = {"testing_documentation": 4.0}
    sdlc.run(ctx)
    assert ctx["sdlc"]["weights"]["testing_documentation"] == 4.0
    assert ctx["sdlc"]["weights"]["expected_change"] == sdlc.DEFAULT_WEIGHTS["expected_change"]


def test_factor_derivation_from_project_details(db):
    text = "[Customer] Customers shall be able to view their account balance on the mobile app."
    ctx = run_until(db, text, "Risk", scale="large", schedule="Go-live in 3 months, fixed date",
                    stakeholder_availability="")
    factors = {f["id"]: f for f in sdlc.derive_factors(ctx)}
    assert factors["system_size"]["value"] == 4  # declared scale "large"
    assert factors["budget_schedule_constraints"]["value"] == 5  # <= 3 months and "fixed"
    # Not provided -> a moderate value is assumed and labelled as an assumption.
    assert factors["stakeholder_availability"]["value"] == 3
    assert factors["stakeholder_availability"]["assumption"] is True

    sdlc.run(ctx)
    assumptions = [f for f in ctx["findings"] if f["issue_type"] == "sdlc_factor"]
    assert any("Stakeholder availability" in f["title"] for f in assumptions)
