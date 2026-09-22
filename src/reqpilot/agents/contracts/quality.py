"""Contracts of the P5 semantic checks: quality review and conflict adjudication.

**Quality review** (C.3 node 6, "LLM + rules", role #3 support). A model reads a
batch of requirement statements, each under a short key (``R1``...), and
*proposes* findings: which statement, what kind of defect, the words of the
statement it is about, and why. It cannot set a severity (it only proposes
one), a lifecycle state, an approval, or anything about any other requirement.

**Conflict adjudication** (C.3 node 8, role #6). A model reads one shortlisted
pair and proposes a verdict - definite conflict, potential conflict, compatible
under conditions, duplicate, no conflict, or not enough information - with the
words of each statement that disagree. It must echo both version ids exactly;
deterministic validation rejects a substituted id.

Neither contract has a field for approval, lifecycle, baseline, risk, a
regulatory mapping or a resolution: they are not the model's to set
(architecture E.1), and ``extra="forbid"`` makes an output that adds one
schema-invalid.

``finding_type`` is a plain string, as the classification label is (P3): an
unknown type is then rejected on its own by validation, and the other findings
in the same output survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"

ProposedSeverity = Literal["low", "medium", "high"]
Verdict = Literal[
    "definite_conflict",
    "potential_conflict",
    "conditional_compatible",
    "duplicate",
    "no_conflict",
    "insufficient_information",
]
Kind = Literal["numeric", "timing", "actor_scope", "logical", "security", "behavioural", "other"]


# --- quality review -----------------------------------------------------------------


@dataclass(frozen=True)
class QualityReviewItem:
    """One statement as the reviewing role sees it: a key and the text, nothing more."""

    key: str
    statement: str


class ProposedQualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The key the statement was shown under, e.g. ``R3``.
    requirement_key: str = Field(pattern=r"^R[0-9]{1,3}$")
    finding_type: str = Field(min_length=1, max_length=40)
    #: Exact words of that statement the finding is about ("" only for a
    #: finding about something missing, which must say what is missing).
    evidence: str = Field(default="", max_length=500)
    explanation: str = Field(min_length=1, max_length=1000)
    #: For incompleteness: what information is missing. Never a proposed value.
    missing: str | None = Field(default=None, max_length=300)
    proposed_severity: ProposedSeverity
    #: Heuristic review-prioritisation signal - not a probability (Phase 0 H.1).
    review_signal: float = Field(ge=0.0, le=1.0)


class QualityReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: list[ProposedQualityFinding] = Field(default_factory=list, max_length=100)


# --- conflict adjudication ------------------------------------------------------------


@dataclass(frozen=True)
class ConflictPairView:
    """One shortlisted pair as the adjudicating role sees it (architecture E #6 input)."""

    version_a_id: str
    version_b_id: str
    statement_a: str
    statement_b: str
    stakeholder_a: str | None
    stakeholder_b: str | None
    masked: bool
    synthetic: bool


class ConflictAdjudication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Echoed exactly; a different id is a substitution and is rejected.
    requirement_version_id_a: str = Field(min_length=36, max_length=36)
    requirement_version_id_b: str = Field(min_length=36, max_length=36)
    verdict: Verdict
    conflict_kind: Kind
    explanation: str = Field(min_length=1, max_length=1500)
    #: Exact words of statement A, and of statement B, that disagree.
    evidence_a: str = Field(default="", max_length=500)
    evidence_b: str = Field(default="", max_length=500)
    #: For a conditional verdict: the condition that reconciles the two.
    conditions: str | None = Field(default=None, max_length=500)
    proposed_severity: ProposedSeverity
    review_signal: float = Field(ge=0.0, le=1.0)
