"""Role #4 - Clarification (architecture E #4). LLM-driven, propose-only.

One gateway call per question. The role sees the one requirement, its one
defect and the questions already asked about it (least privilege). It proposes
a question; deterministic code binds it, checks it, stores it and routes the
answer.
"""

from __future__ import annotations

from reqpilot.agents.contracts.clarification import ClarificationInput, ClarificationProposal
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

PROMPT_NAME = "clarification_question"


class ClarificationRole:
    role = AgentRole.CLARIFICATION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(self, item: ClarificationInput) -> StructuredResult[ClarificationProposal]:
        requirement = f"{item.requirement_ref}: {item.statement}"
        if item.original_wording:
            requirement += f"\nStakeholder's original words: {item.original_wording}"
        defect = f"Defect ({item.defect_type}, {item.severity}): {item.defect_rationale}"
        if item.defect_span:
            defect += f"\nAbout these words of the requirement: {item.defect_span}"
        blocks = [
            ContentBlock(
                label="requirement",
                text=requirement,
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=item.masked,
                synthetic=item.synthetic,
            ),
            ContentBlock(
                label="defect",
                text=defect,
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=item.masked,
                synthetic=item.synthetic,
            ),
        ]
        if item.prior_questions:
            blocks.append(
                ContentBlock(
                    label="prior_questions",
                    text="\n".join(f"- {q}" for q in item.prior_questions),
                    trust_class=TrustClass.MODEL_OUTPUT,
                )
            )
        return self._gateway.generate(
            role=self.role,
            prompt_name=PROMPT_NAME,
            params={
                "requirement_ref": item.requirement_ref,
                "defect_id": item.defect_id,
                "defect_type": item.defect_type,
                "severity": item.severity,
            },
            content=blocks,
            schema=ClarificationProposal,
        )
