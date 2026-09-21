"""The deterministic topic-coverage tracker (architecture C.4; ``FR-ELI-002``, ``FR-ELI-003``).

Pure functions over plain values: no database, no model, no graph. This module
alone decides

* which topics of a session's template apply, and in what order they are taken
  up (``select_next_topic``);
* what an answer assessment *means* for the interview - ask a follow-up, or
  close the topic as covered or as unresolved (``decide_after_assessment``);
* when coverage is complete (``is_complete``).

The model proposes questions and assessments; it never writes coverage, never
chooses the next topic and never moves the follow-up counter. The follow-up
bound is the architecture's ``followups_this_topic < max_followups`` - state
compared with a versioned rule, not an instruction in a prompt.

Coverage is a JSON-serialisable mapping stored on the session::

    {topic_id: {"status", "required", "priority", "questions", "followups",
                "last_assessment", "last_addressed_seq"}}
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from reqpilot.domain.enums import AnswerStatus, TopicStatus

#: Statuses that close a topic. An UNRESOLVED topic is closed but not covered:
#: the analyst sees it as such.
CLOSED_STATUSES = frozenset({TopicStatus.COVERED, TopicStatus.UNRESOLVED})


@dataclass(frozen=True)
class TopicPlan:
    """What a template says about one topic."""

    topic_id: str
    priority: int
    required: bool


def initial_coverage(plan: Iterable[TopicPlan]) -> dict[str, dict[str, Any]]:
    """Every applicable topic, not yet started."""
    coverage: dict[str, dict[str, Any]] = {}
    for topic in plan:
        coverage[topic.topic_id] = {
            "status": str(TopicStatus.NOT_STARTED),
            "required": topic.required,
            "priority": topic.priority,
            "questions": 0,
            "followups": 0,
            "last_assessment": None,
            "last_addressed_seq": None,
        }
    return coverage


def status_of(coverage: Mapping[str, Mapping[str, Any]], topic_id: str) -> TopicStatus:
    return TopicStatus(coverage[topic_id]["status"])


def select_next_topic(coverage: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The topic to take up next, or ``None`` when coverage is complete.

    A topic already in progress is resumed. Otherwise the next not-started
    topic: required before optional, then by the template's priority, then by
    id - a total order, so the choice never depends on a model or on dict order.
    """
    in_progress = sorted(
        t for t, entry in coverage.items() if entry["status"] == TopicStatus.IN_PROGRESS
    )
    if in_progress:
        return in_progress[0]
    candidates = [
        (not bool(entry["required"]), int(entry["priority"]), topic_id)
        for topic_id, entry in coverage.items()
        if entry["status"] == TopicStatus.NOT_STARTED
    ]
    return min(candidates)[2] if candidates else None


def is_complete(coverage: Mapping[str, Mapping[str, Any]]) -> bool:
    """Every applicable topic is closed (covered, or unresolved at the bound)."""
    return all(TopicStatus(e["status"]) in CLOSED_STATUSES for e in coverage.values())


def _updated(
    coverage: Mapping[str, Mapping[str, Any]], topic_id: str, **changes: Any
) -> dict[str, dict[str, Any]]:
    if topic_id not in coverage:
        raise KeyError(f"topic {topic_id!r} is not applicable to this session")
    result = {t: dict(e) for t, e in coverage.items()}
    result[topic_id].update(changes)
    return result


def start_topic(
    coverage: Mapping[str, Mapping[str, Any]], topic_id: str
) -> dict[str, dict[str, Any]]:
    if status_of(coverage, topic_id) in CLOSED_STATUSES:
        raise ValueError(f"topic {topic_id!r} is already closed")
    return _updated(coverage, topic_id, status=str(TopicStatus.IN_PROGRESS))


def record_question(
    coverage: Mapping[str, Mapping[str, Any]], topic_id: str, *, is_followup: bool
) -> dict[str, dict[str, Any]]:
    entry = coverage[topic_id]
    return _updated(
        coverage,
        topic_id,
        questions=int(entry["questions"]) + 1,
        followups=int(entry["followups"]) + (1 if is_followup else 0),
    )


def record_answer(
    coverage: Mapping[str, Mapping[str, Any]], topic_id: str, *, seq: int
) -> dict[str, dict[str, Any]]:
    return _updated(coverage, topic_id, last_addressed_seq=seq)


