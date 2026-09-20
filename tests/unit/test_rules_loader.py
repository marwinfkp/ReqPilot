"""Rule-configuration loading tests (architecture DQ-03).

Two invariants matter here and both exist so that a rating or score can always
name the ruleset version that produced it: every ruleset declares a version,
and loaded rulesets are immutable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules import load_all, load_ruleset

pytestmark = pytest.mark.unit

EXAMPLE = Path("src/reqpilot/rules/data/example.yaml")


def test_example_ruleset_loads() -> None:
    ruleset = load_ruleset(EXAMPLE)
    assert ruleset.name == "example"
    assert ruleset.version == "1.0.0"


def test_nested_values_are_accessible() -> None:
    ruleset = load_ruleset(EXAMPLE)
    assert ruleset["demonstration"]["lookup"]["high"] == 3


def test_loaded_ruleset_is_immutable() -> None:
    """A caller must not be able to mutate shared rule data at runtime."""
    ruleset = load_ruleset(EXAMPLE)
    with pytest.raises(TypeError):
        ruleset.data["demonstration"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        ruleset["demonstration"]["lookup"]["high"] = 99  # type: ignore[index]


def test_missing_key_raises_a_named_error() -> None:
    ruleset = load_ruleset(EXAMPLE)
    with pytest.raises(RuleConfigurationError, match="has no key"):
        _ = ruleset["not_there"]


def test_missing_file_is_reported_clearly() -> None:
    with pytest.raises(RuleConfigurationError, match="not found"):
        load_ruleset("src/reqpilot/rules/data/does_not_exist.yaml")


@pytest.mark.parametrize(
    "content,expected",
    [
        ("name: x\nrules: {}\n", "version"),
        ("name: x\nversion: '1'\n", "rules"),
        ("version: '1'\nrules: {}\n", "name"),
    ],
)
def test_every_required_key_is_enforced(tmp_path: Path, content: str, expected: str) -> None:
    """An unversioned or unnamed ruleset is rejected, not silently accepted."""
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match=expected):
        load_ruleset(path)


def test_empty_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: x\nversion: '  '\nrules: {}\n", encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match="empty version"):
        load_ruleset(path)


def test_malformed_yaml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match="not valid YAML"):
        load_ruleset(path)


def test_non_mapping_toplevel_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match="mapping at the top level"):
        load_ruleset(path)


def test_load_all_finds_the_example_ruleset() -> None:
    rulesets = load_all("src/reqpilot/rules/data")
    assert "example" in rulesets


def test_load_all_rejects_a_missing_directory() -> None:
    with pytest.raises(RuleConfigurationError, match="directory not found"):
        load_all("src/reqpilot/rules/nope")
