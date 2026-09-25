"""The structured document model every artefact is built as (ADR-007, ``FR-DOC-008``).

**One structured artefact, several renderings.** An assembler (in
``services/documents``) builds a :class:`Document` from persisted, approved data;
the Markdown and DOCX renderers in this package turn that one object into text
and into a Word file. There is no DOCX-only path and no Markdown-only path: both
read the same sections, blocks and citations, so they carry the same
authoritative information.

Pure data - no I/O, no database, no model. Two properties are enforced here:

* **Canonical hashing.** :meth:`Document.content_hash` is a sha256 over a
  canonical JSON serialisation of the structure *excluding* the generation
  timestamp and the generating user, so identical inputs give an identical hash
  (the reproducibility key of ``FR-DOC-009``).
* **Section traceability** (``FR-DOC-008``). :func:`validate_document` refuses a
  document in which a content section cites no requirement version of the
  baseline, or cites one outside it. Front matter and explicitly empty sections
  are the only exemptions, and a project-level section (a project-level risk has
  no requirement) must cite the project-level rows it is about.

Requirement and source text is **data** here. Nothing in this module evaluates,
formats or interprets it; the renderers escape it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from reqpilot.domain.errors import ArtifactError

SectionKind = Literal["front_matter", "content", "empty"]
SectionScope = Literal["baseline", "project"]
#: A citation names a persisted row. Only these kinds become ``CITES`` trace links
#: (they are the allowlisted targets); the others - a compliance gap, an open
#: clarification or task on the open-issues list - are recorded in the structure.
TRACEABLE_CITATION_KINDS: frozenset[str] = frozenset(
    {"requirement_version", "risk", "compliance_mapping", "evidence"}
)

#: Bumped when the canonical serialisation changes (it invalidates old hashes).
DOCUMENT_MODEL_VERSION = "document-model-1.0.0"


@dataclass(frozen=True)
class Citation:
    """A section's reference to a persisted row. Becomes a ``CITES`` trace link."""

    kind: str
    id: str
    label: str


@dataclass(frozen=True)
class Paragraph:
    text: str
    kind: str = "paragraph"


@dataclass(frozen=True)
class Notice:
    """A standing notice from template code - never generated text."""

    text: str
    kind: str = "notice"


@dataclass(frozen=True)
class Items:
    items: tuple[str, ...]
    ordered: bool = False
    kind: str = "items"


@dataclass(frozen=True)
class Fields:
    """Label / value pairs, e.g. one requirement's structured record."""

    pairs: tuple[tuple[str, str], ...]
    kind: str = "fields"


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    kind: str = "table"

    def __post_init__(self) -> None:
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ArtifactError("every table row needs one cell per column")


Block = Paragraph | Notice | Items | Fields | Table


@dataclass(frozen=True)
class Section:
    key: str
    number: str
    title: str
    level: int = 2
    kind: SectionKind = "content"
    scope: SectionScope = "baseline"
    blocks: tuple[Block, ...] = ()
    citations: tuple[Citation, ...] = ()

    def cites(self, kind: str) -> tuple[Citation, ...]:
        return tuple(c for c in self.citations if c.kind == kind)

    def canonical(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "number": self.number,
            "title": self.title,
            "level": self.level,
            "kind": self.kind,
            "scope": self.scope,
            "blocks": [_block_dict(b) for b in self.blocks],
            # Order is the assembler's (deterministic) order, kept so that every
            # rendering lists citations identically.
            "citations": [{"kind": c.kind, "id": c.id, "label": c.label} for c in self.citations],
        }

    def content_hash(self) -> str:
        return sha256_json(self.canonical())


@dataclass(frozen=True)
class Document:
    artifact_type: str
    title: str
    template_id: str
    template_version: str
    #: Ordered, displayed metadata that is part of the content (baseline, scope, ...).
    metadata: tuple[tuple[str, str], ...]
    sections: tuple[Section, ...]
    #: Generation stamp (FR-DOC-009): shown on the document, excluded from the hash.
    stamp: tuple[tuple[str, str], ...] = field(default=())

    def canonical(self) -> dict[str, Any]:
        return {
            "model": DOCUMENT_MODEL_VERSION,
            "artifact_type": self.artifact_type,
            "title": self.title,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "metadata": [list(p) for p in self.metadata],
            "sections": [s.canonical() for s in self.sections],
        }

    def content_hash(self) -> str:
        return sha256_json(self.canonical())

    def with_stamp(self, stamp: Iterable[tuple[str, str]]) -> Document:
        return Document(
            artifact_type=self.artifact_type,
            title=self.title,
            template_id=self.template_id,
            template_version=self.template_version,
            metadata=self.metadata,
            sections=self.sections,
            stamp=tuple(stamp),
        )

    def structure(self) -> dict[str, Any]:
        """What is persisted: the canonical structure plus the stamp."""
        data = self.canonical()
        data["stamp"] = [list(p) for p in self.stamp]
        return data

    def cited_version_ids(self) -> frozenset[str]:
        return frozenset(
            c.id for s in self.sections for c in s.citations if c.kind == "requirement_version"
        )


