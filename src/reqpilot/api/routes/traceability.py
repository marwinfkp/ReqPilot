"""Governance, traceability and artefact endpoints (P8; architecture S, M.5, N, C.6).

What P8 needs to be usable, and no more:

* ``GET  /projects/{id}/review-queue`` - the single review queue (``FR-HIL-006``).
* ``GET  /requirement-versions/{id}/readiness`` - what still blocks a version.
* ``POST /projects/{id}/governance/fan-out`` - raise the G4/G5/G7 tasks the
  persisted state requires (raising is not deciding).
* ``POST /requirement-versions/{id}/architecture-flag`` - the analyst's G5 flag.
* ``POST /projects/{id}/traceability/sync`` - materialise typed trace links.
* ``GET  /projects/{id}/traceability`` - the RTM (``?format=csv`` / ``md``), from
  the persisted graph, for a baseline scope or the current project view.
* ``GET  /projects/{id}/traceability/coverage`` - ``FR-TRC-003`` and E6.
* ``GET  /requirement-versions/{id}/trace-links`` - one exact version's recorded
  edges, historical versions included (``FR-TRC-004``).
* ``POST /baselines/{id}/artifacts`` - generate artefacts from an approved baseline.
* ``GET  /projects/{id}/artifacts``, ``/artifacts/{id}/versions``,
  ``/artifact-versions/{id}`` - artefacts and their immutable history.
* ``GET  /artifact-versions/{id}/export?format=markdown|docx|csv`` - downloads.

Every gate is still decided only through ``POST /approval-tasks/{id}/decide``.
No endpoint here approves, baselines, sets a severity or asserts a link. A
resource outside the caller's projects is a 404, exactly as for one that does
not exist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from reqpilot.api.dependencies import CurrentActor, DbSession
from reqpilot.api.lookup import require_found
from reqpilot.api.schemas import ApprovalTaskOut
from reqpilot.api.trace_schemas import (
    ArtifactOut,
    ArtifactVersionDetailOut,
    ArtifactVersionOut,
    BlockerOut,
    CoverageOut,
    CoverageVersionOut,
    FanOutOut,
    FlagIn,
    GenerateIn,
    GenerationOut,
    QueueEntryOut,
    QueueOut,
    ReadinessOut,
    RtmOut,
    SectionOut,
    SyncOut,
    TraceLinkOut,
)
from reqpilot.domain.enums import Action, ArtifactFormat, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.repositories.baseline import BaselineRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.documents import DEFAULT_SET, ArtifactService, GenerationOutcome
from reqpilot.services.governance import (
    GovernanceFanOut,
    GovernanceReadinessService,
    UnifiedReviewQueue,
)
from reqpilot.services.traceability import (
    RTM_COLUMNS,
    CoverageReport,
    TraceGraphSync,
    TraceQueryService,
    rtm_csv,
)

router = APIRouter(prefix="/api/v1", tags=["traceability"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


def _version(session: DbSession, actor: Actor, version_id: uuid.UUID):  # type: ignore[no-untyped-def]
    repo = RequirementVersionRepository(session, actor)
    return require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda pid: repo.get(pid, version_id),
    )


def generation_out(outcome: GenerationOutcome) -> GenerationOut:
    return GenerationOut(
        artifact_type=outcome.artifact_type,
        refused=outcome.refused,
        reused_identical_version=outcome.reused,
        blockers=list(outcome.blockers),
        version=ArtifactVersionOut.model_validate(outcome.version) if outcome.version else None,
    )


def coverage_out(report: CoverageReport) -> CoverageOut:
    return CoverageOut(
        scope_kind=report.scope_kind,
        scope_label=report.scope_label,
        definition_version=report.definition_version,
        total=report.total,
        fully_traced=report.fully_traced,
        e6=report.e6,
        counts=dict(report.counts),
        orphan_requirements=list(report.orphan_requirements),
        unsourced_statements=list(report.unsourced_statements),
        unlinked_risks=list(report.unlinked_risks),
        project_level_risks=report.project_level_risks,
        findings_without_parent=list(report.findings_without_parent),
        versions=[
            CoverageVersionOut(
                version_id=str(v.version_id),
                requirement=f"{v.human_id} v{v.version_no}",
                state=v.state,
                fully_traced=v.fully_traced,
                missing=list(v.missing),
            )
            for v in report.versions
        ],
    )


# --- governance -----------------------------------------------------------------


@router.get("/projects/{project_id}/review-queue", response_model=QueueOut)
def review_queue(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, mine_only: bool = False
) -> QueueOut:
    view = UnifiedReviewQueue(session, actor).build(ProjectId(project_id), mine_only=mine_only)
    return QueueOut(
        ordering_rule=view.ordering_rule,
        entries=[
            QueueEntryOut(
                kind=e.kind,
                item_id=e.item_id,
                title=e.title,
                blocking=e.blocking,
                severity=e.severity,
                review_signal=e.review_signal,
                created_at=e.created_at,
                gate=e.gate,
                required_role=e.required_role,
                subject_type=e.subject_type,
                subject_id=e.subject_id,
                subject_label=e.subject_label,
                actionable=e.actionable,
                link=e.link,
            )
            for e in view.entries
        ],
    )


@router.get("/requirement-versions/{version_id}/readiness", response_model=ReadinessOut)
def version_readiness(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ReadinessOut:
    project_id, version = _version(session, actor, version_id)
    require(
        actor,
        Action.GOVERNANCE_READ,
        ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id),
    )
    readiness = GovernanceReadinessService(session, actor).evaluate(
        project_id, version, stage="submission"
    )
    return ReadinessOut(
        version_id=version.id,
        ready=readiness.ready,
        blockers=[
            BlockerOut(
                code=b.code,
                message=b.message,
                gate=b.gate,
                subject_type=b.subject_type,
                subject_id=b.subject_id,
            )
            for b in readiness.blockers
        ],
    )


@router.post("/projects/{project_id}/governance/fan-out", response_model=FanOutOut)
def fan_out(project_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> FanOutOut:
    result = GovernanceFanOut(session, actor).raise_required(ProjectId(project_id))
    return FanOutOut(
        g4=[ApprovalTaskOut.model_validate(t) for t in result.g4],
        g5=[ApprovalTaskOut.model_validate(t) for t in result.g5],
        g7=[ApprovalTaskOut.model_validate(t) for t in result.g7],
    )


@router.post(
    "/requirement-versions/{version_id}/architecture-flag",
    response_model=ApprovalTaskOut,
    status_code=status.HTTP_201_CREATED,
)
def architecture_flag(
    version_id: uuid.UUID, payload: FlagIn, session: DbSession, actor: CurrentActor
) -> ApprovalTaskOut:
    project_id, _version_row = _version(session, actor, version_id)
    task = GovernanceFanOut(session, actor).flag_architecture_critical(
        project_id, version_id, payload.reason
    )
    return ApprovalTaskOut.model_validate(task)


# --- traceability ---------------------------------------------------------------


@router.post("/projects/{project_id}/traceability/sync", response_model=SyncOut)
def sync_trace(project_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> SyncOut:
    result = TraceGraphSync(session, actor).sync(ProjectId(project_id))
    return SyncOut(
        created=result.created,
        already_present=result.already_present,
        by_link_type=dict(sorted(result.by_link_type.items())),
        unresolved_source_refs=result.unresolved_source_refs,
    )


@router.get("/projects/{project_id}/traceability", response_model=None)
def rtm(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    baseline_id: uuid.UUID | None = None,
    format: str = Query("json", pattern="^(json|csv|md)$"),
) -> Response | RtmOut:
    pid = _project(actor, project_id, Action.TRACE_READ)
    rows = ArtifactService(session, actor).rtm_rows(pid, baseline_id)
    if format == "csv":
        return PlainTextResponse(
            rtm_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="rtm.csv"'},
        )
    if format == "md":
        from reqpilot.artifacts.markdown import md_cell

        lines = [
            "| " + " | ".join(title for _k, title in RTM_COLUMNS) + " |",
            "|" + "---|" * len(RTM_COLUMNS),
        ]
        lines += [
            "| " + " | ".join(md_cell(r.get(k)) for k, _t in RTM_COLUMNS) + " |" for r in rows
        ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/markdown; charset=utf-8")
    return RtmOut(
        scope=f"baseline {baseline_id}"
        if baseline_id
        else "project (current versions; not all approved)",
        columns=[key for key, _t in RTM_COLUMNS],
        rows=[dict(r.cells) for r in rows],
    )


@router.get("/projects/{project_id}/traceability/coverage", response_model=CoverageOut)
def coverage(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    baseline_id: uuid.UUID | None = None,
) -> CoverageOut:
    pid = _project(actor, project_id, Action.TRACE_READ)
    return coverage_out(ArtifactService(session, actor).coverage(pid, baseline_id))


@router.get("/requirement-versions/{version_id}/trace-links", response_model=list[TraceLinkOut])
def version_links(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[TraceLinkOut]:
    project_id, _version_row = _version(session, actor, version_id)
    links = TraceQueryService(session, actor).version_links(project_id, version_id)
    return [TraceLinkOut.model_validate(link) for link in links]


# --- artefacts ------------------------------------------------------------------


@router.post("/baselines/{baseline_id}/artifacts", response_model=list[GenerationOut])
def generate(
    baseline_id: uuid.UUID, payload: GenerateIn, session: DbSession, actor: CurrentActor
) -> JSONResponse:
    """Generate artefacts from an approved baseline.

    ``201`` when at least one artefact version exists afterwards; ``409`` when
    every requested artefact was refused. A refusal is answered with its reasons
    and is itself audited - the response is returned rather than raised so the
    audit record of the refusal is kept.
    """
    repo = BaselineRepository(session, actor)
    project_id, _baseline = require_found(
        actor, Action.BASELINE_READ, ResourceType.BASELINE, lambda pid: repo.get(pid, baseline_id)
    )
    types = tuple(dict.fromkeys(payload.artifact_types)) or DEFAULT_SET
    outcomes = ArtifactService(session, actor).generate_set(project_id, baseline_id, types)
    body = [generation_out(o).model_dump(mode="json") for o in outcomes]
    code = status.HTTP_409_CONFLICT if all(o.refused for o in outcomes) else status.HTTP_201_CREATED
    return JSONResponse(status_code=code, content=body)


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[ArtifactOut]:
    service = ArtifactService(session, actor)
    return [ArtifactOut.model_validate(a) for a in service.list_artifacts(ProjectId(project_id))]


@router.get("/artifacts/{artifact_id}/versions", response_model=list[ArtifactVersionOut])
def artifact_versions(
    artifact_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[ArtifactVersionOut]:
    service = ArtifactService(session, actor)
    project_id, artifact = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT,
        lambda pid: service.get_artifact(pid, artifact_id),
    )
    return [ArtifactVersionOut.model_validate(v) for v in service.versions(project_id, artifact.id)]


@router.get("/artifact-versions/{version_id}", response_model=ArtifactVersionDetailOut)
def artifact_version(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ArtifactVersionDetailOut:
    service = ArtifactService(session, actor)
    project_id, version = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT_VERSION,
        lambda pid: service.get_version(pid, version_id),
    )
    graph = TraceQueryService(session, actor).graph(project_id)
    sections = []
    for section in service.sections(project_id, version.id):
        out = SectionOut.model_validate(section)
        out.cites_requirement_versions = graph.targets(
            TraceNodeType.ARTIFACT_SECTION, section.id, TraceLinkType.CITES
        )
        sections.append(out)
    return ArtifactVersionDetailOut(
        version=ArtifactVersionOut.model_validate(version),
        sections=sections,
        markdown=version.markdown,
    )


@router.get("/artifact-versions/{version_id}/export", response_model=None)
def export_version(
    version_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    format: ArtifactFormat = ArtifactFormat.MARKDOWN,
) -> Response:
    service = ArtifactService(session, actor)
    project_id, _version_row = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT_VERSION,
        lambda pid: service.get_version(pid, version_id),
    )
    export = service.export(project_id, version_id, format)
    return Response(
        content=export.data,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "X-Content-SHA256": export.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts/{artifact_id}/versions/{version_no}", response_model=None)
def export_by_number(
    artifact_id: uuid.UUID,
    version_no: int,
    session: DbSession,
    actor: CurrentActor,
    format: ArtifactFormat = ArtifactFormat.MARKDOWN,
) -> Response:
    """Architecture S's path: ``GET /artifacts/{id}/versions/{n}`` (``?format=docx``)."""
    service = ArtifactService(session, actor)
    project_id, artifact = require_found(
        actor,
        Action.ARTIFACT_READ,
        ResourceType.ARTIFACT,
        lambda pid: service.get_artifact(pid, artifact_id),
    )
    match = next(
        (v for v in service.versions(project_id, artifact.id) if v.version_no == version_no), None
    )
    if match is None:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "not found"})
    return export_version(match.id, session, actor, format)
