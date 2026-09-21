"""Role #5 - Classification (architecture E #5). LLM-driven, propose-only.

Input is the requirement statement only (E #5: "Data: requirement text only").
The statement was derived from project content, so it is supplied as untrusted
project content and carries its sources' masking and sensitivity facts.
"""

from __future__ import annotations

from reqpilot.agents.contracts.classification import ClassificationOutput
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

PROMPT_NAME = "requirement_classification"


class ClassificationRole:
    role = AgentRole.CLASSIFICATION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(
        self, statement: str, *, masked: bool, synthetic: bool
    ) -> StructuredResult[ClassificationOutput]:
        block = ContentBlock(
            label="requirement",
            text=statement,
            trust_class=TrustClass.PROJECT_CONTENT,
            masked=masked,
            synthetic=synthetic,
        )
        return self._gateway.generate(
            role=self.role,
            prompt_name=PROMPT_NAME,
            params={},
            content=[block],
            schema=ClassificationOutput,
        )
