"""Role #2 - Stakeholder Interaction (architecture E #2). LLM-driven, propose-only.

Two calls through the gateway, each a proposal:

* :meth:`StakeholderInteractionRole.propose_question` -> :class:`QuestionProposal`;
* :meth:`StakeholderInteractionRole.assess_answer` -> :class:`AnswerAssessment`.

The role reads no database, writes nothing and routes nothing. Its data is this
session's recent turns and its template's topic framework (least privilege: no
requirement repository, no retrieval, no other project). The topic framework
comes from versioned rules and fills validated prompt slots; everything a
stakeholder said - and every earlier model output - is a fenced data block.
"""

from __future__ import annotations

from reqpilot.agents.contracts.elicitation import (
    AnswerAssessment,
    AnswerAssessmentInput,
    InterviewTurnInput,
    QuestionProposal,
)
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

QUESTION_PROMPT = "stakeholder_interview_question"
ASSESSMENT_PROMPT = "stakeholder_answer_assessment"


def render_turns(turn_input: InterviewTurnInput) -> str:
    if not turn_input.recent_turns:
        return "(no earlier turns in this session)"
    lines = []
    for turn in turn_input.recent_turns:
        who = "Interviewer" if turn.speaker == "interviewer" else "Stakeholder"
        topic = f" [{turn.topic_id}]" if turn.topic_id else ""
        lines.append(f"{who}{topic}: {turn.text}")
    return "\n".join(lines)


def _slot(text: str, limit: int) -> str:
    """A rules-derived value made safe for a prompt slot (single line, bounded)."""
    return " ".join(text.split())[:limit]


class StakeholderInteractionRole:
    role = AgentRole.STAKEHOLDER_INTERACTION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose_question(self, turn: InterviewTurnInput) -> StructuredResult[QuestionProposal]:
        blocks = [
            ContentBlock(
                label="recent_turns",
                text=render_turns(turn),
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=turn.masked,
                synthetic=turn.synthetic,
            )
        ]
        if turn.is_followup and turn.issue:
            blocks.append(
                ContentBlock(
                    label="followup_issue",
                    text=turn.issue,
                    trust_class=TrustClass.MODEL_OUTPUT,
                )
            )
        return self._gateway.generate(
            role=self.role,
            prompt_name=QUESTION_PROMPT,
            params={
                "template_title": _slot(turn.template_title, 80),
                "topic_id": turn.topic_id,
                "topic_title": _slot(turn.topic_title, 80),
                "topic_description": _slot(turn.topic_description, 300),
                "expected_answer_shape": _slot(turn.expected_answer_shape, 200),
                "covered_topics": ", ".join(turn.covered_topics)[:800],
                "remaining_topics": ", ".join(turn.remaining_topics)[:800],
                "question_kind": "follow-up" if turn.is_followup else "opening",
                "followup_depth": str(turn.followup_depth),
                "max_followups": str(turn.max_followups),
            },
            content=blocks,
            schema=QuestionProposal,
        )

    def assess_answer(self, item: AnswerAssessmentInput) -> StructuredResult[AnswerAssessment]:
        blocks = [
            ContentBlock(label="question", text=item.question, trust_class=TrustClass.MODEL_OUTPUT),
            ContentBlock(
                label="answer",
                text=item.answer,
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=item.masked,
                synthetic=item.synthetic,
            ),
        ]
        if item.earlier_answers:
            blocks.append(
                ContentBlock(
                    label="earlier_answers",
                    text="\n".join(f"- {a}" for a in item.earlier_answers),
                    trust_class=TrustClass.PROJECT_CONTENT,
                    masked=item.masked,
                    synthetic=item.synthetic,
                )
            )
        return self._gateway.generate(
            role=self.role,
            prompt_name=ASSESSMENT_PROMPT,
            params={
                "topic_id": item.topic_id,
                "topic_title": _slot(item.topic_title, 80),
                "topic_description": _slot(item.topic_description, 300),
                "expected_answer_shape": _slot(item.expected_answer_shape, 200),
            },
            content=blocks,
            schema=AnswerAssessment,
        )
