"""Contract of role #9, Risk Analysis (architecture E #9, F.4; roadmap phase P7).

A model reads one requirement - or, for a project-level pass, a summary of the
requirement set - together with the classifications, compliance mappings, gaps
and security/privacy findings that earlier phases produced, and the evidence
retrieved for it. It *proposes* risks: a category, an ordinal likelihood and
impact each with a written rationale, mitigation considerations, and the
supplied evidence ids that ground the risk.

**The schema has no severity field** (``[DESIGN] D4``; architecture E #9: "The
model cannot set severity because there is nowhere to put it"). Severity is
computed by ``risk_compute_severity`` from the versioned 3x3 matrix (I.3).
Deliberately absent for the same reason, so a model has nowhere to put them: a
risk level or priority, a gate decision, a G8 status, an approval, a requirement
lifecycle state, a baseline, a status other than the one the engine assigns, a
project or a risk id. ``extra="forbid"`` makes an output that adds any such
field schema-invalid: one bounded repair, then a recorded failure with nothing
persisted.

``category`` is a plain string rather than an enum so that one unknown value -
``credit``, say - drops that proposal on its own and leaves the rest of the
output intact, with a reason code an audit can read. The ratings are also plain
strings: a malformed rating drops the proposal rather than being invented, which
is the opposite of I.7's upward normalisation and is deliberate. A rating is a
judgement, and there is no safe direction in which to guess one; a *finding*
exists either way and only its level is in question, but an unrated risk is not
a risk assessment at all.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"


def _as_text(value: Any, limit: int) -> Any:
    """Keep a non-string rating as its text, so validation drops it with a reason."""
    if value is None or isinstance(value, str):
        return value
    return str(value)[:limit]


class ProposedMitigationConsideration(BaseModel):
    """One mitigation consideration (``FR-RSK-005``).

    Whatever the model says, this is stored as a *suggestion requiring human
    validation*: ``is_ai_generated`` is set by the code that persists it, and
    there is no field here through which a model could mark its own suggestion
    accepted, approved or applied.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    suggestion: str = Field(min_length=1, max_length=600)


class ProposedRisk(BaseModel):
    """One proposed risk (architecture E #9 ``RiskProposal``). No severity field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: One of the six approved categories: business, technical, security,
    #: privacy, compliance, operational. Anything else drops this proposal.
    category: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1500)
    #: ``L1`` | ``L2`` | ``L3`` (architecture I.2). Not a probability.
    likelihood: str | None = Field(default=None, max_length=40)
    #: ``I1`` | ``I2`` | ``I3`` (architecture I.2).
    impact: str | None = Field(default=None, max_length=40)
    #: ``FR-RSK-003`` requires a written rationale for each rating; a rating
    #: without one is dropped rather than stored unexplained.
    likelihood_rationale: str = Field(default="", max_length=1200)
    impact_rationale: str = Field(default="", max_length=1200)
    #: Ids of evidence supplied in this request. Anything else is fabricated and
    #: is dropped by validation (``FR-RSK-006``).
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    mitigations: list[ProposedMitigationConsideration] = Field(default_factory=list, max_length=6)
    #: Heuristic review-prioritisation signal - not a probability, and never
    #: consulted by severity, gating or routing (approved Phase 0 H.1).
    review_signal: float = Field(ge=0.0, le=1.0)

    @field_validator("likelihood", "impact", mode="before")
    @classmethod
    def _keep_rating_as_text(cls, value: Any) -> Any:
        return _as_text(value, 40)


class RiskAnalysisOutput(BaseModel):
    """One risk-identification call's output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The requirement version this call was about, echoed exactly. A different
    #: id is a substitution and drops every risk in the output. Empty for the
    #: project-level pass, which is about the set rather than one version.
    requirement_version_id: str = Field(default="", max_length=36)
    risks: list[ProposedRisk] = Field(default_factory=list, max_length=12)
    #: What the model felt it could not assess. Informational; never persisted.
    analysis_note: str | None = Field(default=None, max_length=500)
