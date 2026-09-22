"""The P5 semantic roles: quality review (role #3 support) and conflict adjudication (#6).

Both are propose-only: one gateway call returns a typed proposal; deterministic
code validates it and decides what, if anything, is recorded.

* **Quality review** runs under role #3, Requirement Extraction: architecture
  C.3 names ``quality_analysis`` as "LLM + rules" supporting #3/#12, and #12
  Validation stays deterministic (E.0). The role sees a batch of statements
  under short keys and nothing else - no ids, no lifecycle, no other project.
* **Conflict adjudication** is role #6, Conflict Detection (hybrid, E #6): the
  model sees one shortlisted pair - both statements and whom each traces to -
  and nothing else.

Requirement text is untrusted project content, fenced as data. Text inside a
requirement that looks like an instruction is data too (tested).
"""

from __future__ import annotations

from collections.abc import Sequence

from reqpilot.agents.contracts.quality import (
    ConflictAdjudication,
    ConflictPairView,
    QualityReviewItem,
    QualityReviewOutput,
)
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

QUALITY_PROMPT = "requirement_quality_review"
CONFLICT_PROMPT = "conflict_adjudication"


class RequirementQualityRole:
    role = AgentRole.REQUIREMENT_EXTRACTION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(
        self,
        items: Sequence[QualityReviewItem],
        *,
        allowed_types: Sequence[str],
        masked: bool,
        synthetic: bool,
    ) -> StructuredResult[QualityReviewOutput]:
        text = "\n".join(f"[{item.key}] {' '.join(item.statement.split())}" for item in items)
        return self._gateway.generate(
            role=self.role,
            prompt_name=QUALITY_PROMPT,
            params={"allowed_types": ", ".join(sorted(allowed_types))},
            content=[
                ContentBlock(
                    label="requirements",
                    text=text,
                    trust_class=TrustClass.PROJECT_CONTENT,
                    masked=masked,
                    synthetic=synthetic,
                )
            ],
            schema=QualityReviewOutput,
        )


class ConflictDetectionRole:
    role = AgentRole.CONFLICT_DETECTION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(self, pair: ConflictPairView) -> StructuredResult[ConflictAdjudication]:
        def side(statement: str, stakeholder: str | None) -> str:
            who = f"\nStated by: {stakeholder}" if stakeholder else ""
            return " ".join(statement.split()) + who

        blocks = [
            ContentBlock(
                label="requirement_a",
                text=side(pair.statement_a, pair.stakeholder_a),
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=pair.masked,
                synthetic=pair.synthetic,
            ),
            ContentBlock(
                label="requirement_b",
                text=side(pair.statement_b, pair.stakeholder_b),
                trust_class=TrustClass.PROJECT_CONTENT,
                masked=pair.masked,
                synthetic=pair.synthetic,
            ),
        ]
        return self._gateway.generate(
            role=self.role,
            prompt_name=CONFLICT_PROMPT,
            params={"version_a_id": pair.version_a_id, "version_b_id": pair.version_b_id},
            content=blocks,
            schema=ConflictAdjudication,
        )
