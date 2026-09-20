"""Proof that P0 makes no external LLM call and needs no credentials.

An explicit P0 requirement, tested rather than asserted in prose: install, run,
migrate and test must all work with zero API access.
"""

from __future__ import annotations

import pytest

from reqpilot.config import LLMProvider, Settings
from reqpilot.domain.enums import AgentRole
from reqpilot.llm import (
    LLMGateway,
    LLMRequest,
    RecordingLLMGateway,
    StubLLMGateway,
    build_gateway,
)

pytestmark = pytest.mark.security


def a_request() -> LLMRequest:
    return LLMRequest(
        role=AgentRole.REQUIREMENT_EXTRACTION,
        prompt_template_id="extraction.v1",
        instructions="Extract requirements.",
        untrusted_content={"transcript": "The system shall do something."},
    )


def test_default_gateway_needs_no_credentials() -> None:
    gateway = build_gateway(Settings(_env_file=None))
    assert isinstance(gateway, LLMGateway)


def test_stub_returns_a_marked_response() -> None:
    """A stub answer must never be mistakable for a real one."""
    response = StubLLMGateway(Settings(_env_file=None)).complete(a_request())
    assert response.is_stub is True
    assert response.provider == "stub"
    assert response.model_id == "stub"


def test_network_provider_is_not_implemented_in_p0() -> None:
    """Selecting a real provider fails clearly rather than attempting a call."""
    settings = Settings(
        _env_file=None, LLM_PROVIDER=LLMProvider.ANTHROPIC, LLM_API_KEY="placeholder"
    )
    with pytest.raises(NotImplementedError, match="not implemented in P0"):
        build_gateway(settings)


def test_no_provider_sdk_is_installed_or_imported() -> None:
    """The gateway must not have pulled in a provider client as a dependency."""
    import sys

    for module in ("anthropic", "openai", "ollama"):
        assert module not in sys.modules, f"{module} was imported during P0 tests"


def test_recording_gateway_replays_without_calling_inner(tmp_path) -> None:
    """Replay must hit the fixture, not the wrapped gateway."""

    class ExplodingGateway:
        def complete(self, request: LLMRequest):
            raise AssertionError("inner gateway must not be called on a fixture hit")

    request = a_request()
    recorder = RecordingLLMGateway(
        StubLLMGateway(Settings(_env_file=None)), fixture_dir=tmp_path, mode="record"
    )
    recorder.complete(request)  # writes the fixture

    replayer = RecordingLLMGateway(ExplodingGateway(), fixture_dir=tmp_path, mode="replay")
    replayed = replayer.complete(request)
    assert replayed.is_stub is True


def test_fixture_key_changes_when_the_prompt_changes(tmp_path) -> None:
    """Prompt drift must invalidate the fixture rather than reuse a stale answer."""
    recorder = RecordingLLMGateway(
        StubLLMGateway(Settings(_env_file=None)), fixture_dir=tmp_path, mode="replay"
    )
    first = recorder.fixture_key(a_request())
    changed = a_request().model_copy(update={"instructions": "Extract requirements differently."})
    assert recorder.fixture_key(changed) != first


def test_fixture_key_is_stable_for_identical_requests(tmp_path) -> None:
    recorder = RecordingLLMGateway(
        StubLLMGateway(Settings(_env_file=None)), fixture_dir=tmp_path, mode="replay"
    )
    assert recorder.fixture_key(a_request()) == recorder.fixture_key(a_request())


def test_recording_gateway_rejects_an_unknown_mode(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown fixture mode"):
        RecordingLLMGateway(StubLLMGateway(Settings(_env_file=None)), tmp_path, mode="guess")


def test_request_separates_instructions_from_untrusted_content() -> None:
    """The trust-class split must exist in the contract from the start.

    Untrusted content has its own field so it can never be concatenated into
    the instruction position by accident (architecture Q.1).
    """
    request = a_request()
    assert "transcript" in request.untrusted_content
    assert "The system shall do something." not in request.instructions
