"""Module M4 - the LLM gateway boundary (architecture ADR-006).

**P0 implements the abstraction and an offline stub. No network call exists in
this file, and no API key is required to install, migrate, run, or test.**

The gateway matters more than the provider. It is the single place where seven
cross-cutting obligations will be implemented exactly once, rather than eight
times across the LLM-using roles:

  1. prompt-template resolution and versioning
  2. sensitive-data masking before egress
  3. trust-class assembly (untrusted content never in the instruction position)
  4. schema-constrained decoding and repair
  5. token and cost accounting
  6. ``AgentRun`` audit emission
  7. fixture record/replay

Those are stubbed here as documented seams, not implemented, because each
belongs to the roadmap phase that first needs it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from reqpilot.config import LLMProvider, Settings, get_settings
from reqpilot.domain.enums import AgentRole


class LLMRequest(BaseModel):
    """A request to the gateway.

    ``instructions`` is the only trusted region. ``untrusted_content`` carries
    project content and retrieved material, which the gateway will place inside
    a delimited data block - never in the instruction position (architecture Q.1).
    """

    role: AgentRole
    prompt_template_id: str
    instructions: str
    untrusted_content: dict[str, str] = Field(default_factory=dict)
    response_schema_name: str | None = None
    max_tokens: int | None = None


class LLMResponse(BaseModel):
    """A gateway response.

    ``raw_text`` is untrusted model output. Nothing downstream may treat it as
    an instruction, and it must pass the validation pipeline before it can
    influence any persisted state (architecture F.2).
    """

    text: str
    model_id: str
    provider: str
    prompt_template_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    is_stub: bool = False


@runtime_checkable
class LLMGateway(Protocol):
    """The contract every provider implementation must satisfy.

    No component outside this package may import a provider SDK; they depend on
    this protocol instead.
    """

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a completion for ``request``."""
        ...


class StubLLMGateway:
    """An offline gateway that returns a deterministic placeholder.

    The P0 default. It exists so the whole system is runnable and testable with
    no credentials and no network, which is an explicit P0 requirement. It never
    pretends to be a model: every response is marked ``is_stub=True`` so a stub
    answer cannot be mistaken for a real one in a run record.
    """

    provider_name = "stub"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=(f"[stub response for role={request.role} template={request.prompt_template_id}]"),
            model_id="stub",
            provider=self.provider_name,
            prompt_template_id=request.prompt_template_id,
            is_stub=True,
        )


class RecordingLLMGateway:
    """Record/replay wrapper for deterministic, offline tests (ADR-012).

    Recording happens at the *gateway* boundary rather than at HTTP level, so a
    replayed fixture still exercises prompt assembly, masking, schema parsing,
    validation and audit emission - everything except the network, which is
    where the interesting bugs live.

    P0 implements ``replay`` against an on-disk fixture store and delegates
    misses to the wrapped gateway (the stub, by default). ``record`` mode is a
    documented seam: it is wired to the same store but is only meaningful once a
    real provider exists, which is not in P0.
    """

    def __init__(
        self,
        inner: LLMGateway,
        fixture_dir: Path,
        mode: str = "replay",
    ) -> None:
        if mode not in {"replay", "record", "live"}:
            raise ValueError(f"unknown fixture mode {mode!r}")
        self._inner = inner
        self._fixture_dir = fixture_dir
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    def fixture_key(self, request: LLMRequest) -> str:
        """A stable key for a request.

        Derived from the fields that determine the response, so a prompt change
        produces a new key - which makes prompt drift show up as a fixture diff
        rather than silently reusing a stale answer.
        """
        payload: dict[str, Any] = {
            "role": str(request.role),
            "template": request.prompt_template_id,
            "instructions": request.instructions,
            "content": request.untrusted_content,
            "schema": request.response_schema_name,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        import hashlib

        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = self.fixture_key(request)
        path = self._fixture_dir / f"{key}.json"

        if self._mode == "replay" and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse(**data)

        response = self._inner.complete(request)

        if self._mode == "record":
            self._fixture_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(response.model_dump_json(indent=2), encoding="utf-8")

        return response


def build_gateway(settings: Settings | None = None) -> LLMGateway:
    """Return the gateway for the configured provider.

    Only ``stub`` is constructible in P0. Selecting a network provider raises a
    clear, actionable error rather than failing later inside a graph run -
    because the provider and model tier decisions are still open, and P0
    deliberately implements no model invocation.
    """
    settings = settings or get_settings()

    if settings.llm_provider is LLMProvider.STUB:
        inner: LLMGateway = StubLLMGateway(settings)
    else:
        raise NotImplementedError(
            f"LLM provider {settings.llm_provider!r} is not implemented in P0. "
            "P0 establishes the gateway abstraction only; provider integration "
            "belongs to the roadmap phase that first needs model output. "
            "Use LLM_PROVIDER=stub."
        )

    if settings.llm_fixture_mode in {"replay", "record"}:
        return RecordingLLMGateway(
            inner, fixture_dir=settings.llm_fixture_dir, mode=settings.llm_fixture_mode
        )
    return inner
