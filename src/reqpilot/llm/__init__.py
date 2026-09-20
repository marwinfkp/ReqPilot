"""Module M4 - the LLM gateway boundary (architecture ADR-006).

The single choke point through which every model call must pass. No other
package may import a provider SDK.

P0 establishes the abstraction and an offline stub only. No network call is
implemented and no API key is required to install, migrate, run or test.
"""

from reqpilot.llm.gateway import (
    LLMGateway,
    LLMRequest,
    LLMResponse,
    RecordingLLMGateway,
    StubLLMGateway,
    build_gateway,
)

__all__ = [
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "RecordingLLMGateway",
    "StubLLMGateway",
    "build_gateway",
]
