"""The Classification role's contract (architecture E #5, F.4).

``category`` is a plain string on purpose. If it were an enum, a single unknown
label would make the whole output schema-invalid; as a string, each label is
normalised and checked on its own, so an unknown one is rejected and sent to a
human while the valid ones survive (``FR-CLS-001``, ``FR-CLS-002``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"


class ProposedLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=64)
    #: The model's heuristic review signal - not a probability (Phase 0 H.1).
    review_signal: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=1000)


class ClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    labels: list[ProposedLabel] = Field(max_length=26)
