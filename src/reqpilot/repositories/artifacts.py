"""Project-scoped persistence for generated artefacts (architecture G.8).

Versions and sections are append-only: this repository adds and reads them and
offers no update or delete. The artefact row's current-version pointer is the
one mutable column, moved only when a new version is added.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from reqpilot.domain.enums import Action, ArtifactType, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.artifacts import Artifact, ArtifactSection, ArtifactVersion
from reqpilot.repositories.base import ProjectScopedRepository


class ArtifactRepository(ProjectScopedRepository[Artifact]):
    resource_type = ResourceType.ARTIFACT

    # -- artefacts -------------------------------------------------------
    def get_or_create(
        self, project_id: ProjectId, artifact_type: ArtifactType, title: str, created_by: uuid.UUID
    ) -> Artifact:
        self.authorize(Action.ARTIFACT_GENERATE, project_id)
        found = self.by_type(project_id, artifact_type)
        if found is not None:
            return found
        artifact = Artifact(
            project_id=project_id, artifact_type=artifact_type, title=title, created_by=created_by
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def by_type(self, project_id: ProjectId, artifact_type: ArtifactType) -> Artifact | None:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(Artifact).where(Artifact.artifact_type == artifact_type)
        stmt = self.scoped(stmt, Artifact.project_id, project_id)
        return self._session.scalars(stmt).first()

    def get(self, project_id: ProjectId, artifact_id: uuid.UUID) -> Artifact | None:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(Artifact).where(Artifact.id == artifact_id)
        stmt = self.scoped(stmt, Artifact.project_id, project_id)
        return self._session.scalars(stmt).first()

    def list_for_project(self, project_id: ProjectId) -> list[Artifact]:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(Artifact).order_by(Artifact.artifact_type)
        stmt = self.scoped(stmt, Artifact.project_id, project_id)
        return list(self._session.scalars(stmt))

    # -- versions --------------------------------------------------------
    def next_version_no(self, project_id: ProjectId, artifact_id: uuid.UUID) -> int:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(func.max(ArtifactVersion.version_no)).where(
            ArtifactVersion.artifact_id == artifact_id
        )
        stmt = self.scoped(stmt, ArtifactVersion.project_id, project_id)
        return int(self._session.scalar(stmt) or 0) + 1

    def add_version(
        self, artifact: Artifact, version: ArtifactVersion, sections: list[ArtifactSection]
    ) -> ArtifactVersion:
        self.authorize(Action.ARTIFACT_GENERATE, artifact.project_id)  # type: ignore[arg-type]
        self._session.add(version)
        self._session.flush()
        for section in sections:
            self._session.add(section)
        self._session.flush()
        artifact.current_version_id = version.id
        self._session.flush()
        return version

    def get_version(self, project_id: ProjectId, version_id: uuid.UUID) -> ArtifactVersion | None:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(ArtifactVersion).where(ArtifactVersion.id == version_id)
        stmt = self.scoped(stmt, ArtifactVersion.project_id, project_id)
        return self._session.scalars(stmt).first()

    def versions(self, project_id: ProjectId, artifact_id: uuid.UUID) -> list[ArtifactVersion]:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact_id)
        stmt = self.scoped(stmt, ArtifactVersion.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(ArtifactVersion.version_no)))

    def versions_for_project(self, project_id: ProjectId) -> list[ArtifactVersion]:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(ArtifactVersion)
        stmt = self.scoped(stmt, ArtifactVersion.project_id, project_id)
        return list(
            self._session.scalars(
                stmt.order_by(ArtifactVersion.artifact_type, ArtifactVersion.version_no)
            )
        )

    def sections(self, project_id: ProjectId, version_id: uuid.UUID) -> list[ArtifactSection]:
        self.authorize(Action.ARTIFACT_READ, project_id)
        stmt = select(ArtifactSection).where(ArtifactSection.artifact_version_id == version_id)
        stmt = self.scoped(stmt, ArtifactSection.project_id, project_id)
        return list(self._session.scalars(stmt.order_by(ArtifactSection.ordinal)))
