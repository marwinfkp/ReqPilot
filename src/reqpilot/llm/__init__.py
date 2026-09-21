"""Module M4 - the LLM gateway boundary (architecture ADR-006).

The single choke point through which every model call must pass. No other
package may import a provider SDK.

P3 implements the gateway's obligations - prompt registry and versioning,
trust-class assembly, egress guards, structured output with bounded repair,
retries, token and cost accounting, record/replay. Providers: the offline ones
(stub, scripted, replay) and one network provider, OpenAI, selected at P3
closure (architecture Y) and used only when ``LLM_PROVIDER=openai``.
"""

from reqpilot.llm.accounting import UsageLedger, UsageSink, estimate_cost
from reqpilot.llm.gateway import LLMGateway, build_gateway, build_provider, parse_json_object
from reqpilot.llm.openai_provider import OpenAIProvider
from reqpilot.llm.prompts import PromptRegistry, PromptSpec
from reqpilot.llm.providers import RecordingLLMGateway, ScriptedProvider, StubLLMGateway
from reqpilot.llm.types import (
    CompletionProvider,
    ContentBlock,
    GatewayErrorCode,
    LLMRequest,
    LLMResponse,
    ModelMeta,
    StructuredResult,
    TrustClass,
)

__all__ = [
    "CompletionProvider",
    "ContentBlock",
    "GatewayErrorCode",
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "ModelMeta",
    "OpenAIProvider",
    "PromptRegistry",
    "PromptSpec",
    "RecordingLLMGateway",
    "ScriptedProvider",
    "StructuredResult",
    "StubLLMGateway",
    "TrustClass",
    "UsageLedger",
    "UsageSink",
    "build_gateway",
    "build_provider",
    "estimate_cost",
    "parse_json_object",
]
