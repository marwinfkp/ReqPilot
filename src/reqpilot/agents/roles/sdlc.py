"""Role #10, SDLC Selection (architecture E #10, E.1, L; roadmap phase P9).

Hybrid and advisory. Two gateway calls, each returning a typed value that
deterministic code judges:

* :meth:`SdlcSelectionRole.propose_factors` - bounded, evidence-cited
  refinements of the derived factor profile (``FactorProposalOutput``);
* :meth:`SdlcSelectionRole.explain` - the explanation of a ranking that already
  exists (``ExplanationDraft``), whose assertions are compared with the
  persisted ranking and never used in its place.

What the role is shown, and nothing else (least privilege, E.1): the derived
profile - each factor's score, source, the formula and numbers behind it, and
its typed evidence references - and, for the explanation, the computed ranking,
the rule effects and the reversal conditions. **No requirement text**, no
stakeholder name, no approval, no gate, no other project. All of it is fenced
``PROJECT_CONTENT`` carrying the masking and synthetic facts of the requirement
versions it was derived from, so the gateway's egress guard applies exactly as
it does to the requirements themselves (``FR-ING-003``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.sdlc import ExplanationDraft, FactorProposalOutput
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

FACTOR_PROPOSAL_PROMPT = "sdlc_factor_proposal"
EXPLANATION_PROMPT = "sdlc_explanation"


@dataclass(frozen=True)
class FactorView:
    """One factor as role #10 sees it."""

    factor: str
    score: int
    source: str
    weight: float
    rationale: str
    evidence_refs: tuple[str, ...]
    evidence_state: str
    derived_score: int | None = None

    def render(self) -> str:
        derived = (
            f" (derived {self.derived_score})"
            if self.derived_score is not None and self.derived_score != self.score
            else ""
        )
        refs = ", ".join(self.evidence_refs) if self.evidence_refs else "none"
        return (
            f"[factor:{self.factor}] score={self.score}{derived} | source={self.source} | "
            f"weight={self.weight:g} | evidence={self.evidence_state}\n"
            f"  basis: {' '.join(self.rationale.split())}\n"
            f"  refs: {refs}"
        )


@dataclass(frozen=True)
class CandidateView:
    key: str
    label: str
    rank: int
    score: float
    mcda_score: float
    rule_effects: tuple[str, ...] = ()

    def render(self) -> str:
        effects = f" | rules: {', '.join(self.rule_effects)}" if self.rule_effects else ""
        return (
            f"{self.rank}. {self.key} ({self.label}) | score={self.score:g} | "
            f"mcda={self.mcda_score:g}{effects}"
        )


@dataclass(frozen=True)
class ProfileView:
    """The factor profile, with the facts the egress guard needs."""

    factors: Sequence[FactorView]
    masked: bool
    synthetic: bool

    def block(self) -> ContentBlock:
        return ContentBlock(
            label="factor_profile",
            text="\n".join(f.render() for f in self.factors),
            trust_class=TrustClass.PROJECT_CONTENT,
            masked=self.masked,
            synthetic=self.synthetic,
        )

    def refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r for f in self.factors for r in f.evidence_refs))


@dataclass(frozen=True)
class RankingView:
    candidates: Sequence[CandidateView]
    rules: Sequence[str]
    reversals: Sequence[str]

    def render(self) -> str:
        lines = ["RANKING (computed)"]
        lines.extend(c.render() for c in self.candidates)
        lines.append("RULES APPLIED")
        lines.extend(self.rules or ["none triggered"])
        lines.append("REVERSAL CONDITIONS (single-factor changes that put the runner-up first)")
        lines.extend(self.reversals or ["none within the 1-5 scale"])
        return "\n".join(lines)


class SdlcSelectionRole:
    """Role #10: proposals and an explanation. **No ranking or selection field exists.**"""

    role = AgentRole.SDLC_SELECTION

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose_factors(
        self, profile: ProfileView, *, proposable: Sequence[str], max_deviation: int
    ) -> StructuredResult[FactorProposalOutput]:
        return self._gateway.generate(
            role=self.role,
            prompt_name=FACTOR_PROPOSAL_PROMPT,
            params={"proposable": ", ".join(proposable), "max_deviation": str(max_deviation)},
            content=[profile.block()],
            schema=FactorProposalOutput,
        )

    def explain(
        self, profile: ProfileView, ranking: RankingView, *, top: str, runner_up: str
    ) -> StructuredResult[ExplanationDraft]:
        return self._gateway.generate(
            role=self.role,
            prompt_name=EXPLANATION_PROMPT,
            params={"top_candidate": top, "runner_up": runner_up},
            content=[
                ContentBlock(
                    label="ranking",
                    text=ranking.render(),
                    trust_class=TrustClass.PROJECT_CONTENT,
                    masked=profile.masked,
                    synthetic=profile.synthetic,
                ),
                profile.block(),
            ],
            schema=ExplanationDraft,
        )
