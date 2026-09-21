"""Human oversight of AI proposals: overrides, the review queue, merging (FR-CLS-003, M.5).

The queue is not approval. The last tests here prove it: resolving every item
creates no approval task and moves no requirement past ``CLASSIFIED``; G1 is
still the only way to approval, and it still needs two roles.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import ingest, member, run_extraction
from tests.workflow.test_p1_exit_test import make_project

from reqpilot.domain.enums import (
    AuditEventType,
    CandidateStatus,
    ProposalSource,
    RequirementCategory,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
    Role,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ClassificationError,
    ExtractionError,
    ImmutableRecordError,
    ProjectIsolationError,
    ReviewError,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    ExtractionCandidate,
    RequirementClassification,
    ReviewItem,
)
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.classification import ClassificationService
from reqpilot.services.extraction import RequirementMergeService, pipeline_actor
from reqpilot.services.requirements import RequirementService
from reqpilot.services.review import ReviewQueue
from reqpilot.services.review.service import ReviewQueueService

pytestmark = pytest.mark.integration

C = RequirementCategory


@pytest.fixture
def world(db_session: Session):
    project = make_project(db_session)
    analyst = member(db_session, project, Role.ANALYST, "analyst@example.test")
    document = ingest(db_session, analyst, project.id)
    summary = run_extraction(db_session, analyst, project.id, [document.id])
    pid = ProjectId(project.id)
    service = RequirementService(db_session, analyst)
    versions = {
        r.human_id: service.get_version(pid, r.current_version_id)
        for r in service.list_requirements(pid)
    }
    return {
        "session": db_session,
        "project": project,
        "pid": pid,
        "analyst": analyst,
        "reviewer": member(db_session, project, Role.ANALYST, "reviewer@example.test"),
        "officer": member(db_session, project, Role.COMPLIANCE_OFFICER, "officer@example.test"),
        "auditor": member(db_session, project, Role.AUDITOR, "auditor@example.test"),
        "stakeholder": member(db_session, project, Role.STAKEHOLDER, "stake@example.test"),
        "versions": versions,
        "summary": summary,
    }


def item(world, reason: ReviewReason) -> ReviewItem:
    return next(i for i in world["session"].scalars(select(ReviewItem)) if i.reason is reason)


# --- overrides (FR-CLS-003) -------------------------------------------------------


def test_an_override_is_a_new_revision_that_keeps_the_old_one(world) -> None:
    perf = world["versions"]["NFR-LOAN-002"]
    service = ClassificationService(world["session"], world["analyst"])
    revision = service.override(
        project_id=world["pid"],
        version_id=perf.id,
        categories=[C.PERFORMANCE],
        reason="load is not availability",
    )
    assert revision == 2
    history = service.history(world["pid"], perf.id)
    assert [sorted(str(label.category) for label in rev) for rev in history] == [
        ["availability", "performance"],
        ["performance"],
    ]
    (human,) = service.current(world["pid"], perf.id)
    assert human.source is ProposalSource.HUMAN and human.review_signal is None
    assert (
        human.created_by == world["analyst"].actor_id
        and human.change_reason == "load is not availability"
    )
    assert world["session"].get(type(perf), perf.id).content_hash == perf.content_hash, (
        "version untouched"
    )


def test_an_override_is_audited_with_before_and_after(world) -> None:
    perf = world["versions"]["NFR-LOAN-002"]
    ClassificationService(world["session"], world["analyst"]).override(
        project_id=world["pid"],
        version_id=perf.id,
        categories=[C.PERFORMANCE, C.USABILITY],
        reason="r",
    )
    event = next(
        e
        for e in AuditService(world["session"]).list_for_project(world["project"].id)
        if e.event_type is AuditEventType.HUMAN_OVERRIDE
    )
    assert event.payload == {
        "override": "classification",
        "from_revision": 1,
        "to_revision": 2,
        "before": ["availability", "performance"],
        "after": ["performance", "usability"],
    }
    assert event.actor_kind.value == "human" and event.actor_ref == str(world["analyst"].actor_id)


def test_an_override_settles_the_versions_label_review_items(world) -> None:
    perf = world["versions"]["NFR-LOAN-002"]
    low = item(world, ReviewReason.LOW_CLASSIFICATION_SIGNAL)
    assert low.requirement_version_id == perf.id and low.status is ReviewStatus.OPEN
    ClassificationService(world["session"], world["analyst"]).override(
        project_id=world["pid"], version_id=perf.id, categories=[C.PERFORMANCE], reason="r"
    )
    world["session"].refresh(low)
    assert low.status is ReviewStatus.RESOLVED and low.resolution is ReviewResolution.OVERRIDDEN


@pytest.mark.parametrize("who", ["officer", "auditor", "reviewer_system"])
def test_only_a_human_analyst_may_override(world, who) -> None:
    actor = (
        pipeline_actor(world["analyst"], world["pid"], world["summary"].run_id)
        if who == "reviewer_system"
        else world[who]
    )
    with pytest.raises(AuthorizationError):
        ClassificationService(world["session"], actor).override(
            project_id=world["pid"],
            version_id=world["versions"]["FR-LOAN-001"].id,
            categories=[C.SECURITY],
            reason="r",
        )


def test_an_override_in_another_project_is_refused(world) -> None:
    other = make_project(world["session"], "Payments")
    outsider = member(world["session"], other, Role.ANALYST, "o@example.test")
    with pytest.raises(ProjectIsolationError):
        ClassificationService(world["session"], outsider).override(
            project_id=world["pid"],
            version_id=world["versions"]["FR-LOAN-001"].id,
            categories=[C.SECURITY],
            reason="r",
        )


@pytest.mark.parametrize(
    ("categories", "reason", "message"),
    [
        ([], "r", "at least one"),
        ([C.FUNCTIONAL], "r", "does not change"),
        ([C.SECURITY], " ", "reason"),
    ],
)
def test_empty_or_pointless_overrides_are_refused(world, categories, reason, message) -> None:
    with pytest.raises(ClassificationError, match=message):
        ClassificationService(world["session"], world["analyst"]).override(
            project_id=world["pid"],
            version_id=world["versions"]["FR-LOAN-001"].id,
            categories=categories,
            reason=reason,
        )


def test_a_version_under_validation_or_approval_cannot_be_relabelled(world) -> None:
    upload = world["versions"]["FR-LOAN-001"]
    requirements = RequirementService(world["session"], world["analyst"])
    for target in (RequirementState.ANALYZED, RequirementState.VALIDATED):
        requirements.transition(project_id=world["pid"], version_id=upload.id, target=target)
    with pytest.raises(ClassificationError, match="VALIDATED version cannot be relabelled"):
        ClassificationService(world["session"], world["analyst"]).override(
            project_id=world["pid"], version_id=upload.id, categories=[C.SECURITY], reason="late"
        )


# --- the review queue --------------------------------------------------------------


def queue(world, who: str = "analyst") -> ReviewQueueService:
    return ReviewQueueService(world["session"], world[who])


def test_the_queue_orders_the_lowest_signal_first(world) -> None:
    signals = [i.review_signal for i in queue(world).list(world["pid"], status=ReviewStatus.OPEN)]
    known = [s for s in signals if s is not None]
    assert known == sorted(known) and signals[: len(known)] == known


def test_accepting_a_low_signal_extraction_keeps_the_requirement(world) -> None:
    low = item(world, ReviewReason.LOW_EXTRACTION_SIGNAL)
    resolved = queue(world).resolve(
        project_id=world["pid"], item_id=low.id, resolution=ReviewResolution.ACCEPTED
    )
    assert (
        resolved.status is ReviewStatus.RESOLVED
        and resolved.resolved_by == world["analyst"].actor_id
    )
    version = world["session"].get(
        type(world["versions"]["FR-LOAN-003"]), low.requirement_version_id
    )
    assert version.state is RequirementState.CLASSIFIED


def test_rejecting_a_proposal_withdraws_it_through_p1(world) -> None:
    low = item(world, ReviewReason.LOW_EXTRACTION_SIGNAL)
    queue(world).resolve(
        project_id=world["pid"],
        item_id=low.id,
        resolution=ReviewResolution.REJECTED,
        note="too vague",
    )
    version = world["session"].get(
        type(world["versions"]["FR-LOAN-003"]), low.requirement_version_id
    )
    assert version.state is RequirementState.WITHDRAWN


def test_overriding_through_the_queue_relabels(world) -> None:
    unknown = item(world, ReviewReason.UNKNOWN_LABEL)
    queue(world).resolve(
        project_id=world["pid"],
        item_id=unknown.id,
        resolution=ReviewResolution.OVERRIDDEN,
        note="an audit trail is audit/reporting and regulatory",
        categories=[C.AUDIT_REPORTING, C.REGULATORY],
    )
    world["session"].refresh(unknown)
    assert unknown.resolution is ReviewResolution.OVERRIDDEN
    current = ClassificationService(world["session"], world["analyst"]).current(
        world["pid"], unknown.requirement_version_id
    )
    assert {label.category for label in current} == {C.AUDIT_REPORTING, C.REGULATORY}


@pytest.mark.parametrize(
    ("reason", "resolution", "message"),
    [
        (ReviewReason.LOW_EXTRACTION_SIGNAL, ReviewResolution.MERGED, "may be resolved as"),
        (ReviewReason.LOW_EXTRACTION_SIGNAL, ReviewResolution.REJECTED, "needs a note"),
        (ReviewReason.UNRESOLVED_SOURCE, ReviewResolution.ACCEPTED, "acknowledged"),
    ],
)
def test_resolutions_must_fit_the_item(world, reason, resolution, message) -> None:
    with pytest.raises(ReviewError, match=message):
        queue(world).resolve(
            project_id=world["pid"], item_id=item(world, reason).id, resolution=resolution
        )


def test_an_item_is_resolved_once(world) -> None:
    rejected = item(world, ReviewReason.UNRESOLVED_SOURCE)
    queue(world).resolve(
        project_id=world["pid"], item_id=rejected.id, resolution=ReviewResolution.ACKNOWLEDGED
    )
    with pytest.raises(ReviewError, match="already resolved"):
        queue(world).resolve(
            project_id=world["pid"], item_id=rejected.id, resolution=ReviewResolution.ACKNOWLEDGED
        )


def test_reviewing_roles_read_the_queue_but_only_an_analyst_resolves(world) -> None:
    assert queue(world, "officer").list(world["pid"])
    assert queue(world, "auditor").list(world["pid"])
    with pytest.raises(AuthorizationError):
        queue(world, "stakeholder").list(world["pid"])
    rejected = item(world, ReviewReason.UNRESOLVED_SOURCE)
    for who in ("officer", "auditor"):
        with pytest.raises(AuthorizationError):
            queue(world, who).resolve(
                project_id=world["pid"],
                item_id=rejected.id,
                resolution=ReviewResolution.ACKNOWLEDGED,
            )


def test_resolutions_are_audited(world) -> None:
    rejected = item(world, ReviewReason.UNRESOLVED_SOURCE)
    queue(world).resolve(
        project_id=world["pid"],
        item_id=rejected.id,
        resolution=ReviewResolution.ACKNOWLEDGED,
        note="injected",
    )
    event = next(
        e
        for e in AuditService(world["session"]).list_for_project(world["project"].id)
        if e.event_type is AuditEventType.REVIEW_ITEM_RESOLVED
    )
    assert event.payload["resolution"] == "acknowledged" and event.payload["note_supplied"] is True
    assert "injected" not in str(event.payload)


# --- merging duplicates (FR-EXT-005) --------------------------------------------------


def test_a_human_merge_keeps_every_source_link(world) -> None:
    session, pid = world["session"], world["pid"]
    status, export = world["versions"]["FR-LOAN-002"], world["versions"]["FR-LOAN-003"]
    merged = RequirementMergeService(session, world["analyst"]).merge(
        project_id=pid,
        survivor_id=status.requirement_id,
        duplicate_id=export.requirement_id,
        reason="same need",
    )
    assert merged.version_no == 2 and merged.state is RequirementState.EXTRACTED
    assert merged.statement == status.statement and merged.original_text == status.original_text
    assert len(merged.source_refs) == len(status.source_refs) + len(export.source_refs)
    session.refresh(export)
    assert export.state is RequirementState.WITHDRAWN
    assert export.source_refs, "the duplicate keeps its own sources and wording"
    event = next(
        e
        for e in AuditService(session).list_for_project(world["project"].id)
        if e.event_type is AuditEventType.REQUIREMENTS_MERGED
    )
    assert (event.payload["survivor"], event.payload["duplicate"]) == ("FR-LOAN-002", "FR-LOAN-003")


def test_a_merge_carries_the_criteria_forward_as_copies(world) -> None:
    session, pid = world["session"], world["pid"]
    upload, export = world["versions"]["FR-LOAN-001"], world["versions"]["FR-LOAN-003"]
    merged = RequirementMergeService(session, world["analyst"]).merge(
        project_id=pid,
        survivor_id=upload.requirement_id,
        duplicate_id=export.requirement_id,
        reason="r",
    )
    (copy,) = session.scalars(
        select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_version_id == merged.id)
    ).all()
    assert copy.copied_from_id is not None and copy.given_text.startswith("an applicant")


def test_merging_through_the_queue(world) -> None:
    session, pid = world["session"], world["pid"]
    status, export = world["versions"]["FR-LOAN-002"], world["versions"]["FR-LOAN-003"]
    system = pipeline_actor(world["analyst"], pid, world["summary"].run_id)
    duplicate = ReviewQueue(session, system).raise_item(  # as a later run would raise it
        project_id=pid,
        reason=ReviewReason.POSSIBLE_DUPLICATE,
        subject_type="requirement_version",
        subject_id=export.id,
        requirement_version_id=export.id,
        related_subject_id=status.id,
        detail={"similarity": 0.5, "basis": "test"},
    )
    queue(world).resolve(
        project_id=pid,
        item_id=duplicate.id,
        resolution=ReviewResolution.MERGED,
        note="the same need",
    )
    session.refresh(export)
    assert export.state is RequirementState.WITHDRAWN


def test_merging_is_human_only_and_pre_approval_only(world) -> None:
    session, pid = world["session"], world["pid"]
    upload, export = world["versions"]["FR-LOAN-001"], world["versions"]["FR-LOAN-003"]
    system = pipeline_actor(world["analyst"], pid, world["summary"].run_id)
    with pytest.raises(AuthorizationError):
        RequirementMergeService(session, system).merge(
            project_id=pid,
            survivor_id=upload.requirement_id,
            duplicate_id=export.requirement_id,
            reason="r",
        )
    RequirementService(session, world["analyst"]).transition(
        project_id=pid, version_id=upload.id, target=RequirementState.ANALYZED
    )
    with pytest.raises(ExtractionError, match="can be merged"):
        RequirementMergeService(session, world["analyst"]).merge(
            project_id=pid,
            survivor_id=upload.requirement_id,
            duplicate_id=export.requirement_id,
            reason="r",
        )


# --- the queue is not approval ------------------------------------------------------------


def test_resolving_every_item_approves_nothing(world) -> None:
    session, pid = world["session"], world["pid"]
    service = queue(world)
    for review in service.list(pid, status=ReviewStatus.OPEN):
        choice = {
            ReviewReason.UNRESOLVED_SOURCE: ReviewResolution.ACKNOWLEDGED,
            ReviewReason.LOW_EXTRACTION_SIGNAL: ReviewResolution.ACCEPTED,
            ReviewReason.LOW_CLASSIFICATION_SIGNAL: ReviewResolution.ACCEPTED,
            ReviewReason.UNKNOWN_LABEL: ReviewResolution.ACKNOWLEDGED,
        }[review.reason]
        service.resolve(project_id=pid, item_id=review.id, resolution=choice)
    assert service.list(pid, status=ReviewStatus.OPEN) == []
    assert session.scalars(select(ApprovalTask)).all() == []
    states = {v.state for v in world["versions"].values()}
    assert states == {RequirementState.CLASSIFIED}


def test_g1_is_unchanged_for_an_extracted_requirement(world) -> None:
    """A P3 requirement reaches approval only by the P1 path, with both roles."""
    session, pid = world["session"], world["pid"]
    upload = world["versions"]["FR-LOAN-001"]
    requirements = RequirementService(session, world["analyst"])
    for target in (RequirementState.ANALYZED, RequirementState.VALIDATED):
        requirements.transition(project_id=pid, version_id=upload.id, target=target)
    tasks = ApprovalService(session, world["analyst"]).submit_versions_for_baseline(
        project_id=pid, version_ids=[upload.id]
    )
    assert {t.required_role for t in tasks} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}


def test_the_pipeline_can_never_move_a_version_beyond_classification(world) -> None:
    system = pipeline_actor(world["analyst"], world["pid"], world["summary"].run_id)
    with pytest.raises(AuthorizationError, match="only to"):
        RequirementService(world["session"], system).transition(
            project_id=world["pid"],
            version_id=world["versions"]["FR-LOAN-001"].id,
            target=RequirementState.ANALYZED,
        )


# --- immutability of the new records ------------------------------------------------------


def test_a_label_cannot_be_edited(world) -> None:
    session = world["session"]
    label = session.scalars(select(RequirementClassification)).first()
    label.category = C.SECURITY
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


def test_a_criterion_cannot_be_edited(world) -> None:
    session = world["session"]
    criterion = session.scalars(select(AcceptanceCriterion)).first()
    criterion.then_text = "anything at all"
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


def test_a_proposal_cannot_be_rewritten(world) -> None:
    session = world["session"]
    candidate = session.scalars(select(ExtractionCandidate)).first()
    candidate.statement = "The system shall do something else."
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


def test_a_decided_candidate_cannot_be_decided_again(world) -> None:
    session = world["session"]
    accepted = session.scalars(
        select(ExtractionCandidate).where(ExtractionCandidate.status == CandidateStatus.ACCEPTED)
    ).first()
    accepted.status = CandidateStatus.REJECTED
    with pytest.raises(ImmutableRecordError, match="decided once"):
        session.flush()
    session.rollback()
