"""The OpenAI completion provider: the one network provider behind the gateway.

The team selected OpenAI at P3 closure (architecture Y; ADR-006). This module is
the only place in ReqPilot that imports the OpenAI SDK, and it imports it
lazily: the application, the offline suite and CI never load the SDK unless
``LLM_PROVIDER=openai`` is configured. The model is whatever
``LLM_MODEL_DEFAULT`` names; nothing here fixes one.

The adapter translates, and nothing more:

* **The trust boundary is kept.** An :class:`LLMRequest` becomes one Responses
  API call. The gateway-assembled ``instructions`` go in the instruction
  position. Every untrusted block, already fenced by the gateway, goes unchanged
  as a separate text part of a single *user* message. Project content never
  reaches the instruction position (architecture Q.1).
* **It adds no prompt text of its own.** The JSON output contract is already in
  the gateway-assembled instructions (a versioned template plus the schema).
  Schema validation, the one repair, and every deterministic check after it stay
  in the gateway, identical for every provider (C.8, F.2). The API's JSON mode
  is *not* used: it requires the word "json" in the user input. Here that input
  carries only fenced data, and writing words into it here would place
  unversioned instructions in the data region.
* **It retries nothing.** The SDK's own retries are switched off. Transient
  failures are raised as :class:`TransientProviderError`, and the gateway's
  bounded policy (``LLM_MAX_RETRIES``) is the only one.
* **It asks the provider to keep nothing** (``store=False``): a response is not
  stored on the provider's side for later retrieval.
* **It never repeats a provider's error message**, which can echo part of the
  API key. The errors raised here are written by this module from the HTTP
  status, the error type and code, and the request id. They are raised outside
  the SDK exception's context, so the original is not chained to them.

What leaves the machine is decided before this module is reached: the gateway's
egress guards refuse secrets, and refuse unmasked project content that is not
declared synthetic (``FR-ING-003``; masking itself is P11).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from reqpilot.config import Settings
from reqpilot.domain.errors import ProviderUnavailableError, TransientProviderError
from reqpilot.llm.types import LLMRequest, LLMResponse

__all__ = [
    "INSTALL_HINT",
    "OpenAIProvider",
    "ResponsesClient",
    "new_client",
    "translate_error",
]

#: How to install the SDK, which is an optional extra.
INSTALL_HINT = 'pip install -e ".[openai]"'

#: HTTP statuses that are worth retrying: the same set the SDK itself would retry.
_TRANSIENT_STATUSES = frozenset({408, 409, 429})

#: Identifiers from an error body are echoed only if they look like identifiers.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:\-]{1,80}$")


class ResponsesClient(Protocol):
    """The part of the OpenAI client this provider uses. Tests substitute a fake."""

    @property
    def responses(self) -> Any: ...


def new_client(settings: Settings) -> ResponsesClient:
    """An OpenAI client configured from settings, with the SDK's own retries off."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ProviderUnavailableError(
            f"LLM_PROVIDER=openai needs the OpenAI SDK, which is not installed: {INSTALL_HINT}"
        ) from None
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=float(settings.llm_timeout_seconds),
        max_retries=0,
    )