class Route(StrEnum):
    """What happens after an answer is assessed. Chosen here, never by the model."""

    FOLLOW_UP = "follow_up"
    ADVANCE = "advance"


@dataclass(frozen=True)
class AssessmentOutcome:
    route: Route
    #: The topic's status after this assessment.
    topic_status: TopicStatus
    #: The follow-up counter for the topic after this assessment.
    followups_this_topic: int


def decide_after_assessment(
    status: AnswerStatus, *, followups_this_topic: int, max_followups: int
) -> AssessmentOutcome:
    """The C.4 router's decision, as a pure function (``FR-ELI-003``).

    * A complete answer closes the topic as covered.
    * A vague, incomplete or inconsistent answer earns a follow-up while
      ``followups_this_topic < max_followups``.
    * At the bound the topic closes as UNRESOLVED - visible to the analyst - and
      the interview moves on. It never loops, whatever the model says.
    """
    if followups_this_topic < 0 or max_followups < 0:
        raise ValueError("follow-up counts are never negative")
    if status is AnswerStatus.COMPLETE:
        return AssessmentOutcome(Route.ADVANCE, TopicStatus.COVERED, 0)
    if followups_this_topic < max_followups:
        return AssessmentOutcome(Route.FOLLOW_UP, TopicStatus.IN_PROGRESS, followups_this_topic + 1)
    return AssessmentOutcome(Route.ADVANCE, TopicStatus.UNRESOLVED, 0)


def apply_outcome(
    coverage: Mapping[str, Mapping[str, Any]],
    topic_id: str,
    outcome: AssessmentOutcome,
    assessment: AnswerStatus,
) -> dict[str, dict[str, Any]]:
    return _updated(
        coverage,
        topic_id,
        status=str(outcome.topic_status),
        last_assessment=str(assessment),
    )


@dataclass(frozen=True)
class CoverageSummary:
    applicable: tuple[str, ...]
    covered: tuple[str, ...]
    unresolved: tuple[str, ...]
    in_progress: tuple[str, ...]
    remaining: tuple[str, ...]
    required_remaining: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.remaining and not self.in_progress


def summarise(coverage: Mapping[str, Mapping[str, Any]]) -> CoverageSummary:
    """Live coverage and what is left (``FR-ELI-006``), in taking-up order."""

    def order(topic_id: str) -> tuple[bool, int, str]:
        entry = coverage[topic_id]
        return (not bool(entry["required"]), int(entry["priority"]), topic_id)

    def having(*statuses: TopicStatus) -> tuple[str, ...]:
        return tuple(
            sorted(
                (t for t, e in coverage.items() if TopicStatus(e["status"]) in statuses),
                key=order,
            )
        )

    remaining = having(TopicStatus.NOT_STARTED)
    return CoverageSummary(
        applicable=tuple(sorted(coverage, key=order)),
        covered=having(TopicStatus.COVERED),
        unresolved=having(TopicStatus.UNRESOLVED),
        in_progress=having(TopicStatus.IN_PROGRESS),
        remaining=remaining,
        required_remaining=tuple(t for t in remaining if coverage[t]["required"]),
    )


@dataclass(frozen=True)
class RoleSuggestion:
    """``FR-ELI-007`` (secondary): who to interview next, and why."""

    stakeholder_role: str
    template_id: str
    #: Uncovered topics this role's template would address, required first.
    gap_topics: tuple[str, ...]
    score: int


def suggest_next_role(
    covered_topics: Iterable[str],
    templates: Mapping[str, tuple[str, Iterable[TopicPlan]]],
) -> RoleSuggestion | None:
    """The template whose topics best fill the project's coverage gaps.

    ``templates`` maps ``template_id -> (stakeholder_role, topic plans)``. A
    gap topic scores 2 when the template marks it required and 1 when optional;
    ties break on the template id. Explainable from the gap list alone - no
    model is involved (``FR-ELI-007``).
    """
    covered = set(covered_topics)
    best: RoleSuggestion | None = None
    for template_id in sorted(templates):
        role, plans = templates[template_id]
        gaps = [p for p in plans if p.topic_id not in covered]
        if not gaps:
            continue
        score = sum(2 if p.required else 1 for p in gaps)
        ranked = sorted(gaps, key=lambda p: (not p.required, p.priority))
        ordered = tuple(p.topic_id for p in ranked)
        if best is None or score > best.score:
            best = RoleSuggestion(role, template_id, ordered, score)
    return best
