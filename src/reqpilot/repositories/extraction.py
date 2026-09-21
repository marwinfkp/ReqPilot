"""Project-scoped persistence for sources, runs, proposals, labels and reviews (P3).

The foundation pattern throughout: every public method authorises first and
scopes second. Business rules - what a proposal becomes, who may override a
label - live in the services, not here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from reqpilot.domain.enums import Action, ResourceType, ReviewStatus
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    ExtractionCandidate,
    ModelVersion,
    PromptTemplate,
    RequirementClassification,
    ReviewItem,
    SourceChunk,
    SourceDocument,
)
from reqpilot.domain.models.runs import AgentRun, GraphRun
from reqpilot.repositories.base import ProjectScopedRepository


class SourceDocumentRepository(ProjectScopedRepository[SourceDocument]):
    resource_type = ResourceType.SOURCE_DOCUMENT

    def add(self, document: SourceDocument, chunks: Sequence[SourceChunk]) -> SourceDocument:
        project_id = ProjectId(document.project_id)
        self.authorize(Action.SOURCE_CREATE, project_id)
        if any(c.project_id != document.project_id for c in chunks):
            raise ValueError("every chunk must belong to its document's project")
        self._session.add(document)
        self._session.flush()
        self._session.add_all(chunks)
        self._session.flush()
        return document

    def get(self, project_id: ProjectId, document_id: uuid.UUID) -> SourceDocument | None:
        self.authorize(Action.SOURCE_READ, project_id)
        stmt = select(SourceDocument).where(SourceDocument.id == document_id)
        return self._session.scalars(
            self.scoped(stmt, SourceDocument.project_id, project_id)
        ).first()

    def get_by_hash(self, project_id: ProjectId, content_hash: str) -> SourceDocument | None:
        self.authorize(Action.SOURCE_READ, project_id)
        stmt = select(SourceDocument).where(SourceDocument.content_hash == content_hash)
        return self._session.scalars(
            self.scoped(stmt, SourceDocument.project_id, project_id)
        ).first()

    def list_for_project(self, project_id: ProjectId) -> list[SourceDocument]:
        self.authorize(Action.SOURCE_READ, project_id)
        stmt = select(SourceDocument).order_by(SourceDocument.created_at, SourceDocument.id)
        return list(self._session.scalars(self.scoped(stmt, SourceDocument.project_id, project_id)))

    def chunks(self, project_id: ProjectId, document_id: uuid.UUID) -> list[SourceChunk]:
        self.authorize(Action.SOURCE_READ, project_id)
        stmt = (
            select(SourceChunk)
            .where(SourceChunk.source_document_id == document_id)
            .order_by(SourceChunk.ordinal)
        )
        return list(self._session.scalars(self.scoped(stmt, SourceChunk.project_id, project_id)))

    def chunks_by_id(
        self, project_id: ProjectId, chunk_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, SourceChunk]:
        self.authorize(Action.SOURCE_READ, project_id)
        if not chunk_ids:
            return {}
        stmt = select(SourceChunk).where(SourceChunk.id.in_(list(chunk_ids)))
        rows = self._session.scalars(self.scoped(stmt, SourceChunk.project_id, project_id))
        return {row.id: row for row in rows}

    def documents_by_id(
        self, project_id: ProjectId, document_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, SourceDocument]:
        self.authorize(Action.SOURCE_READ, project_id)
        if not document_ids:
            return {}
        stmt = select(SourceDocument).where(SourceDocument.id.in_(list(document_ids)))
        rows = self._session.scalars(self.scoped(stmt, SourceDocument.project_id, project_id))
        return {row.id: row for row in rows}


class RunRepository(ProjectScopedRepository[GraphRun]):
    """Graph runs, agent runs, and the prompt and model provenance they cite."""

    resource_type = ResourceType.GRAPH_RUN

    def add_run(self, run: GraphRun) -> GraphRun:
        self.authorize(Action.RUN_START, ProjectId(run.project_id))
        self._session.add(run)
        self._session.flush()
        return run

    def update_run(self, run: GraphRun) -> GraphRun:
        self.authorize(Action.RUN_RECORD, ProjectId(run.project_id))
        self._session.flush()
        return run

    def get_run(self, project_id: ProjectId, run_id: uuid.UUID) -> GraphRun | None:
        self.authorize(Action.RUN_READ, project_id)
        stmt = select(GraphRun).where(GraphRun.id == run_id)
        return self._session.scalars(self.scoped(stmt, GraphRun.project_id, project_id)).first()

    def list_runs(self, project_id: ProjectId) -> list[GraphRun]:
        self.authorize(Action.RUN_READ, project_id)
        stmt = select(GraphRun).order_by(GraphRun.started_at.desc())
        return list(self._session.scalars(self.scoped(stmt, GraphRun.project_id, project_id)))

    def add_agent_run(self, project_id: ProjectId, agent_run: AgentRun) -> AgentRun:
        self.authorize(Action.RUN_RECORD, project_id)
        run = self._session.get(GraphRun, agent_run.graph_run_id)
        if run is None or run.project_id != project_id:
            raise ValueError("an agent run belongs to a graph run of the same project")
        self._session.add(agent_run)
        self._session.flush()
        return agent_run

    def agent_runs(self, project_id: ProjectId, run_id: uuid.UUID) -> list[AgentRun]:
        self.authorize(Action.RUN_READ, project_id)
        stmt = (
            select(AgentRun)
            .join(GraphRun, GraphRun.id == AgentRun.graph_run_id)
            .where(AgentRun.graph_run_id == run_id, GraphRun.project_id == project_id)
            .order_by(AgentRun.started_at, AgentRun.id)
        )
        return list(self._session.scalars(stmt))

    def prompt_template(
        self, project_id: ProjectId, *, name: str, version: str
    ) -> PromptTemplate | None:
        self.authorize(Action.RUN_READ, project_id)
        stmt = select(PromptTemplate).where(
            PromptTemplate.name == name, PromptTemplate.version == version
        )
        return self._session.scalars(stmt).first()

    def add_prompt_template(
        self, project_id: ProjectId, template: PromptTemplate
    ) -> PromptTemplate:
        self.authorize(Action.RUN_RECORD, project_id)
        self._session.add(template)
        self._session.flush()
        return template

    def model_version(
        self, project_id: ProjectId, *, provider: str, model_id: str, params_hash: str
    ) -> ModelVersion | None:
        self.authorize(Action.RUN_READ, project_id)
        stmt = select(ModelVersion).where(
            ModelVersion.provider == provider,
            ModelVersion.model_id == model_id,
            ModelVersion.params_hash == params_hash,
        )
        return self._session.scalars(stmt).first()

    def add_model_version(self, project_id: ProjectId, model: ModelVersion) -> ModelVersion:
        self.authorize(Action.RUN_RECORD, project_id)
        self._session.add(model)
        self._session.flush()
        return model

    def get_model_version(
        self, project_id: ProjectId, model_version_id: uuid.UUID
    ) -> ModelVersion | None:
        self.authorize(Action.RUN_READ, project_id)
        return self._session.get(ModelVersion, model_version_id)

    def get_prompt_template(
        self, project_id: ProjectId, template_id: uuid.UUID
    ) -> PromptTemplate | None:
        self.authorize(Action.RUN_READ, project_id)
        return self._session.get(PromptTemplate, template_id)


class CandidateRepository(ProjectScopedRepository[ExtractionCandidate]):
    resource_type = ResourceType.EXTRACTION_CANDIDATE

    def add_all(self, project_id: ProjectId, candidates: Sequence[ExtractionCandidate]) -> None:
        self.authorize(Action.RUN_RECORD, project_id)
        if any(c.project_id != project_id for c in candidates):
            raise ValueError("every candidate must belong to the run's project")
        self._session.add_all(candidates)
        self._session.flush()

    def record_decision(self, project_id: ProjectId, candidate: ExtractionCandidate) -> None:
        self.authorize(Action.RUN_RECORD, project_id)
        if candidate.project_id != project_id:
            raise ValueError("candidate is not in this project")
        self._session.flush()

    def get(self, project_id: ProjectId, candidate_id: uuid.UUID) -> ExtractionCandidate | None:
        self.authorize(Action.RUN_READ, project_id)
        stmt = select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id)
        return self._session.scalars(
            self.scoped(stmt, ExtractionCandidate.project_id, project_id)
        ).first()

    def list_for_run(self, project_id: ProjectId, run_id: uuid.UUID) -> list[ExtractionCandidate]:
        self.authorize(Action.RUN_READ, project_id)
        stmt = (
            select(ExtractionCandidate)
            .where(ExtractionCandidate.graph_run_id == run_id)
            .order_by(ExtractionCandidate.ordinal)
        )
        return list(
            self._session.scalars(self.scoped(stmt, ExtractionCandidate.project_id, project_id))
        )

    def by_ids(
        self, project_id: ProjectId, candidate_ids: Sequence[uuid.UUID]
    ) -> list[ExtractionCandidate]:
        self.authorize(Action.RUN_READ, project_id)
        if not candidate_ids:
            return []
        stmt = (
            select(ExtractionCandidate)
            .where(ExtractionCandidate.id.in_(list(candidate_ids)))
            .order_by(ExtractionCandidate.ordinal)
        )
        return list(
            self._session.scalars(self.scoped(stmt, ExtractionCandidate.project_id, project_id))
        )

    def for_version(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[ExtractionCandidate]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(ExtractionCandidate).where(
            ExtractionCandidate.requirement_version_id == version_id
        )
        return list(
            self._session.scalars(self.scoped(stmt, ExtractionCandidate.project_id, project_id))
        )


class ClassificationRepository(ProjectScopedRepository[RequirementClassification]):
    resource_type = ResourceType.CLASSIFICATION

    def add_revision(
        self,
        project_id: ProjectId,
        labels: Sequence[RequirementClassification],
        *,
        action: Action,
    ) -> None:
        if action not in (Action.CLASSIFICATION_PROPOSE, Action.CLASSIFICATION_OVERRIDE):
            raise ValueError("a classification revision is either a proposal or an override")
        self.authorize(action, project_id)
        if not labels:
            raise ValueError("a classification revision has at least one label")
        if len({(label.requirement_version_id, label.revision_no) for label in labels}) != 1:
            raise ValueError("one revision belongs to one version")
        if any(label.project_id != project_id for label in labels):
            raise ValueError("labels must belong to this project")
        self._session.add_all(labels)
        self._session.flush()

    def latest_revision_no(self, project_id: ProjectId, version_id: uuid.UUID) -> int:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = select(func.max(RequirementClassification.revision_no)).where(
            RequirementClassification.requirement_version_id == version_id,
            RequirementClassification.project_id == project_id,
        )
        return self._session.scalar(stmt) or 0

    def revision(
        self, project_id: ProjectId, version_id: uuid.UUID, revision_no: int
    ) -> list[RequirementClassification]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = (
            select(RequirementClassification)
            .where(
                RequirementClassification.requirement_version_id == version_id,
                RequirementClassification.revision_no == revision_no,
            )
            .order_by(RequirementClassification.category)
        )
        return list(
            self._session.scalars(
                self.scoped(stmt, RequirementClassification.project_id, project_id)
            )
        )

    def current(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[RequirementClassification]:
        """The labels of the newest revision - the version's classification now."""
        latest = self.latest_revision_no(project_id, version_id)
        return self.revision(project_id, version_id, latest) if latest else []

    def current_categories(self, project_id: ProjectId, version_id: uuid.UUID) -> list[str]:
        return [str(label.category) for label in self.current(project_id, version_id)]

    def history(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[RequirementClassification]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = (
            select(RequirementClassification)
            .where(RequirementClassification.requirement_version_id == version_id)
            .order_by(RequirementClassification.revision_no, RequirementClassification.category)
        )
        return list(
            self._session.scalars(
                self.scoped(stmt, RequirementClassification.project_id, project_id)
            )
        )


class AcceptanceCriterionRepository(ProjectScopedRepository[AcceptanceCriterion]):
    resource_type = ResourceType.REQUIREMENT_VERSION

    def add_all(self, project_id: ProjectId, criteria: Sequence[AcceptanceCriterion]) -> None:
        self.authorize(Action.REQUIREMENT_CREATE, project_id)
        if any(c.project_id != project_id for c in criteria):
            raise ValueError("criteria must belong to this project")
        self._session.add_all(criteria)
        self._session.flush()

    def for_version(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[AcceptanceCriterion]:
        self.authorize(Action.REQUIREMENT_READ, project_id)
        stmt = (
            select(AcceptanceCriterion)
            .where(AcceptanceCriterion.requirement_version_id == version_id)
            .order_by(AcceptanceCriterion.ordinal)
        )
        return list(
            self._session.scalars(self.scoped(stmt, AcceptanceCriterion.project_id, project_id))
        )


class ReviewItemRepository(ProjectScopedRepository[ReviewItem]):
    resource_type = ResourceType.REVIEW_ITEM

    def add(self, item: ReviewItem) -> ReviewItem:
        self.authorize(Action.RUN_RECORD, ProjectId(item.project_id))
        self._session.add(item)
        self._session.flush()
        return item

    def record_resolution(self, item: ReviewItem) -> ReviewItem:
        self.authorize(Action.REVIEW_RESOLVE, ProjectId(item.project_id))
        self._session.flush()
        return item

    def get(self, project_id: ProjectId, item_id: uuid.UUID) -> ReviewItem | None:
        self.authorize(Action.REVIEW_READ, project_id)
        stmt = select(ReviewItem).where(ReviewItem.id == item_id)
        return self._session.scalars(self.scoped(stmt, ReviewItem.project_id, project_id)).first()

    def list_for_project(
        self, project_id: ProjectId, *, status: ReviewStatus | None = None
    ) -> list[ReviewItem]:
        """The queue, ordered as architecture M.5 asks: low signal and age first.

        P3 has no blocking items and no risk severity yet, so the remaining keys
        of M.5's order - (blocking, risk severity) - are constant here.
        """
        self.authorize(Action.REVIEW_READ, project_id)
        stmt = select(ReviewItem)
        if status is not None:
            stmt = stmt.where(ReviewItem.status == status)
        stmt = self.scoped(stmt, ReviewItem.project_id, project_id)
        items = list(self._session.scalars(stmt))
        return sorted(
            items,
            key=lambda i: (
                i.status is not ReviewStatus.OPEN,
                1.0 if i.review_signal is None else i.review_signal,
                i.created_at,
                str(i.id),
            ),
        )

    def open_for_version(self, project_id: ProjectId, version_id: uuid.UUID) -> list[ReviewItem]:
        self.authorize(Action.REVIEW_READ, project_id)
        stmt = select(ReviewItem).where(
            ReviewItem.requirement_version_id == version_id,
            ReviewItem.status == ReviewStatus.OPEN,
        )
        return list(self._session.scalars(self.scoped(stmt, ReviewItem.project_id, project_id)))

    def for_run(self, project_id: ProjectId, run_id: uuid.UUID) -> list[ReviewItem]:
        self.authorize(Action.REVIEW_READ, project_id)
        stmt = select(ReviewItem).where(ReviewItem.graph_run_id == run_id)
        return list(self._session.scalars(self.scoped(stmt, ReviewItem.project_id, project_id)))
