"""The deterministic coverage tracker (architecture C.4; FR-ELI-002, FR-ELI-003, FR-ELI-007)."""

from __future__ import annotations

import pytest

from reqpilot.domain import coverage as tracker
from reqpilot.domain.enums import AnswerStatus, TopicStatus

pytestmark = pytest.mark.unit

PLAN = [
    tracker.TopicPlan("optional_early", priority=1, required=False),
    tracker.TopicPlan("required_late", priority=9, required=True),
    tracker.TopicPlan("required_early", priority=2, required=True),
]


def test_initial_coverage_lists_every_applicable_topic_not_started() -> None:
    coverage = tracker.initial_coverage(PLAN)
    assert set(coverage) == {"optional_early", "required_late", "required_early"}
    assert {e["status"] for e in coverage.values()} == {TopicStatus.NOT_STARTED}
    assert not tracker.is_complete(coverage)


def test_topics_are_taken_up_required_first_then_by_priority() -> None:
    coverage = tracker.initial_coverage(PLAN)
    order = []
    while (topic := tracker.select_next_topic(coverage)) is not None:
        order.append(topic)
        coverage = tracker.start_topic(coverage, topic)
        outcome = tracker.decide_after_assessment(
            AnswerStatus.COMPLETE, followups_this_topic=0, max_followups=2
        )
        coverage = tracker.apply_outcome(coverage, topic, outcome, AnswerStatus.COMPLETE)
    assert order == ["required_early", "required_late", "optional_early"]
    assert tracker.is_complete(coverage)


def test_a_topic_in_progress_is_resumed_before_any_new_one() -> None:
    coverage = tracker.start_topic(tracker.initial_coverage(PLAN), "optional_early")
    assert tracker.select_next_topic(coverage) == "optional_early"


def test_selection_does_not_depend_on_dict_order() -> None:
    forward = tracker.initial_coverage(PLAN)
    backward = dict(reversed(list(forward.items())))
    assert tracker.select_next_topic(forward) == tracker.select_next_topic(backward)


@pytest.mark.parametrize(
    "status", [AnswerStatus.VAGUE, AnswerStatus.INCOMPLETE, AnswerStatus.INCONSISTENT]
)
def test_follow_ups_are_bounded_deterministically(status: AnswerStatus) -> None:
    # 0 follow-ups so far -> a follow-up is allowed, and the counter moves to 1.
    first = tracker.decide_after_assessment(status, followups_this_topic=0, max_followups=2)
    assert first.route is tracker.Route.FOLLOW_UP and first.followups_this_topic == 1
    # 1 -> 2, still allowed.
    second = tracker.decide_after_assessment(status, followups_this_topic=1, max_followups=2)
    assert second.route is tracker.Route.FOLLOW_UP and second.followups_this_topic == 2
    # At the bound -> no further follow-up; the topic closes as UNRESOLVED.
    third = tracker.decide_after_assessment(status, followups_this_topic=2, max_followups=2)
    assert third.route is tracker.Route.ADVANCE
    assert third.topic_status is TopicStatus.UNRESOLVED and third.followups_this_topic == 0


def test_a_complete_answer_closes_the_topic_as_covered() -> None:
    outcome = tracker.decide_after_assessment(
        AnswerStatus.COMPLETE, followups_this_topic=1, max_followups=2
    )
    assert (outcome.route, outcome.topic_status) == (tracker.Route.ADVANCE, TopicStatus.COVERED)


def test_a_zero_bound_never_asks_a_follow_up() -> None:
    outcome = tracker.decide_after_assessment(
        AnswerStatus.VAGUE, followups_this_topic=0, max_followups=0
    )
    assert outcome.route is tracker.Route.ADVANCE and outcome.topic_status is TopicStatus.UNRESOLVED


def test_counts_can_never_be_negative() -> None:
    with pytest.raises(ValueError):
        tracker.decide_after_assessment(
            AnswerStatus.VAGUE, followups_this_topic=-1, max_followups=2
        )


def test_an_unknown_or_closed_topic_is_refused() -> None:
    coverage = tracker.initial_coverage(PLAN)
    with pytest.raises(KeyError):
        tracker.start_topic(coverage, "not_in_template")
    closed = tracker.apply_outcome(
        tracker.start_topic(coverage, "required_early"),
        "required_early",
        tracker.decide_after_assessment(
            AnswerStatus.COMPLETE, followups_this_topic=0, max_followups=2
        ),
        AnswerStatus.COMPLETE,
    )
    with pytest.raises(ValueError):
        tracker.start_topic(closed, "required_early")


def test_question_and_answer_bookkeeping() -> None:
    coverage = tracker.start_topic(tracker.initial_coverage(PLAN), "required_early")
    coverage = tracker.record_question(coverage, "required_early", is_followup=False)
    coverage = tracker.record_question(coverage, "required_early", is_followup=True)
    coverage = tracker.record_answer(coverage, "required_early", seq=4)
    entry = coverage["required_early"]
    assert (entry["questions"], entry["followups"], entry["last_addressed_seq"]) == (2, 1, 4)


def test_the_summary_lists_covered_unresolved_and_remaining() -> None:
    coverage = tracker.initial_coverage(PLAN)
    coverage = tracker.apply_outcome(
        tracker.start_topic(coverage, "required_early"),
        "required_early",
        tracker.decide_after_assessment(
            AnswerStatus.VAGUE, followups_this_topic=2, max_followups=2
        ),
        AnswerStatus.VAGUE,
    )
    summary = tracker.summarise(coverage)
    assert summary.unresolved == ("required_early",)
    assert summary.remaining == ("required_late", "optional_early")
    assert summary.required_remaining == ("required_late",)
    assert not summary.complete


def test_the_next_role_suggestion_is_explained_by_gaps() -> None:
    templates = {
        "ops": ("operations", [tracker.TopicPlan("a", 1, True), tracker.TopicPlan("b", 2, False)]),
        "sec": ("security", [tracker.TopicPlan("c", 1, True), tracker.TopicPlan("d", 2, True)]),
    }
    suggestion = tracker.suggest_next_role(["a"], templates)
    assert suggestion is not None
    assert suggestion.stakeholder_role == "security" and suggestion.gap_topics == ("c", "d")
    assert suggestion.score == 4
    assert tracker.suggest_next_role(["a", "b", "c", "d"], templates) is None
