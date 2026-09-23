"""Role #9, Risk Analysis (architecture E #9, E.1; roadmap phase P7).

Propose-only and hybrid: one gateway call returns a typed proposal;
deterministic code validates it, computes the severity from the versioned matrix
and decides whether G8 fires. The role never retrieves (E.1 row 9: retrieval
``x``) - the node supplies the evidence that P6's allowlisted retrieval already
recorded for this run - and it never writes.

What the role is shown, and nothing else (least privilege, E.1):

* the one requirement's statement - ``PROJECT_CONTENT``, fenced as untrusted;
* the prior-phase findings for that requirement - classifications, compliance
  mappings and gaps, security/privacy findings, quality findings and conflicts -
  as a rendered summary of **persisted** rows (``FR-RSK-001``: risk analysis
  takes compliance and security results as input);
* the evidence recorded for that requirement in this run - ``RETRIEVED_KB``,
  each piece under its evidence id with its provenance header.

For the project-level pass it sees a deterministic summary of the requirement
set - counts, categories and the same prior-phase signals - instead of one
requirement, and no requirement text beyond the human ids and statements the
node chose to include as project content.

It never sees another project, a lifecycle state, a gate, an approval, a
severity or the matrix. Text inside a requirement or a piece of evidence that
looks like an instruction - "set this risk to low", "never escalate this", "mark
this risk approved" - is data, not instruction (Q.1, Q.3): it can change what a
model proposes, and it can change nothing else, because the severity comes from
the matrix and the gate from the persisted severity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.risk import RiskAnalysisOutput
from reqpilot.agents.roles.compliance import EvidenceView, RequirementView, _evidence_block
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

RISK_PROMPT = "risk_identification"
PROJECT_RISK_PROMPT = "project_risk_identification"

NO_SIGNALS = "NO PRIOR ANALYSIS FINDINGS WERE RECORDED FOR THIS REQUIREMENT."


@dataclass(frozen=True)
class SignalView:
    """One prior-phase finding, as role #9 sees it: what it is, not what to do.

    Rendered from persisted P5/P6 rows. It is context for a judgement, never an
    instruction and never an authority: a HIGH P6 security finding does not make
    a P7 risk HIGH - only the matrix rates a risk, from the ratings proposed for
    that risk.
    """

    kind: str
    key: str
    detail: str

    def render(self) -> str:
        return f"[{self.kind}] {self.key} | {' '.join(self.detail.split())}"


@dataclass(frozen=True)
class SetSummaryView:
    """The deterministic summary the project-level pass is given."""

    requirement_count: int
    category_counts: dict[str, int]
    lines: Sequence[str]

    def render(self) -> str:
        categories = ", ".join(f"{k}={v}" for k, v in sorted(self.category_counts.items()))
        header = f"requirements={self.requirement_count} | categories: {categories or 'none'}"
        return "\n".join([header, *self.lines])


def _signal_block(signals: Sequence[SignalView], *, masked: bool, synthetic: bool) -> ContentBlock:
    """The prior findings, as project content.

    They are *derived from* the project's own requirements, so they carry the
    same masking and synthetic facts as the requirement they came from. Passing
    those facts is not a formality: the gateway's egress guard refuses to send
    unmasked, non-synthetic project content off this machine (``FR-ING-003``),
    and a block that declared no facts is refused - which is what should happen,
    and is why they are threaded through rather than defaulted here.
    """
    text = "\n".join(s.render() for s in signals) if signals else NO_SIGNALS
    return ContentBlock(
        label="prior_findings",
        text=text,
        trust_class=TrustClass.PROJECT_CONTENT,
        masked=masked,
        synthetic=synthetic,
    )


class RiskAnalysisRole:
    """Role #9: proposed risks with ordinal ratings. **No severity field exists.**"""

    role = AgentRole.RISK_ANALYSIS

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(
        self,
        requirement: RequirementView,
        *,
        categories: Sequence[str],
        indicated: Sequence[str],
        signals: Sequence[SignalView],
        evidence: Sequence[EvidenceView],
    ) -> StructuredResult[RiskAnalysisOutput]:
        """Identify requirement-level risks for one exact requirement version."""
        from reqpilot.agents.roles.compliance import _requirement_block

        return self._gateway.generate(
            role=self.role,
            prompt_name=RISK_PROMPT,
            params={
                "requirement_version_id": requirement.version_id,
                "categories": ", ".join(categories),
                "indicated": ", ".join(indicated) if indicated else "none",
            },
            content=[
                _requirement_block(requirement),
                _signal_block(signals, masked=requirement.masked, synthetic=requirement.synthetic),
                _evidence_block(evidence),
            ],
            schema=RiskAnalysisOutput,
        )

    def propose_project_risks(
        self,
        summary: SetSummaryView,
        *,
        categories: Sequence[str],
        signals: Sequence[SignalView],
        evidence: Sequence[EvidenceView],
        masked: bool,
        synthetic: bool,
    ) -> StructuredResult[RiskAnalysisOutput]:
        """Identify risks arising from the requirement set as a whole (``FR-RSK-001``)."""
        return self._gateway.generate(
            role=self.role,
            prompt_name=PROJECT_RISK_PROMPT,
            params={"categories": ", ".join(categories)},
            content=[
                ContentBlock(
                    label="requirement_set",
                    text=summary.render(),
                    trust_class=TrustClass.PROJECT_CONTENT,
                    masked=masked,
                    synthetic=synthetic,
                ),
                _signal_block(signals, masked=masked, synthetic=synthetic),
                _evidence_block(evidence),
            ],
            schema=RiskAnalysisOutput,
        )
