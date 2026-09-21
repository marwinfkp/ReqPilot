"""Deterministic validation of role #4's proposals (architecture E #4: "question
references the defect; no duplicate open question for the same defect").

A question is accepted only if it echoes the defect it was given, is targeted
rather than generic, is bounded, repeats no earlier question about the
requirement, and claims no authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.clarification import ClarificationProposal
from reqpilot.agents.validation.elicitation import claims_authority, normalise_question
from reqpilot.rules.elicitation import ElicitationRules


@dataclass(frozen=True)
class ClarificationDecision:
    accepted: bool
    question: str
    expected_answer_shape: str
    findings: tuple[str, ...]


def validate_clarification(
    proposal: ClarificationProposal,
    *,
    defect_id: str,
    prior_questions: Sequence[str],
    rules: ElicitationRules,
) -> ClarificationDecision:
    question = " ".join(proposal.question.split())
    shape = " ".join(proposal.expected_answer_shape.split())
    findings: list[str] = []
    if proposal.defect_id.strip().lower() != defect_id.lower():
        findings.append("defect_id does not match the defect the question was raised for")
    normalised = normalise_question(question)
    if normalised in {normalise_question(g) for g in rules.generic_questions}:
        findings.append("the question is generic; it must target the specific defect")
    if len(question.split()) < rules.clarification_min_question_words:
        findings.append(
            f"the question has fewer than {rules.clarification_min_question_words} words"
        )
    if len(question) > rules.clarification_max_question_chars:
        findings.append("the question is too long")
    if not shape or len(shape) > rules.clarification_max_answer_shape_chars:
        findings.append("expected_answer_shape is empty or too long")
    if normalised in {normalise_question(q) for q in prior_questions}:
        findings.append("the question repeats an earlier clarification question")
    phrase = claims_authority(question, rules.authority_phrases)
    if phrase is not None:
        findings.append(f"the question claims authority ({phrase!r})")
    return ClarificationDecision(not findings, question, shape, tuple(findings))
