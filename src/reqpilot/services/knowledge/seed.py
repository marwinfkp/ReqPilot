"""Seeding the knowledge base from a curated manifest (architecture V: ``scripts/seed_kb``).

Ingestion is manual curation only - no live feeds (J.6). A manifest is how a
curator hands the knowledge base a reviewed set of sources and items; every item
still goes through :class:`KnowledgeAdminService`, so the licence, taxonomy and
dedupe rules apply exactly as they do to an item added through the API, and every
addition is audited.

Seeding is idempotent: a source that already exists is reused and an item key
that already exists is skipped, so re-running a manifest adds only what is new.
It never versions or retires anything; those are deliberate curation acts.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from reqpilot.domain.enums import LicenceClass, NormativeSourceType, TextOrigin
from reqpilot.domain.errors import KnowledgeBaseError
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.extraction import extract_text
from reqpilot.services.knowledge.admin import (
    ItemSpec,
    KnowledgeAdminService,
    SourceSpec,
    normalise_jurisdiction,
)


class ManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str
    text_origin: TextOrigin
    title: str | None = None
    clause_ref: str | None = None
    applicability: list[str] = Field(default_factory=list)
    #: Inline text, or ``text_file`` relative to the manifest (.txt/.md/.pdf/.docx).
    text: str | None = None
    text_file: str | None = None


class ManifestSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: NormativeSourceType
    issuing_body: str
    title: str
    jurisdiction: str
    version: str
    effective_date: dt.date | None = None
    retrieved_at: dt.date
    source_url: str | None = None
    licence_class: LicenceClass
    licence_note: str
    items: list[ManifestItem] = Field(default_factory=list)


class KnowledgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: int = Field(ge=1, le=1)
    #: Whether every source in the manifest is synthetic. Declared, and checked.
    synthetic: bool
    description: str = ""
    sources: list[ManifestSource]


class SeedSummary(BaseModel):
    sources_added: int = 0
    sources_reused: int = 0
    items_added: int = 0
    items_skipped: int = 0


def load_manifest(path: str | Path) -> KnowledgeManifest:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise KnowledgeBaseError(f"cannot read knowledge manifest {path}: {exc}") from exc
    manifest = KnowledgeManifest.model_validate(raw)
    if manifest.synthetic and any(
        s.licence_class is not LicenceClass.SYNTHETIC for s in manifest.sources
    ):
        raise KnowledgeBaseError("a manifest declared synthetic may contain only synthetic sources")
    return manifest


def _item_text(item: ManifestItem, base_dir: Path) -> str:
    if (item.text is None) == (item.text_file is None):
        raise KnowledgeBaseError(f"item {item.item_key}: give exactly one of text or text_file")
    if item.text is not None:
        return item.text
    target = (base_dir / str(item.text_file)).resolve()
    if base_dir.resolve() not in target.parents:
        raise KnowledgeBaseError(
            f"item {item.item_key}: text_file must stay inside the manifest's directory"
        )
    return extract_text(target)


def seed_from_manifest(
    path: str | Path, admin: KnowledgeAdminService, repo: KnowledgeBaseRepository
) -> SeedSummary:
    """Apply a manifest through the admin service, in the caller's transaction."""
    path = Path(path)
    manifest = load_manifest(path)
    summary = SeedSummary()
    for spec in manifest.sources:
        source = repo.find_source(
            issuing_body=spec.issuing_body.strip(),
            title=spec.title.strip(),
            version=spec.version.strip(),
            jurisdiction=normalise_jurisdiction(spec.jurisdiction),
        )
        if source is None:
            source = admin.add_source(
                SourceSpec(
                    source_type=spec.source_type,
                    issuing_body=spec.issuing_body,
                    title=spec.title,
                    jurisdiction=spec.jurisdiction,
                    version=spec.version,
                    effective_date=spec.effective_date,
                    retrieved_at=spec.retrieved_at,
                    source_url=spec.source_url,
                    licence_class=spec.licence_class,
                    licence_note=spec.licence_note,
                )
            )
            summary.sources_added += 1
        else:
            summary.sources_reused += 1

        for item in spec.items:
            if repo.versions_of(item.item_key.strip().upper()):
                summary.items_skipped += 1
                continue
            admin.add_item(
                source.id,
                item.item_key,
                ItemSpec(
                    text=_item_text(item, path.parent),
                    text_origin=item.text_origin,
                    title=item.title,
                    clause_ref=item.clause_ref,
                    applicability=tuple(item.applicability),
                ),
            )
            summary.items_added += 1
    return summary
