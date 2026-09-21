"""Proof that the default configuration makes no external LLM call and needs no credentials.

An explicit P0 requirement, tested rather than asserted in prose: install, run,
migrate and test must all work with zero API access. It still holds now that an
OpenAI provider exists (P3 closure): that provider is used only when configured,
and its SDK is not even imported otherwise.
"""

from __future__ import annotations

import subprocess
import sys

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


def test_an_unimplemented_provider_fails_clearly_rather_than_calling_out() -> None:
    """Selecting a provider with no implementation fails before any call is attempted."""
    settings = Settings(
        _env_file=None, LLM_PROVIDER=LLMProvider.ANTHROPIC, LLM_API_KEY="placeholder"
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        build_gateway(settings)


def test_the_default_configuration_imports_no_provider_sdk() -> None:
    """Starting the app and building the default gateway loads no provider client.

    Checked in a fresh interpreter, because other tests in this process may load
    the OpenAI SDK deliberately (its adapter is tested offline, with its own types).
    """
    code = "\n".join(
        [
            "import sys",
            "import reqpilot.main",
            "from reqpilot.config import Settings",
            "from reqpilot.llm import build_gateway",
            "build_gateway(Settings(_env_file=None))",
            "loaded = [m for m in ('anthropic', 'openai', 'ollama') if m in sys.modules]",
            "assert not loaded, loaded",
            "print('ok')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


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
