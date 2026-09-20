"""Versioned rule/config loading (architecture DQ-03, section M7).

Rule-like configuration lives in versioned YAML data files, never in the
environment, so that changing a rule is a data change rather than a code change.

Two invariants this loader enforces, because both are what make a rule change
auditable later:

* **Every ruleset declares a version.** An unversioned file is rejected. A risk
  rating or an SDLC score must be able to name the ruleset that produced it.
* **Rulesets are immutable once loaded.** The returned mapping is deep-frozen,
  so a caller cannot mutate shared rule data at runtime.

P0 provides the mechanism and one example file. The real risk matrix, MCDA
weights, SDLC rules and control catalogues arrive with the roadmap phases that
use them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from reqpilot.domain.errors import RuleConfigurationError


@dataclass(frozen=True)
class RuleSet:
    """An immutable, versioned set of deterministic rules."""

    name: str
    version: str
    description: str
    data: MappingProxyType

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        try:
            return self.data[key]
        except KeyError as exc:
            raise RuleConfigurationError(
                f"ruleset {self.name!r} v{self.version} has no key {key!r}"
            ) from exc


def _freeze(value: Any) -> Any:
    """Recursively make a loaded structure read-only."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def load_ruleset(path: str | Path) -> RuleSet:
    """Load and validate a versioned ruleset from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise RuleConfigurationError(f"ruleset file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleConfigurationError(f"ruleset {path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuleConfigurationError(f"ruleset {path} must be a mapping at the top level")

    for required in ("name", "version", "rules"):
        if required not in raw:
            raise RuleConfigurationError(
                f"ruleset {path} is missing required key {required!r}; "
                "every ruleset must declare name, version and rules"
            )

    version = str(raw["version"])
    if not version.strip():
        raise RuleConfigurationError(f"ruleset {path} has an empty version")

    return RuleSet(
        name=str(raw["name"]),
        version=version,
        description=str(raw.get("description", "")),
        data=_freeze(raw["rules"]),
    )


def load_all(directory: str | Path) -> dict[str, RuleSet]:
    """Load every ``*.yaml`` ruleset in a directory, keyed by ruleset name."""
    directory = Path(directory)
    if not directory.is_dir():
        raise RuleConfigurationError(f"rules directory not found: {directory}")

    loaded: dict[str, RuleSet] = {}
    for path in sorted(directory.glob("*.yaml")):
        ruleset = load_ruleset(path)
        if ruleset.name in loaded:
            raise RuleConfigurationError(f"duplicate ruleset name {ruleset.name!r} in {directory}")
        loaded[ruleset.name] = ruleset
    return loaded
