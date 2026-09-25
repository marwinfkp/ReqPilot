"""Generated artefacts: identity, immutable versions and traceable sections (G.8).

Architecture G.8 names ``artifact`` (identity plus a current-version pointer) and
``artifact_version`` (append-only, ``FR-DOC-009``). P8 adds ``artifact_section``
so that every section of every version is a row a trace link can point at
(``FR-DOC-008``): "SRS section 4.2 cites FR-LOAN-001 v2" is reconstructable from
the database, not merely printed in the text.

The rules these tables protect:

* **An artefact version is immutable.** Regenerating never overwrites history; a
  different result is a new version, and an identical result is reported as the
  existing version (the content hash decides - see ``ArtifactService``).
* **Every version names the exact baseline it was rendered from**, and the
  generation metadata of ``FR-DOC-009`` - timestamp, model identifier, prompt
  version and knowledge-base version. A deterministic artefact says so
  (``model_identifier = "deterministic"``, no prompt version) rather than
  pretending a model wrote it.
* **The structure is canonical; Markdown and DOCX are renderings of it.** The
  structured document is stored with its hash, the Markdown with its own hash,
  and DOCX is produced from the same structure on export - there is no
  DOCX-only data path.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import ArtifactType
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk

#: Written on a deterministic artefact instead of a model identifier (FR-DOC-009).
DETERMINISTIC_MODEL_IDENTIFIER = "deterministic"


class Artifact(Base):
    """One artefact of one type in one project; its versions carry the content."""

    __tablename__ = "artifact"
    __table_args__ = (
        UniqueConstraint("project_id", "artifact_type", name="project_type"),
        UniqueConstraint("id", "project_id", name="id_project"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[ArtifactType] = mapped_column(
        SAEnum(ArtifactType, name="artifact_type_enum"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: The newest version. The only mutable column; it records history's head.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class ArtifactVersion(Base):
    """One immutable rendering of an artefact from one exact baseline (FR-DOC-009)."""

    __tablename__ = "artifact_version"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_no", name="artifact_version_no"),
        UniqueConstraint("id", "project_id", name="id_project"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_present"),
        CheckConstraint("length(model_identifier) >= 1", name="model_identifier_present"),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifact.id", "artifact.project_id"],
            name="fk_artifact_version_artifact",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_artifact_version_baseline",
        ),
        Index("ix_artifact_version_artifact", "artifact_id", "version_no"),
        Index("ix_artifact_version_baseline", "project_id", "baseline_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_type: Mapped[ArtifactType] = mapped_column(
        SAEnum(ArtifactType, name="artifact_type_enum", create_type=False), nullable=False
    )
    #: The exact baseline this version was rendered from. Never null: an
    #: authoritative artefact without a baseline is exactly what FR-HIL-004 forbids.
    baseline_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    # --- generation metadata (FR-DOC-009) ---------------------------------
    template_id: Mapped[str] = mapped_column(String(100), nullable=False)
    template_version: Mapped[str] = mapped_column(String(20), nullable=False)
    generator: Mapped[str] = mapped_column(String(100), nullable=False)
    #: ``"deterministic"`` for an artefact no model contributed to.
    model_identifier: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Null when no prompt was used (a deterministic artefact).
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: The knowledge-base version the cited evidence came from, if any.
    kb_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How ``kb_version`` was determined: ``project_pin``, ``evidence`` or ``none``.
    kb_version_source: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- content ------------------------------------------------------------
    #: sha256 of the canonical structure - the reproducibility key. Excludes the
    #: generation timestamp and the generating user, so identical inputs give an
    #: identical hash.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 of the exact input set: baseline members and their hashes plus the
    #: persisted rows each section was built from.
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    structure: Mapped[dict] = mapped_column(JsonType, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cited_version_count: Mapped[int] = mapped_column(Integer, nullable=False)

    generated_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generated_at: Mapped[dt.datetime] = created_at_column()


class ArtifactSection(Base):
    """One section of one artefact version, addressable by trace links (FR-DOC-008)."""

    __tablename__ = "artifact_section"
    __table_args__ = (
        UniqueConstraint("artifact_version_id", "section_key", name="version_section"),
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["artifact_version_id", "project_id"],
            ["artifact_version.id", "artifact_version.project_id"],
            name="fk_artifact_section_version",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    artifact_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Stable key within the artefact, e.g. ``req.FR-LOAN-001`` or ``4.2``.
    section_key: Mapped[str] = mapped_column(String(120), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``content`` sections must cite the baseline; ``front_matter`` and
    #: explicitly ``empty`` sections are the only exemptions (FR-DOC-008).
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


@event.listens_for(Session, "before_flush")
def _guard_artifacts(session: Session, _context: object, _instances: object) -> None:
    """Artefact versions and sections are append-only; an artefact moves its pointer only."""
    for instance in session.deleted:
        if isinstance(instance, (Artifact, ArtifactVersion, ArtifactSection)):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are never deleted; history is preserved"
            )
    for instance in session.dirty:
        if not isinstance(instance, (Artifact, ArtifactVersion, ArtifactSection)):
            continue
        state: Any = inspect(instance)
        changed = {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}
        if isinstance(instance, Artifact):
            changed -= {"current_version_id"}
        if changed:
            raise ImmutableRecordError(
                f"{type(instance).__name__} is immutable (attempted: {sorted(changed)}); "
                "regenerate to create a new version instead"
            )