class OpenAIProvider:
    """A :class:`~reqpilot.llm.types.CompletionProvider` for the OpenAI Responses API."""

    provider_name = "openai"
    #: Content sent here leaves the machine, so the gateway's egress rule applies in full.
    leaves_machine = True
    is_model = True

    def __init__(
        self,
        *,
        model: str,
        client: ResponsesClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not model or not model.strip():
            raise ValueError("the OpenAI provider needs a model: set LLM_MODEL_DEFAULT")
        self._model = model.strip()
        self._client = client
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIProvider:
        if not settings.llm_api_key:
            raise ProviderUnavailableError("LLM_PROVIDER=openai needs LLM_API_KEY")
        if not settings.llm_model_default:
            raise ProviderUnavailableError("LLM_PROVIDER=openai needs LLM_MODEL_DEFAULT")
        return cls(model=settings.llm_model_default, client=new_client(settings))

    @property
    def model_id(self) -> str:
        return self._model

    def __repr__(self) -> str:
        # Never the client: it holds the API key.
        return f"OpenAIProvider(model={self._model!r})"

    def build_arguments(self, request: LLMRequest) -> dict[str, Any]:
        """The Responses API arguments for ``request``, exactly as they will be sent."""
        arguments: dict[str, Any] = {
            "model": request.model_id or self._model,
            "instructions": request.instructions,
            "store": False,
        }
        parts = [
            {"type": "input_text", "text": text} for text in request.untrusted_content.values()
        ]
        if parts:
            arguments["input"] = [{"role": "user", "content": parts}]
        if request.temperature is not None:
            arguments["temperature"] = request.temperature
        if request.max_tokens is not None:
            arguments["max_output_tokens"] = request.max_tokens
        return arguments

    def complete(self, request: LLMRequest) -> LLMResponse:
        arguments = self.build_arguments(request)
        started = self._clock()
        try:
            response = self._client.responses.create(**arguments)
        except Exception as exc:  # every SDK failure is translated, never passed through
            failure = translate_error(exc)
        else:
            latency_ms = max(0, int((self._clock() - started) * 1000))
            return self._to_response(response, request, arguments["model"], latency_ms)
        # Raised here, outside the except block, so the SDK exception - whose message
        # may carry part of the key - is neither the cause nor the context.
        raise failure

    def _to_response(
        self, response: Any, request: LLMRequest, model: str, latency_ms: int
    ) -> LLMResponse:
        status = getattr(response, "status", None)
        if status not in (None, "completed"):
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or getattr(
                getattr(response, "error", None), "code", None
            )
            suffix = f" ({_safe(reason)})" if _safe(reason) else ""
            raise ProviderUnavailableError(
                f"the OpenAI response ended with status {_safe(status) or 'unknown'}{suffix}; "
                "its partial output was not used"
            )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=str(getattr(response, "output_text", "") or ""),
            model_id=str(getattr(response, "model", None) or model),
            provider=self.provider_name,
            prompt_template_id=request.prompt_template_id,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=latency_ms,
            is_stub=False,
            response_id=_safe(getattr(response, "id", None)),
        )


def translate_error(exc: BaseException) -> Exception:
    """The ReqPilot error for an SDK failure. Its message is written here, never copied."""
    try:
        import openai
    except ImportError:  # pragma: no cover - a client exists only if the SDK does
        return ProviderUnavailableError(f"the OpenAI call failed ({type(exc).__name__})")

    if isinstance(exc, openai.APITimeoutError):
        return TransientProviderError("the OpenAI request timed out")
    if isinstance(exc, openai.APIConnectionError):
        return TransientProviderError("could not reach the OpenAI API (connection error)")
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        detail = _describe(exc)
        if isinstance(exc, openai.AuthenticationError):
            return ProviderUnavailableError(
                f"OpenAI rejected the credentials ({detail}); check LLM_API_KEY"
            )
        if isinstance(exc, openai.PermissionDeniedError):
            return ProviderUnavailableError(
                f"OpenAI refused access ({detail}); the key may not have access to the "
                "configured model"
            )
        if isinstance(exc, openai.NotFoundError):
            return ProviderUnavailableError(
                f"OpenAI did not find the model or endpoint ({detail}); check "
                "LLM_MODEL_DEFAULT and LLM_BASE_URL"
            )
        if status == 429 and exc.code == "insufficient_quota":
            return ProviderUnavailableError(f"the OpenAI quota is exhausted ({detail})")
        if status in _TRANSIENT_STATUSES or status >= 500:
            return TransientProviderError(f"OpenAI is temporarily unavailable ({detail})")
        return ProviderUnavailableError(f"OpenAI refused the request ({detail})")
    return ProviderUnavailableError(f"the OpenAI call failed ({type(exc).__name__})")


def _describe(exc: Any) -> str:
    """HTTP status plus the error body's identifiers. Never the error message."""
    parts = [f"HTTP {int(exc.status_code)}"]
    for name in ("type", "code", "param", "request_id"):
        value = _safe(getattr(exc, name, None))
        if value:
            parts.append(f"{name}={value}")
    return ", ".join(parts)


def _safe(value: object) -> str | None:
    """``value`` if it is a short identifier; otherwise nothing."""
    if value is None:
        return None
    text = str(value)
    return text if _SAFE_TOKEN.match(text) else None
