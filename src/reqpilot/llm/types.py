"""Gateway types: requests, responses, content trust classes, model metadata.

``LLMRequest`` and ``LLMResponse`` are the P0 completion contract and keep their
P0 fields. The provider sees only an ``LLMRequest`` whose ``instructions`` were
assembled by the gateway from a registered template, and whose
``untrusted_content`` is already fenced as data (architecture Q.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from reqpilot.domain.enums import AgentRole
from reqpilot.domain.provenance import ModelMeta, params_hash

__all__ = [
    "DATA_CLASSES",
    "CompletionProvider",
    "ContentBlock",
    "GatewayErrorCode",
    "LLMRequest",
    "LLMResponse",
    "ModelMeta",
    "StructuredResult",
    "TrustClass",
    "params_hash",
]


class LLMRequest(BaseModel):
    """A completion request as a provider receives it.

    ``instructions`` is the only trusted region. ``untrusted_content`` carries
    project content, retrieved material and earlier model output, each already
    fenced as data by the gateway - never placed in the instruction position
    (architecture Q.1).
    """

    role: AgentRole
    prompt_template_id: str
    instructions: str
    untrusted_content: dict[str, str] = Field(default_factory=dict)
    response_schema_name: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    model_id: str | None = None


class LLMResponse(BaseModel):
    """A provider's response.

    ``text`` is untrusted model output. Nothing downstream may treat it as an
    instruction, and it must pass validation before it can influence any
    persisted state (architecture F.2).
    """

    text: str
    model_id: str
    provider: str
    prompt_template_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    #: True for anything that is not a model: the P0 stub, scripted responses.
    is_stub: bool = False
    #: The provider's own identifier for this response, where it gives one.
    response_id: str | None = None


@runtime_checkable
class CompletionProvider(Protocol):
    """What the gateway needs from a provider: one completion call.

    Providers may also declare, as attributes:

    * ``provider_name`` - recorded on every run;
    * ``leaves_machine`` - whether content is sent off this machine. Assumed
      **true** when not declared, so an unknown provider is treated as external;
    * ``is_model`` - whether responses come from a model at all. The stub and
      scripted providers say no, and their output never counts towards a metric.
    """

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a completion for ``request``."""
        ...


class TrustClass(StrEnum):
    """The five content trust classes (architecture Q.1, ``[DESIGN] D14``)."""

    #: Registered prompt templates. The only class that may instruct.
    SYSTEM = "system"
    #: Validated task parameters (e.g. the identifier domain). Slots only.
    OPERATOR = "operator"
    #: Transcripts, documents, stakeholder answers. Never instructions.
    PROJECT_CONTENT = "project_content"
    #: Curated knowledge chunks. Data, never instructions.
    RETRIEVED_KB = "retrieved_kb"
    #: Any earlier model response. Re-validated before reuse.
    MODEL_OUTPUT = "model_output"


#: Classes that may only ever appear inside a fenced data block.
DATA_CLASSES: frozenset[TrustClass] = frozenset(
    {TrustClass.PROJECT_CONTENT, TrustClass.RETRIEVED_KB, TrustClass.MODEL_OUTPUT}
)


@dataclass(frozen=True)
class ContentBlock:
    """One piece of untrusted data for a prompt.

    ``masked`` and ``synthetic`` are facts about where the text came from - the
    source document's masking status and declared sensitivity - and are what
    the gateway's egress rule reads.
    """

    label: str
    text: str
    trust_class: TrustClass
    masked: bool = False
    synthetic: bool = False

    def __post_init__(self) -> None:
        if self.trust_class not in DATA_CLASSES:
            raise ValueError(
                f"{self.trust_class} content cannot be supplied as a data block; instructions "
                "come only from registered templates and validated parameters (Q.1)"
            )
        if not self.label or not self.label.replace("_", "").isalnum():
            raise ValueError("a content block label is a short alphanumeric name")


class GatewayErrorCode(StrEnum):
    """Why a structured call produced no value. Stable, safe to store."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_OUTPUT = "malformed_output"
    FIXTURE_MISSING = "fixture_missing"


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    """A schema-valid value, or the reason there is none. Never both.

    Only *type* validity is established here. Whether the value is acceptable
    - its sources resolve, its labels exist - is for deterministic validation.
    """

    meta: ModelMeta
    value: T | None = None
    error_code: GatewayErrorCode | None = None
    #: A system-generated description of the failure (never model text).
    error_message: str | None = None
    #: sha256 of the accepted raw output, for the run record.
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error_code is None):
            raise ValueError("a structured result has exactly one of a value or an error")

    @property
    def ok(self) -> bool:
        return self.value is not None
