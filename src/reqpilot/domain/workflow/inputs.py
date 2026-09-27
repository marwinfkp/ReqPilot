"""What a workflow is derived from: the project's approved records, as plain values.

The service loads these from persisted rows with the same eligibility P9 used for
the recommendation the workflow realises (``services/sdlc/evidence.py``): the
baseline's requirement versions, the mappings of those versions that validation
accepted or G2 approved, the risks of those versions plus the project-level risks
in a status that needs no further human decision, the derived security/privacy
requirements of those versions that are not rejected, and the latest compliance
run's gaps. Nothing here is a model output, and nothing is invented to fill a gap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MappingInput:
    """An eligible compliance mapping of an in-scope requirement version."""

    id: str
    control_key: str
    control_title: str
    obligation_kind: str
    relationship: str
    status: str
    requirement_label: str
    evidence_count: int
    content_hash: str


@dataclass(frozen=True)
class MitigationInput:
    id: str
    suggestion: str
    is_ai_generated: bool
    status: str

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def awaits_validation(self) -> bool:
        """An AI suggestion no human has accepted yet (``FR-RSK-005``)."""
        return self.is_ai_generated and self.status == "suggested"


@dataclass(frozen=True)
class RiskInput:
    """An eligible risk of the register (requirement-level in scope, or project-level)."""

    id: str
    title: str
    category: str
    severity: str
    status: str
    subject_label: str
    content_hash: str
    mitigations: tuple[MitigationInput, ...] = ()

    @property
    def high(self) -> bool:
        return self.severity == "high"

    def live_mitigations(self) -> tuple[MitigationInput, ...]:
        """The mitigations the register still carries (a human-rejected one is not)."""
        return tuple(m for m in self.mitigations if not m.rejected)


@dataclass(frozen=True)
class SecurityFindingInput:
    """A derived security/privacy requirement (P6) of an in-scope version, not rejected."""

    id: str
    family: str
    category: str
    risk_level: str
    status: str
    requirement_label: str
    content_hash: str


@dataclass(frozen=True)
class GapInput:
    """A compliance gap of the project's latest compliance run (P6)."""

    id: str
    control_key: str
    control_title: str
    obligation_kind: str


@dataclass(frozen=True)
class WorkflowInputs:
    project_id: str
    sdlc_run_id: str
    candidate_id: str
    candidate_key: str
    candidate_label: str
    #: The SDLC models named by the P9 candidate's attributes (the hybrid's parts).
    candidate_models: tuple[str, ...]
    baseline_id: str
    baseline_label: str
    mappings: tuple[MappingInput, ...] = ()
    risks: tuple[RiskInput, ...] = ()
    findings: tuple[SecurityFindingInput, ...] = ()
    gaps: tuple[GapInput, ...] = ()

    def high_risks(self) -> tuple[RiskInput, ...]:
        return tuple(r for r in self.risks if r.high)
