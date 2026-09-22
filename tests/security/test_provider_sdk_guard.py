"""The CI provider-SDK guard: OpenAI approved, every other provider SDK refused.

The guard (``scripts/check_provider_sdks.py``) inspects installed distributions;
these tests drive it with explicit package lists, so no provider is installed,
imported or called.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib

import pytest
from tests.p3_helpers import REPO_ROOT

pytestmark = pytest.mark.security


def _guard():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "check_provider_sdks", REPO_ROOT / "scripts" / "check_provider_sdks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_provider_sdks"] = module
    spec.loader.exec_module(module)
    return module


GUARD = _guard()


def test_openai_is_the_only_approved_provider_sdk() -> None:
    assert frozenset({"openai"}) == GUARD.APPROVED_PROVIDER_SDKS
    assert GUARD.APPROVED_PROVIDER_SDKS <= GUARD.KNOWN_PROVIDER_SDKS


@pytest.mark.parametrize(
    "installed",
    [["openai", "fastapi"], ["fastapi", "pydantic"], []],
    ids=["openai", "no-provider", "empty"],
)
def test_approved_or_absent_providers_pass(installed: list[str], capsys) -> None:  # type: ignore[no-untyped-def]
    assert GUARD.main(installed) == 0
    assert "all approved" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("installed", "refused"),
    [
        (["openai", "anthropic"], ["anthropic"]),
        (["ollama"], ["ollama"]),
        (["Anthropic", "OLLAMA", "openai"], ["anthropic", "ollama"]),
        (["google_generativeai"], ["google-generativeai"]),
        (["langchain.anthropic"], ["langchain-anthropic"]),
    ],
)
def test_unapproved_providers_fail(installed: list[str], refused: list[str], capsys) -> None:  # type: ignore[no-untyped-def]
    assert GUARD.unapproved(installed) == refused
    assert GUARD.main(installed) == 1
    out = capsys.readouterr().out
    assert all(name in out for name in refused)


def test_the_real_environment_passes() -> None:
    assert GUARD.unapproved(GUARD.installed_distributions()) == []


def test_the_project_declares_no_unapproved_provider() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        declared.extend(extra)
    names = {
        GUARD.normalise(spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip())
        for spec in declared
    }
    assert GUARD.unapproved(names) == []
    # The approved SDK is optional for the application (a keyless install carries none).
    assert "openai" not in {
        GUARD.normalise(d.split(">")[0].strip()) for d in project["dependencies"]
    }


def test_ci_runs_the_guard_and_cannot_ignore_it() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/check_provider_sdks.py" in workflow
    step = workflow.split("python scripts/check_provider_sdks.py")[0].rsplit("- name:", 1)[1]
    assert "continue-on-error" not in step
    line = next(line for line in workflow.splitlines() if "check_provider_sdks.py" in line)
    assert "||" not in line and "true" not in line
    assert "LLM_PROVIDER: stub" in workflow, "CI stays offline: no provider is ever called"
