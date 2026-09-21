"""Deterministic validation of role #2's proposals (architecture E #2, F.2).

Pure functions. A proposal the checks refuse is never asked or acted on:

* a **question** must be non-empty and bounded, be about exactly the topic the
  coverage tracker selected, carry the follow-up flag the tracker expects, be
  recognisably about that topic (or the issue it follows up), not repeat a
  question already asked in the session, and claim no authority;
* an **assessment** must be about the current topic, and name the issue for
  any status other than "complete".

Neither check can make a topic eligible, change the counter or end the
interview: those are the coverage tracker's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.elicitation import AnswerAssessment, QuestionProposal
from reqpilot.domain.enums import AnswerStatus
from reqpilot.rules.elicitation import ElicitationRules, TopicSpec

_WORD = re.compile(r"[a-z0-9']+")


def normalise_question(text: str) -> str:
    """Case, punctuation and whitespace removed: how duplicates are recognised."""
    return " ".join(_WORD.findall(text.lower()))


def claims_authority(text: str, phrases: Sequence[str]) -> str | None:
    lowered = " ".join(text.lower().split())
    return next((p for p in phrases if p in lowered), None)


@dataclass(frozen=True)
class QuestionDecision:
    accepted: bool
    question: str
    findings: tuple[str, ...]


def validate_question(
    proposal: QuestionProposal,
    *,
    expected_topic: TopicSpec,
    expected_followup: bool,
    asked_before: Sequence[str],
    issue: str | None,
    last_answer: str | None,
    rules: ElicitationRules,
) -> QuestionDecision:
    question = " ".join(proposal.question.split())
    findings: list[str] = []
    if len(question.split()) < rules.min_question_words:
        findings.append(f"the question has fewer than {rules.min_question_words} words")
    if len(question) > rules.max_question_chars:
        findings.append(f"the question is longer than {rules.max_question_chars} characters")
    if proposal.topic_id != expected_topic.topic_id:
        # Covers both an unknown topic and a known-but-ineligible one: the only
        # eligible topic is the one the tracker selected.
        findings.append(
            f"topic_id {proposal.topic_id[:64]!r} is not the selected topic "
            f"{expected_topic.topic_id!r}"
        )
    if proposal.is_followup is not expected_followup:
        findings.append(
            "is_followup does not match the interview state "
            f"(expected {str(expected_followup).lower()})"
        )
    phrase = claims_authority(question, rules.authority_phrases)
    if phrase is not None:
        findings.append(f"the question claims authority ({phrase!r})")
    normalised = normalise_question(question)
    if normalised in {normalise_question(q) for q in asked_before}:
        findings.append("the question repeats one already asked in this session")
    if not _relevant(question, expected_topic, expected_followup, issue, last_answer):
        findings.append(f"the question is not recognisably about {expected_topic.title!r}")
    return QuestionDecision(not findings, question, tuple(findings))


def _content_words(text: str | None) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) >= 5}


def _relevant(
    question: str,
    topic: TopicSpec,
    followup: bool,
    issue: str | None,
    last_answer: str | None,
) -> bool:
    """A deterministic, deliberately lenient relevance test.

    An opening question must mention one of the topic's keywords. A follow-up
    may instead take up the issue or the stakeholder's last answer (sharing a
    content word with either): that is what a targeted follow-up does.
    """
    lowered = question.lower()
    if any(keyword in lowered for keyword in topic.keywords):
        return True
    if followup:
        words = _content_words(question)
        return bool(words & (_content_words(issue) | _content_words(last_answer)))
    return False


@dataclass(frozen=True)
class AssessmentDecision:
    status: AnswerStatus | None
    issue: str | None
    findings: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return self.status is not None


def validate_assessment(assessment: AnswerAssessment, *, expected_topic: str) -> AssessmentDecision:
    findings: list[str] = []
    if assessment.topic_id != expected_topic:
        findings.append(
            f"topic_id {assessment.topic_id[:64]!r} is not the current topic {expected_topic!r}"
        )
    status = AnswerStatus(assessment.status)
    issue = " ".join((assessment.detected_issue or "").split()) or None
    if status is not AnswerStatus.COMPLETE and issue is None:
        findings.append(f"a {status} assessment must name the issue")
    if findings:
        return AssessmentDecision(None, None, tuple(findings))
    return AssessmentDecision(status, issue if status is not AnswerStatus.COMPLETE else None, ())
