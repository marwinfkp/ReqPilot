"""Role #3 - Requirement Extraction (architecture E #3). LLM-driven, propose-only.

The role turns numbered source segments into an :class:`ExtractionOutput`
*proposal* by way of the gateway. It reads no database, writes nothing, and has
no tool surface: its only capability is one gateway call (E.1: "Writes:
propose"; Retrieval: none).
"""

from __future__ import annotations

from collections.abc import Sequence

from reqpilot.agents.contracts.extraction import ExtractionOutput, SegmentView
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass
from reqpilot.rules.extraction import ExtractionRules

PROMPT_NAME = "requirement_extraction"


def render_segments(segments: Sequence[SegmentView]) -> str:
    """The numbered segments exactly as the model sees them.

    The segment text is reproduced unchanged, so a verbatim quote from it is a
    verbatim quote from the stored source.
    """
    lines: list[str] = []
    for segment in segments:
        speaker = f" {segment.speaker}:" if segment.speaker else ""
        # The question an interview answer replies to is context, not segment
        # text: it cannot be quoted as evidence (FR-EXT-007 cites stakeholders).
        context = f" (answering the question: {segment.context})" if segment.context else ""
        lines.append(f"[{segment.segment_id}]{speaker}{context}\n{segment.text}")
    return "\n\n".join(lines)


class RequirementExtractionRole:
    role = AgentRole.REQUIREMENT_EXTRACTION

    def __init__(self, gateway: LLMGateway, rules: ExtractionRules) -> None:
        self._gateway = gateway
        self._rules = rules

    def propose(
        self, segments: Sequence[SegmentView], *, domain: str
    ) -> StructuredResult[ExtractionOutput]:
        if not segments:
            raise ValueError("extraction needs at least one segment")
        block = ContentBlock(
            label="segments",
            text=render_segments(segments),
            trust_class=TrustClass.PROJECT_CONTENT,
            masked=all(s.masked for s in segments),
            synthetic=all(s.synthetic for s in segments),
        )
        return self._gateway.generate(
            role=self.role,
            prompt_name=PROMPT_NAME,
            params={"domain": domain, "statement_prefix": self._rules.statement_prefix},
            content=[block],
            schema=ExtractionOutput,
        )
