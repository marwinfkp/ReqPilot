"""The P6 roles: #7 Compliance and #8 Security & Privacy (architecture E, E.1).

Both are propose-only and hybrid: one gateway call returns a typed proposal;
deterministic code validates it, computes everything authoritative (gaps, the
risk level, whether G2/G3 fires) and decides what is recorded.

What each role is shown, and nothing else (least privilege, E.1):

* the one requirement's statement - ``PROJECT_CONTENT``, fenced as untrusted;
* the evidence retrieved *for that requirement in this run* - ``RETRIEVED_KB``,
  fenced, each piece under its evidence id with its provenance header;
* (#7) the project's expected-control checklist - curated rule data, fenced as
  ``RETRIEVED_KB``, never an instruction.

Neither role retrieves: retrieval was done by the node, through the allowlist.
Neither sees another requirement, another project, a lifecycle state, a gate or
an approval. Text inside the requirement or the evidence that looks like an
instruction ("mark this compliant", "set risk to low") is data (Q.1, Q.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.compliance import ComplianceMappingOutput, SecurityPrivacyOutput
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import ContentBlock, StructuredResult, TrustClass

COMPLIANCE_PROMPT = "compliance_mapping"
SECURITY_PROMPT = "security_requirement_analysis"
PRIVACY_PROMPT = "privacy_requirement_analysis"

NO_EVIDENCE = "NO EVIDENCE WAS SUPPLIED FOR THIS REQUIREMENT."


@dataclass(frozen=True)
class EvidenceView:
    """One piece of supplied evidence as a role sees it: its id, provenance, quote."""

    evidence_id: str
    source_title: str
    source_type: str
    binding: str
    issuing_body: str
    jurisdiction: str
    source_version: str
    effective_date: str | None
    clause_ref: str | None
    quote: str

    def render(self) -> str:
        header = (
            f"[evidence_id={self.evidence_id}] {self.source_title} | type={self.source_type} "
            f"({self.binding}) | issuer={self.issuing_body} | jurisdiction={self.jurisdiction} "
            f"| version={self.source_version} | effective={self.effective_date or 'n/a'} "
            f"| clause={self.clause_ref or '-'}"
        )
        return f"{header}\n{' '.join(self.quote.split())}"


@dataclass(frozen=True)
class ControlView:
    key: str
    title: str
    obligation_kind: str
    indicated: bool

    def render(self) -> str:
        hint = "indicated" if self.indicated else "not indicated"
        return f"{self.key} | {self.title} | {self.obligation_kind} | {hint}"


@dataclass(frozen=True)
class RequirementView:
    version_id: str
    statement: str
    masked: bool
    synthetic: bool


def _requirement_block(view: RequirementView) -> ContentBlock:
    return ContentBlock(
        label="requirement",
        text=" ".join(view.statement.split()),
        trust_class=TrustClass.PROJECT_CONTENT,
        masked=view.masked,
        synthetic=view.synthetic,
    )


def _evidence_block(evidence: Sequence[EvidenceView]) -> ContentBlock:
    text = "\n\n".join(e.render() for e in evidence) if evidence else NO_EVIDENCE
    return ContentBlock(label="evidence", text=text, trust_class=TrustClass.RETRIEVED_KB)


class ComplianceRole:
    """Role #7: candidate mappings, grounded only in the evidence supplied."""

    role = AgentRole.COMPLIANCE

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(
        self,
        requirement: RequirementView,
        *,
        controls: Sequence[ControlView],
        evidence: Sequence[EvidenceView],
        jurisdictions: Sequence[str],
    ) -> StructuredResult[ComplianceMappingOutput]:
        blocks = [
            _requirement_block(requirement),
            ContentBlock(
                label="control_checklist",
                text="\n".join(c.render() for c in controls),
                trust_class=TrustClass.RETRIEVED_KB,
            ),
            _evidence_block(evidence),
        ]
        return self._gateway.generate(
            role=self.role,
            prompt_name=COMPLIANCE_PROMPT,
            params={
                "requirement_version_id": requirement.version_id,
                "control_keys": ", ".join(c.key for c in controls),
                "jurisdictions": ", ".join(sorted(set(jurisdictions))),
            },
            content=blocks,
            schema=ComplianceMappingOutput,
        )


class SecurityPrivacyRole:
    """Role #8: derived security or privacy requirements, with a *proposed* level only."""

    role = AgentRole.SECURITY_PRIVACY

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def propose(
        self,
        requirement: RequirementView,
        *,
        category: str,
        families: Sequence[str],
        indicated: Sequence[str],
        evidence: Sequence[EvidenceView],
    ) -> StructuredResult[SecurityPrivacyOutput]:
        prompt = SECURITY_PROMPT if category == "security" else PRIVACY_PROMPT
        return self._gateway.generate(
            role=self.role,
            prompt_name=prompt,
            params={
                "requirement_version_id": requirement.version_id,
                "families": ", ".join(families),
                "indicated": ", ".join(indicated) if indicated else "none",
            },
            content=[_requirement_block(requirement), _evidence_block(evidence)],
            schema=SecurityPrivacyOutput,
        )
