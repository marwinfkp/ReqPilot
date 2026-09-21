"""The prompt registry - versioned templates, stored outside code (DQ-04, F.5).

A prompt template is a data file, ``<name>-<version>.yaml``, under
``llm/prompts/``. ``registry.yaml`` pins which version of each is active and
locks the sha256 of every version's file. Three things follow:

* **A version always means one text.** Editing a template without bumping its
  version fails the lock at startup, so a run's recorded ``name@version``
  identifies exactly the text it used.
* **Templates are the only instruction source.** Their placeholders are
  declared with a pattern, and only validated parameters (trust class
  ``OPERATOR``) fill them; project content never does (architecture Q.1).
* **The role is part of the template.** A template registered for one agent
  role cannot be used by another.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType

import yaml

from reqpilot.domain.enums import AgentRole
from reqpilot.domain.errors import PromptRegistryError

DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
REGISTRY_FILE = "registry.yaml"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def file_sha256(path: Path) -> str:
    """sha256 of a template file, line endings normalised (platform-independent)."""
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptSpec:
    """One version of one registered prompt template."""

    name: str
    role: AgentRole
    version: str
    contract_version: str
    params: Mapping[str, re.Pattern[str]]
    text: str
    sha256: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def render(self, values: Mapping[str, str]) -> str:
        """Fill the declared slots with validated parameters.

        Every declared parameter must be supplied, nothing undeclared is
        accepted, and each value must fully match its pattern. A parameter is a
        short, validated value - never a place to put content.
        """
        supplied, declared = set(values), set(self.params)
        if supplied != declared:
            raise PromptRegistryError(
                f"{self.ref} takes parameters {sorted(declared)}, got {sorted(supplied)}"
            )
        for name, value in values.items():
            if not isinstance(value, str) or not self.params[name].fullmatch(value):
                raise PromptRegistryError(
                    f"parameter {name!r} for {self.ref} does not match its declared pattern"
                )
        return Template(self.text).substitute(values)


class PromptRegistry:
    """Loads, verifies and serves the active prompt templates."""

    def __init__(self, directory: Path = DEFAULT_PROMPT_DIR) -> None:
        self._directory = directory
        self._specs = MappingProxyType(self._load())

    @property
    def directory(self) -> Path:
        return self._directory

    def get(self, name: str) -> PromptSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise PromptRegistryError(f"no active prompt template named {name!r}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def _load(self) -> dict[str, PromptSpec]:
        registry_path = self._directory / REGISTRY_FILE
        try:
            registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
            active = dict(registry["active"])
            locked = dict(registry["locked"])
        except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
            raise PromptRegistryError(
                f"prompt registry {registry_path} is unreadable: {exc}"
            ) from exc

        specs: dict[str, PromptSpec] = {}
        for name, version in active.items():
            version = str(version)
            path = self._directory / f"{name}-{version}.yaml"
            if not path.exists():
                raise PromptRegistryError(f"active prompt {name}@{version} has no file {path.name}")
            sha = file_sha256(path)
            if locked.get(f"{name}@{version}") != sha:
                raise PromptRegistryError(
                    f"prompt {name}@{version} does not match its locked hash: a template's text "
                    "may not change without a new version (DQ-04)"
                )
            specs[name] = self._parse(path, name, version, sha)
        return specs

    @staticmethod
    def _parse(path: Path, name: str, version: str, sha: str) -> PromptSpec:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = PromptSpec(
                name=str(raw["name"]),
                role=AgentRole(raw["role"]),
                version=str(raw["version"]),
                contract_version=str(raw["contract_version"]),
                params=MappingProxyType(
                    {str(k): re.compile(str(v)) for k, v in (raw.get("params") or {}).items()}
                ),
                text=str(raw["system"]).replace("\r\n", "\n"),
                sha256=sha,
            )
        except (yaml.YAMLError, KeyError, TypeError, ValueError, re.error) as exc:
            raise PromptRegistryError(f"prompt template {path.name} is malformed: {exc}") from exc

        if (spec.name, spec.version) != (name, version) or not _VERSION.match(spec.version):
            raise PromptRegistryError(f"{path.name} declares {spec.ref}, not {name}@{version}")
        identifiers = set(Template(spec.text).get_identifiers())
        if identifiers != set(spec.params):
            raise PromptRegistryError(
                f"{spec.ref} uses placeholders {sorted(identifiers)} but declares "
                f"{sorted(spec.params)}"
            )
        return spec
