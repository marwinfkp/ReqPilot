"""The versioned artefact-template registry (``FR-DOC-001``; ADR-007).

Loads ``templates/artefact_templates.yaml`` through the P0 versioned-ruleset
loader, so the file must declare a name and version and is deep-frozen once
loaded. A missing entry or field is a startup error, not a silently different
document.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reqpilot.domain.enums import ArtifactType
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.loader import load_ruleset

REGISTRY_FILE = Path(__file__).resolve().parent / "templates" / "artefact_templates.yaml"


@dataclass(frozen=True)
class ArtefactTemplate:
    artifact_type: ArtifactType
    template_id: str
    version: str
    title: str
    purpose: str
    text: dict[str, str]

    def get(self, key: str) -> str:
        if key not in self.text:
            raise RuleConfigurationError(f"template {self.template_id} has no text {key!r}")
        return self.text[key]


@dataclass(frozen=True)
class TemplateRegistry:
    version: str
    templates: dict[ArtifactType, ArtefactTemplate]

    def for_type(self, artifact_type: ArtifactType) -> ArtefactTemplate:
        return self.templates[artifact_type]


def load_template_registry(path: Path = REGISTRY_FILE) -> TemplateRegistry:
    ruleset = load_ruleset(path)
    templates: dict[ArtifactType, ArtefactTemplate] = {}
    for artifact_type in ArtifactType:
        entry = ruleset.get(artifact_type.value)
        if entry is None:
            raise RuleConfigurationError(f"no artefact template for {artifact_type.value}")
        try:
            fixed = {"template_id", "version", "title", "purpose"}
            templates[artifact_type] = ArtefactTemplate(
                artifact_type=artifact_type,
                template_id=str(entry["template_id"]),
                version=str(entry["version"]),
                title=str(entry["title"]),
                purpose=" ".join(str(entry["purpose"]).split()),
                text={k: " ".join(str(v).split()) for k, v in entry.items() if k not in fixed},
            )
        except KeyError as exc:
            raise RuleConfigurationError(
                f"artefact template {artifact_type.value} is missing {exc}"
            ) from exc
    return TemplateRegistry(version=ruleset.version, templates=templates)


@lru_cache(maxsize=1)
def packaged_templates() -> TemplateRegistry:
    return load_template_registry()
