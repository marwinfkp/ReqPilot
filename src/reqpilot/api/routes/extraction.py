"""Sources, batch extraction runs, classification and the review queue (P3).

Every handler goes through a service or the graph runner, and every service
authorises through ``policy.can`` in the repository layer. A caller outside the
project gets 404, exactly as for something that does not exist.

The only route that reaches a model is ``POST .../analysis-runs``, and it does
so through the one gateway. No route writes a lifecycle state, an approval or a
requirement identifier.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    ExtractionRulesDep,
    Gateway,
    Rules,
)
from reqpilot.api.extraction_schemas import (
    AgentRunOut,
    AnalysisRunIn,
    CandidateOut,
    ClassificationOut,
    CriterionOut,
    LabelOut,
    MergeIn,
    OverrideIn,
    RequirementRecordOut,
    ResolveIn,
    ReviewItemOut,
    RunDetailOut,
    RunSummaryOut,
    SourceChunkOut,
    SourceCreatedOut,
    SourceDetailOut,
    SourceDocumentOut,
    SourceTextIn,
)
from reqpilot.api.lookup import require_found
from reqpilot.domain.enums import (
    Action,
    DataSensitivity,
    ResourceType,
    ReviewStatus,
    SourceDocumentType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.runs import AgentRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.repositories.extraction import (
    AcceptanceCriterionRepository,
    CandidateRepository,
    RunRepository,
)
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.services.classification import ClassificationService
from reqpilot.services.extraction import (
    RequirementMergeService,
    RequirementRecordService,
    SourceDocumentService,
)
from reqpilot.services.review.service import ReviewQueueService

router = APIRouter(prefix="/api/v1", tags=["extraction"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


# --- sources --------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/sources",
    response_model=SourceCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def add_source(
    project_id: uuid.UUID,
    payload: SourceTextIn,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
) -> SourceCreatedOut:
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    document, created = service.add_text(
        project_id=_project(actor, project_id, Action.SOURCE_CREATE),
        doc_type=payload.doc_type,
        title=payload.title,
        text=payload.text,
        sensitivity=payload.sensitivity,
    )
    return SourceCreatedOut(created=created, source=SourceDocumentOut.model_validate(document))


@router.post(
    "/projects/{project_id}/sources/upload",
    response_model=SourceCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[SourceDocumentType, Form()],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    sensitivity: Annotated[DataSensitivity, Form()] = DataSensitivity.UNCLASSIFIED,
) -> SourceCreatedOut:
    data = await file.read()
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    document, created = service.add_file(
        project_id=_project(actor, project_id, Action.SOURCE_CREATE),
        doc_type=doc_type,
        title=title,
        data=data,
        filename=file.filename or "upload.txt",
        sensitivity=sensitivity,
    )
    return SourceCreatedOut(created=created, source=SourceDocumentOut.model_validate(document))


@router.get("/projects/{project_id}/sources", response_model=list[SourceDocumentOut])
def list_sources(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
) -> list[SourceDocumentOut]:
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    documents = service.list_documents(_project(actor, project_id, Action.SOURCE_READ))
    return [SourceDocumentOut.model_validate(d) for d in documents]


@router.get("/sources/{source_id}", response_model=SourceDetailOut)
def get_source(
    source_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: Rules,
    extraction_rules: ExtractionRulesDep,
) -> SourceDetailOut:
    service = SourceDocumentService(
        session, actor, retrieval_rules=rules, extraction_rules=extraction_rules
    )
    project_id, document = require_found(
        actor,
        Action.SOURCE_READ,
        ResourceType.SOURCE_DOCUMENT,
        lambda pid: service.get(pid, source_id),
    )
    chunks = service.chunks(project_id, document.id)
    base = SourceDocumentOut.model_validate(document).model_dump()
    return SourceDetailOut(
        **base,
        chunks=[
            SourceChunkOut(
                id=c.id,
                ordinal=c.ordinal,
                char_start=c.char_start,
                char_end=c.char_end,
                speaker=c.speaker,
                strategy=str(c.strategy),
                text=c.text,
            )
            for c in chunks
        ],
    )


# --- runs -------------------------------------------------------------------------


def _summary(summary: RunSummary) -> RunSummaryOut:
    return RunSummaryOut(
        run_id=summary.run_id,
        status=summary.status,
        requirement_version_ids=list(summary.requirement_version_ids),
        classified_version_ids=list(summary.classified_version_ids),
        review_item_ids=list(summary.review_item_ids),
        accepted=summary.accepted,
        merged=summary.merged,
        rejected=summary.rejected,
        errors=list(summary.errors),
        provider_calls=summary.provider_calls,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        cost_estimate=summary.cost_estimate,
    )


@router.post(
    "/projects/{project_id}/analysis-runs",
    response_model=RunSummaryOut,
    status_code=status.HTTP_201_CREATED,
)
def start_analysis_run(
    project_id: uuid.UUID,
    payload: AnalysisRunIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    settings: AppSettings,
) -> RunSummaryOut:
    """Batch extraction (``source_ids``) or classification (``version_ids``)."""
    pid = _project(actor, project_id, Action.RUN_START)
    runner = AnalysisRunner(session, gateway, extraction_rules, settings=settings)
    if payload.source_ids:
        summary = runner.extract(
            actor=actor, project_id=pid, source_ids=payload.source_ids, domain=payload.domain or ""
        )
    else:
        summary = runner.classify(actor=actor, project_id=pid, version_ids=payload.version_ids)
    return _summary(summary)


def _agent_run_out(runs: RunRepository, project_id: ProjectId, agent_run: AgentRun) -> AgentRunOut:
    model = (
        runs.get_model_version(project_id, uuid.UUID(agent_run.model_version_id))
        if agent_run.model_version_id
        else None
    )
    template = (
        runs.get_prompt_template(project_id, uuid.UUID(agent_run.prompt_template_id))
        if agent_run.prompt_template_id
        else None
    )
    prompt = f"{template.name}@{template.version}" if template else None
    return AgentRunOut(
        id=agent_run.id,
        node=agent_run.node,
        role=agent_run.role,
        status=agent_run.status,
        prompt=prompt,
        provider=model.provider if model else None,
        model=model.model_id if model else None,
        is_model=model.is_model if model else None,
        attempts=agent_run.attempts,
        tokens_in=agent_run.tokens_in,
        tokens_out=agent_run.tokens_out,
        cost_estimate=agent_run.cost_estimate,
        error_code=agent_run.error_code,
    )


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> RunDetailOut:
    runs = RunRepository(session, actor)
    project_id, run = require_found(
        actor, Action.RUN_READ, ResourceType.GRAPH_RUN, lambda pid: runs.get_run(pid, run_id)
    )
    candidates = CandidateRepository(session, actor).list_for_run(project_id, run.id)
    return RunDetailOut(
        id=run.id,
        project_id=run.project_id,
        graph_name=run.graph_name,
        status=run.status,
        started_by=run.started_by,
        started_at=run.started_at,
        finished_at=run.finished_at,
        agent_runs=[
            _agent_run_out(runs, project_id, a) for a in runs.agent_runs(project_id, run.id)
        ],
        candidates=[CandidateOut.model_validate(c) for c in candidates],
    )


# --- review queue --------------------------------------------------------------------


@router.get("/projects/{project_id}/review-items", response_model=list[ReviewItemOut])
def list_review_items(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    status_filter: ReviewStatus | None = None,
) -> list[ReviewItemOut]:
    pid = _project(actor, project_id, Action.REVIEW_READ)
    items = ReviewQueueService(session, actor).list(pid, status=status_filter)
    return [ReviewItemOut.model_validate(i) for i in items]


@router.post("/review-items/{item_id}/resolve", response_model=ReviewItemOut)
def resolve_review_item(
    item_id: uuid.UUID, payload: ResolveIn, session: DbSession, actor: CurrentActor
) -> ReviewItemOut:
    service = ReviewQueueService(session, actor)
    project_id, _item = require_found(
        actor, Action.REVIEW_READ, ResourceType.REVIEW_ITEM, lambda pid: service.get(pid, item_id)
    )
    item = service.resolve(
        project_id=project_id,
        item_id=item_id,
        resolution=payload.resolution,
        note=payload.note,
        categories=payload.categories,
        keep=payload.keep,
    )
    return ReviewItemOut.model_validate(item)


# --- classification and criteria -------------------------------------------------------


def _find_version(session: DbSession, actor: Actor, version_id: uuid.UUID) -> ProjectId:
    versions = RequirementVersionRepository(session, actor)
    project_id, _version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda pid: versions.get(pid, version_id),
    )
    return project_id


def _classification_out(
    service: ClassificationService, project_id: ProjectId, version_id: uuid.UUID
) -> ClassificationOut:
    return ClassificationOut(
        requirement_version_id=version_id,
        current=[
            LabelOut.model_validate(label) for label in service.current(project_id, version_id)
        ],
        history=[
            [LabelOut.model_validate(label) for label in revision]
            for revision in service.history(project_id, version_id)
        ],
    )


@router.get("/requirement-versions/{version_id}/classification", response_model=ClassificationOut)
def get_classification(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ClassificationOut:
    project_id = _find_version(session, actor, version_id)
    return _classification_out(ClassificationService(session, actor), project_id, version_id)


@router.put("/requirement-versions/{version_id}/classification", response_model=ClassificationOut)
def override_classification(
    version_id: uuid.UUID, payload: OverrideIn, session: DbSession, actor: CurrentActor
) -> ClassificationOut:
    """A human override (``FR-CLS-003``): a new revision; the old one is kept."""
    project_id = _find_version(session, actor, version_id)
    service = ClassificationService(session, actor)
    service.override(
        project_id=project_id,
        version_id=version_id,
        categories=payload.categories,
        reason=payload.reason,
    )
    return _classification_out(service, project_id, version_id)


@router.get(
    "/requirement-versions/{version_id}/acceptance-criteria", response_model=list[CriterionOut]
)
def get_acceptance_criteria(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[CriterionOut]:
    project_id = _find_version(session, actor, version_id)
    criteria = AcceptanceCriterionRepository(session, actor).for_version(project_id, version_id)
    return [CriterionOut.model_validate(c) for c in criteria]


# --- the normalised record and merging ---------------------------------------------------


@router.get("/requirements/{requirement_id}/record", response_model=RequirementRecordOut)
def get_requirement_record(
    requirement_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RequirementRecordOut:
    """The thirteen fields of ``FR-EXT-002``, with deferred ones marked as such."""
    requirements = RequirementRepository(session, actor)
    project_id, _requirement = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT,
        lambda pid: requirements.get(pid, requirement_id),
    )
    record = RequirementRecordService(session, actor).record(project_id, requirement_id)
    version = record.version
    return RequirementRecordOut(
        id=record.requirement.human_id,
        requirement_id=record.requirement.id,
        version_id=version.id,
        version_no=version.version_no,
        statement=version.statement,
        original_text=version.original_text,
        category=[label.category for label in record.labels]
        or ([version.category] if version.category else []),
        source_stakeholders=list(record.stakeholders),
        source_refs=list(version.source_refs or []),
        business_justification=version.justification,
        priority=version.priority,
        dependencies=list(version.dependencies or []),
        assumptions=list(version.assumptions or []),
        acceptance_criteria=[CriterionOut.model_validate(c) for c in record.criteria],
        applicable_regulations=record.applicable_regulations,
        risk_level=record.risk_level,
        confidence_score=version.review_signal,
        approval_status=version.state,
        open_review_items=record.open_review_items,
    )


@router.post("/requirements/{requirement_id}/merge", response_model=RequirementRecordOut)
def merge_requirement(
    requirement_id: uuid.UUID, payload: MergeIn, session: DbSession, actor: CurrentActor
) -> RequirementRecordOut:
    """Merge a duplicate into this requirement, keeping every source link."""
    requirements = RequirementRepository(session, actor)
    project_id, _requirement = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT,
        lambda pid: requirements.get(pid, requirement_id),
    )
    RequirementMergeService(session, actor).merge(
        project_id=project_id,
        survivor_id=requirement_id,
        duplicate_id=payload.duplicate_requirement_id,
        reason=payload.reason,
    )
    return get_requirement_record(requirement_id, session, actor)