def _block_dict(block: Block) -> dict[str, Any]:
    if isinstance(block, Table):
        return {
            "kind": "table",
            "columns": list(block.columns),
            "rows": [list(r) for r in block.rows],
        }
    if isinstance(block, Fields):
        return {"kind": "fields", "pairs": [list(p) for p in block.pairs]}
    if isinstance(block, Items):
        return {"kind": "items", "ordered": block.ordered, "items": list(block.items)}
    return {"kind": block.kind, "text": block.text}


def sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_document(document: Document, baseline_version_ids: frozenset[str]) -> None:
    """``FR-DOC-008`` and ``FR-HIL-004`` for one document, before anything is stored.

    * section keys are unique;
    * every ``content`` section cites at least one requirement version, unless it
      is a ``project``-scope section, which must cite at least one row instead;
    * every cited requirement version is in the baseline scope - a document can
      never cite a version that is not an approved baseline member;
    * an ``empty`` section cites nothing and says so.
    """
    keys = [s.key for s in document.sections]
    if len(keys) != len(set(keys)):
        raise ArtifactError("artefact section keys must be unique")
    if not any(s.kind == "content" for s in document.sections) and not any(
        s.kind == "empty" for s in document.sections
    ):
        raise ArtifactError("an artefact needs at least one content or declared-empty section")
    for section in document.sections:
        versions = section.cites("requirement_version")
        outside = [c.id for c in versions if c.id not in baseline_version_ids]
        if outside:
            raise ArtifactError(
                f"section {section.number} cites requirement version(s) outside the approved "
                f"baseline: {sorted(outside)}"
            )
        if section.kind == "content":
            if section.scope == "baseline" and not versions:
                raise ArtifactError(
                    f"section {section.number} {section.title!r} traces to no requirement "
                    "version of the baseline (FR-DOC-008)"
                )
            if section.scope == "project" and not section.citations:
                raise ArtifactError(
                    f"project-level section {section.number} {section.title!r} cites nothing"
                )
        if section.kind == "empty" and section.citations:
            raise ArtifactError(f"section {section.number} is declared empty but cites rows")


def document_from_structure(data: dict[str, Any]) -> Document:
    """Rebuild the document from its persisted structure (for DOCX and CSV export).

    The inverse of :meth:`Document.structure`. Exports are rendered from what was
    stored - never re-assembled from live data - so a downloaded file is the
    artefact version it names.
    """

    def block(raw: dict[str, Any]) -> Block:
        kind = raw.get("kind")
        if kind == "table":
            return Table(tuple(raw["columns"]), tuple(tuple(r) for r in raw["rows"]))
        if kind == "fields":
            return Fields(tuple((str(a), str(b)) for a, b in raw["pairs"]))
        if kind == "items":
            return Items(tuple(raw["items"]), ordered=bool(raw.get("ordered")))
        if kind == "notice":
            return Notice(str(raw["text"]))
        return Paragraph(str(raw["text"]))

    try:
        return Document(
            artifact_type=str(data["artifact_type"]),
            title=str(data["title"]),
            template_id=str(data["template_id"]),
            template_version=str(data["template_version"]),
            metadata=tuple((str(a), str(b)) for a, b in data["metadata"]),
            sections=tuple(
                Section(
                    key=str(s["key"]),
                    number=str(s["number"]),
                    title=str(s["title"]),
                    level=int(s["level"]),
                    kind=s["kind"],
                    scope=s["scope"],
                    blocks=tuple(block(b) for b in s["blocks"]),
                    citations=tuple(
                        Citation(str(c["kind"]), str(c["id"]), str(c["label"]))
                        for c in s["citations"]
                    ),
                )
                for s in data["sections"]
            ),
            stamp=tuple((str(a), str(b)) for a, b in data.get("stamp", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError(f"stored artefact structure is malformed: {exc}") from exc
