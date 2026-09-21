"""P4 rules, templates, role contracts and their deterministic validation (E #2, E #4, F.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from tests.p3_helpers import RULES_DIR

from reqpilot.agents.contracts.clarification import ClarificationProposal
from reqpilot.agents.contracts.elicitation import AnswerAssessment, QuestionProposal
from reqpilot.agents.validation import (
    validate_assessment,
    validate_clarification,
    validate_question,
)
from reqpilot.domain.enums import AnswerStatus
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.elicitation import load_elicitation_rules

pytestmark = pytest.mark.unit

RULES = load_elicitation_rules(RULES_DIR)

#: The problem statement's section 7 checklist, as the approved topic ids.
PROBLEM_STATEMENT_TOPICS = {
    "business_objectives", "users_and_roles", "current_workflow", "inputs", "outputs",
    "business_rules", "exceptional_conditions", "data_collection", "data_retention",
    "authentication", "authorization", "transaction_limits", "audit_reporting",
    "performance", "availability", "integrations", "legacy_systems",
    "regulatory_constraints", "security_constraints", "project_schedule", "project_budget",
}  # fmt: skip


# --- rules and templates ------------------------------------------------------------------


def test_the_taxonomy_is_the_problem_statements_checklist() -> None:
    assert set(RULES.topics) == PROBLEM_STATEMENT_TOPICS


def test_the_templates_cover_the_case_study_roles_and_every_topic() -> None:
    assert set(RULES.stakeholder_roles) == {
        "product_owner", "customer", "operations", "security", "compliance", "risk", "technology"
    }  # fmt: skip
    used = {t.topic_id for tpl in RULES.templates.values() for t in tpl.topics}
    assert used == PROBLEM_STATEMENT_TOPICS
    for template in RULES.templates.values():
        assert any(t.required for t in template.topics), template.ref
        assert len(set(template.topic_ids)) == len(template.topic_ids)


def test_the_follow_up_bound_is_versioned_rule_data() -> None:
    assert RULES.version == "1.0.0" and RULES.templates_version == "1.0.0"
    assert RULES.max_followups_per_topic == 2


def _copy(tmp_path: Path) -> Path:
    for name in ("elicitation.yaml", "interview_templates.yaml"):
        (tmp_path / name).write_text(
            (RULES_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


def _rewrite(path: Path, change) -> None:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(raw)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_a_template_naming_an_unknown_topic_is_refused(tmp_path: Path) -> None:
    directory = _copy(tmp_path)
    _rewrite(
        directory / "interview_templates.yaml",
        lambda raw: raw["rules"]["templates"][0]["topics"].append(
            {"topic_id": "astrology", "priority": 99, "required": False}
        ),
    )
    with pytest.raises(RuleConfigurationError, match="unknown topics"):
        load_elicitation_rules(directory)


def test_a_negative_follow_up_bound_is_refused(tmp_path: Path) -> None:
    directory = _copy(tmp_path)
    _rewrite(
        directory / "elicitation.yaml",
        lambda raw: raw["rules"]["interview"].update({"max_followups_per_topic": -1}),
    )
    with pytest.raises(RuleConfigurationError):
        load_elicitation_rules(directory)


# --- contracts: no authority fields ---------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["approval_status", "lifecycle_state", "coverage", "next_topic", "route", "risk"]
)
def test_authority_fields_are_not_part_of_the_question_contract(field: str) -> None:
    with pytest.raises(ValidationError):
        QuestionProposal.model_validate(
            {"question": "What should it do?", "topic_id": "inputs", "is_followup": False, field: 1}
        )


def test_an_assessment_status_outside_the_four_is_refused() -> None:
    with pytest.raises(ValidationError):
        AnswerAssessment.model_validate({"topic_id": "inputs", "status": "approved"})


# --- question validation ------------------------------------------------------------------


def _question(text: str, *, topic: str = "inputs", followup: bool = False) -> QuestionProposal:
    return QuestionProposal(question=text, topic_id=topic, is_followup=followup)


def _check(proposal: QuestionProposal, **overrides):  # type: ignore[no-untyped-def]
    arguments = {
        "expected_topic": RULES.topics["inputs"],
        "expected_followup": False,
        "asked_before": [],
        "issue": None,
        "last_answer": None,
        "rules": RULES,
    }
    arguments.update(overrides)
    return validate_question(proposal, **arguments)


def test_a_good_question_is_accepted() -> None:
    decision = _check(_question("What documents do applicants need to provide?"))
    assert decision.accepted and not decision.findings


def test_a_question_about_another_topic_is_refused() -> None:
    decision = _check(
        _question("What documents do applicants need to provide?", topic="performance")
    )
    assert not decision.accepted and "not the selected topic" in decision.findings[0]


def test_an_incoherent_follow_up_flag_is_refused() -> None:
    decision = _check(_question("What documents do applicants need to provide?", followup=True))
    assert not decision.accepted and any("is_followup" in f for f in decision.findings)


def test_a_repeated_question_is_refused() -> None:
    decision = _check(
        _question("What documents do applicants need to provide?"),
        asked_before=["what documents do applicants need to provide"],
    )
    assert not decision.accepted and any("repeats" in f for f in decision.findings)


def test_a_question_claiming_authority_is_refused() -> None:
    decision = _check(_question("This input rule is approved; which documents are provided?"))
    assert not decision.accepted and any("authority" in f for f in decision.findings)


def test_an_off_topic_question_is_refused_but_a_targeted_follow_up_is_not() -> None:
    off = _check(_question("How is the weather in the office today?"))
    assert not off.accepted and any("recognisably" in f for f in off.findings)
    targeted = _check(
        _question("Which payslips exactly, and covering how many months?", followup=True),
        expected_followup=True,
        issue="the answer does not say which payslips are required",
    )
    assert targeted.accepted


def test_a_too_short_question_is_refused() -> None:
    assert not _check(_question("Documents?")).accepted


# --- assessment validation -------------------------------------------------------------------


def test_an_assessment_must_be_about_the_current_topic() -> None:
    decision = validate_assessment(
        AnswerAssessment(topic_id="performance", status="complete"), expected_topic="inputs"
    )
    assert not decision.accepted


def test_a_non_complete_assessment_must_name_its_issue() -> None:
    missing = validate_assessment(
        AnswerAssessment(topic_id="inputs", status="vague"), expected_topic="inputs"
    )
    assert not missing.accepted
    named = validate_assessment(
        AnswerAssessment(topic_id="inputs", status="vague", detected_issue="no formats"),
        expected_topic="inputs",
    )
    assert named.status is AnswerStatus.VAGUE and named.issue == "no formats"


def test_a_complete_assessment_carries_no_issue_forward() -> None:
    decision = validate_assessment(
        AnswerAssessment(topic_id="inputs", status="complete", detected_issue="ignored"),
        expected_topic="inputs",
    )
    assert decision.status is AnswerStatus.COMPLETE and decision.issue is None


# --- clarification validation ---------------------------------------------------------------

DEFECT = "0f0e0d0c-0b0a-4900-8000-000000000001"


def _clarify(question: str, **overrides):  # type: ignore[no-untyped-def]
    proposal = ClarificationProposal(
        question=question,
        expected_answer_shape=overrides.pop("shape", "a number of working days"),
        defect_id=overrides.pop("defect_id", DEFECT),
    )
    return validate_clarification(
        proposal, defect_id=DEFECT, prior_questions=overrides.pop("prior", []), rules=RULES
    )


def test_a_targeted_clarification_is_accepted() -> None:
    assert _clarify("Within how many working days must the decision letter be sent?").accepted


@pytest.mark.parametrize("generic", ["Can you clarify this?", "Please clarify."])
def test_a_generic_clarification_is_refused(generic: str) -> None:
    decision = _clarify(generic)
    assert not decision.accepted and any("generic" in f for f in decision.findings)


def test_a_clarification_bound_to_another_defect_is_refused() -> None:
    decision = _clarify(
        "Within how many working days must the decision letter be sent?",
        defect_id="0f0e0d0c-0b0a-4900-8000-000000000002",
    )
    assert not decision.accepted and "defect_id" in decision.findings[0]


def test_a_repeated_clarification_is_refused() -> None:
    question = "Within how many working days must the decision letter be sent?"
    assert not _clarify(question, prior=[question]).accepted


def test_a_clarification_claiming_compliance_is_refused() -> None:
    assert not _clarify("The letter is compliant; within how many days must it be sent?").accepted
