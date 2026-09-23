"""Contracts of the P6 roles: #7 Compliance and #8 Security & Privacy (architecture E, F.4).

**Compliance mapping** (C.3 node 13, role #7). A model reads one requirement,
the expected-control checklist and the evidence retrieved for it, and
*proposes* candidate mappings: which control, how the requirement relates to it,
which supplied evidence ids support that, and whether the interpretation is
high-impact. It may state which jurisdiction and source type it believes it is
citing; those claims are checked against the cited evidence and never stored in
place of it.

**Security / privacy derivation** (C.3 node 16, role #8). A model reads one
requirement and the same evidence and proposes derived security or privacy
requirements, each with a ``proposed_risk_level`` - a suggestion only.

Deliberately absent from both, so that a model has **nowhere to put them**
(``[DESIGN] D4``; F.4): an authoritative ``risk_level``, a severity, a lifecycle
state, an approval, a G2/G3 decision, baseline membership, a gate status, a
project. ``extra="forbid"`` makes an output that adds any such field
schema-invalid: one repair, then a recorded failure with nothing persisted.

Plain strings where an enum might be expected (``control_key``, ``family``,
``source_type``): an unknown value is then refused by validation on its own, and
the other items in the same output survive. ``proposed_risk_level`` accepts
anything the model emits and keeps it as text, so a malformed value is
*normalised* to ``medium`` by the deterministic evaluator (I.7 rule 1) instead of
failing the whole output.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"

Relationship = Literal["addresses", "partially_addresses", "relevant_context"]
Category = Literal["security", "privacy"]


class ProposedComplianceMapping(BaseModel):
    """One candidate mapping (architecture E #7 ``ComplianceMappingProposal``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A key from the supplied checklist, e.g. ``LO-RET-APPLICATION-RECORDS``.
    control_key: str = Field(min_length=1, max_length=64)
    relationship: Relationship
    #: Ids of evidence supplied in this request. Anything else is fabricated.
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    #: The model's claim about the cited source - checked, never stored as fact.
    jurisdiction: str = Field(min_length=1, max_length=20)
    source_type: str = Field(min_length=1, max_length=40)
    #: The model's view that interpreting this is high-impact. Can only add a
    #: G2 gate; the checklist and the source type decide it independently.
    is_high_impact_interpretation: bool
    rationale: str = Field(min_length=1, max_length=1500)
    #: Hedged candidate compliance text ("potentially applicable", "candidate
    #: mapping"...). Checked for prohibited assertions (FR-CMP-006).
    candidate_text: str | None = Field(default=None, max_length=1000)
    #: An implied approval/audit checkpoint or retention/reporting obligation.
    implied_obligation: str | None = Field(default=None, max_length=500)
    #: Heuristic review-prioritisation signal - not a probability (Phase 0 H.1).
    review_signal: float = Field(ge=0.0, le=1.0)


class ComplianceMappingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Echoed exactly; a different id is a substitution and drops every mapping.
    requirement_version_id: str = Field(min_length=36, max_length=36)
    mappings: list[ProposedComplianceMapping] = Field(default_factory=list, max_length=20)
    #: What the evidence did not cover. Informational; never persisted.
    evidence_note: str | None = Field(default=None, max_length=500)


class ProposedSecurityPrivacyRequirement(BaseModel):
    """One derived requirement (architecture E #8 ``SecurityPrivacyProposal``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A catalogue family of the requested category, e.g. ``authentication``.
    family: str = Field(min_length=1, max_length=40)
    proposed_requirement: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    #: Ids of supplied evidence, if any supports it. May be empty: then the
    #: finding says evidence is unavailable.
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    #: **A suggestion only.** Normalised deterministically; the authoritative
    #: level is ``max(normalised, catalogue floor)`` (I.7). There is no field for it.
    proposed_risk_level: str | None = Field(default=None, max_length=50)
    risk_rationale: str | None = Field(default=None, max_length=1000)
    review_signal: float = Field(ge=0.0, le=1.0)

    @field_validator("proposed_risk_level", mode="before")
    @classmethod
    def _keep_as_text(cls, value: Any) -> Any:
        """Anything but a string or null is kept as its JSON text (then normalised to medium)."""
        if value is None or isinstance(value, str):
            return value
        try:
            return json.dumps(value, sort_keys=True)[:50]
        except (TypeError, ValueError):
            return str(value)[:50]


class SecurityPrivacyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_version_id: str = Field(min_length=36, max_length=36)
    category: Category
    findings: list[ProposedSecurityPrivacyRequirement] = Field(default_factory=list, max_length=12)
