"""The offline completion providers. **None of them reaches a network.**

The one network provider, OpenAI (selected at P3 closure; architecture Y), is
in :mod:`reqpilot.llm.openai_provider`. The providers here are what the default
suite, CI and a keyless checkout run on:

* :class:`StubLLMGateway` - the P0 stub (name kept for compatibility). Returns a
  marked placeholder that no output contract accepts, so a stub run can only
  ever fail visibly, never produce a requirement.
* :class:`ScriptedProvider` - a deterministic test double that answers from a
  function. Used by the offline suite and the demonstration; it is not a model,
  says so on every response, and its output never counts towards a metric.
* :class:`RecordingLLMGateway` - record/replay at the gateway boundary (ADR-012,
  name kept from P0). Replay still exercises prompt assembly, the egress guards,
  schema parsing, repair and validation - everything except the provider.

A network provider is one class implementing ``complete`` plus a configuration
value; nothing else in the system changes (the OpenAI provider is exactly that).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reqpilot.config import Settings, get_settings
from reqpilot.domain.errors import FixtureMissingError
from reqpilot.llm.types import CompletionProvider, LLMRequest, LLMResponse


def estimate_tokens(text: str) -> int:
    """A rough, deterministic token count for providers that report none."""
    return max(1, len(text) // 4)


class StubLLMGateway:
    """The P0 offline stub, now a provider behind the gateway.

    Every response is marked ``is_stub=True`` so a stub answer cannot be mistaken
    for a real one in a run record.
    """

    provider_name = "stub"
    leaves_machine = False
    is_model = False

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


#: A scripted answer: text, or an exception to raise (to simulate failures).
ScriptedAnswer = str | Exception


class ScriptedProvider:
    """A deterministic stand-in for a model, driven by a function.

    ``responder`` receives the exact request the gateway assembled - so tests can
    assert what a provider would have seen - and returns the text to answer
    with, or an exception to raise. It is not a model: ``is_model`` is false and
    every response is marked ``is_stub=True``.
    """

    provider_name = "scripted"
    leaves_machine = False
    is_model = False

    def __init__(
        self,
        responder: Callable[[LLMRequest], ScriptedAnswer],
        *,
        model_id: str = "scripted",
    ) -> None:
        self._responder = responder
        self._model_id = model_id
        self.requests: list[LLMRequest] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        answer = self._responder(request)
        if isinstance(answer, Exception):
            raise answer
        prompt_text = request.instructions + "".join(request.untrusted_content.values())
        return LLMResponse(
            text=answer,
            model_id=self._model_id,
            provider=self.provider_name,
            prompt_template_id=request.prompt_template_id,
            tokens_in=estimate_tokens(prompt_text),
            tokens_out=estimate_tokens(answer),
            latency_ms=0,
            is_stub=True,
        )

    @classmethod
    def queue(
        cls, answers: list[ScriptedAnswer], *, model_id: str = "scripted"
    ) -> ScriptedProvider:
        """Answer with ``answers`` in order; a request beyond the end is an error."""
        remaining = list(answers)

        def respond(_request: LLMRequest) -> ScriptedAnswer:
            if not remaining:
                raise AssertionError("scripted provider has no answer left for this request")
            return remaining.pop(0)

        return cls(respond, model_id=model_id)


class RecordingLLMGateway:
    """Record/replay wrapper for deterministic, offline runs (ADR-012).

    Keyed on everything that determines a response, so a prompt change produces
    a new key: prompt drift shows up as a missing fixture rather than silently
    reusing a stale answer.

    Modes: ``replay`` answers from the fixture store and, on a miss, delegates to
    the wrapped provider - unless ``strict``, when a miss is an error (the right
    setting for tests that must never reach a provider); ``record`` delegates and
    writes the answer; ``live`` always delegates.
    """

    def __init__(
        self,
        inner: CompletionProvider,
        fixture_dir: Path,
        mode: str = "replay",
        *,
        strict: bool = False,
    ) -> None:
        if mode not in {"replay", "record", "live"}:
            raise ValueError(f"unknown fixture mode {mode!r}")
        self._inner = inner
        self._fixture_dir = fixture_dir
        self._mode = mode
        self._strict = strict

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def provider_name(self) -> str:
        return str(getattr(self._inner, "provider_name", "unknown"))

    @property
    def leaves_machine(self) -> bool:
        # Strict replay never delegates, so nothing can leave the machine.
        if self._mode == "replay" and self._strict:
            return False
        return bool(getattr(self._inner, "leaves_machine", True))

    @property
    def is_model(self) -> bool:
        return bool(getattr(self._inner, "is_model", True))

    def fixture_key(self, request: LLMRequest) -> str:
        """A stable key for a request, derived from everything that shapes the answer."""
        payload: dict[str, Any] = {
            "role": str(request.role),
            "template": request.prompt_template_id,
            "instructions": request.instructions,
            "content": request.untrusted_content,
            "schema": request.response_schema_name,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = self.fixture_key(request)
        path = self._fixture_dir / f"{key}.json"

        if self._mode == "replay":
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return LLMResponse(**data)
            if self._strict:
                raise FixtureMissingError(
                    f"no recorded response for request {key} ({request.prompt_template_id}); "
                    "strict replay never calls a provider"
                )

        response = self._inner.complete(request)

        if self._mode == "record":
            self._fixture_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(response.model_dump_json(indent=2), encoding="utf-8")

        return response
