"""Batch extraction and classification, end to end, on SQLite (FR-EXT-*, FR-CLS-*).

The scripted provider answers with fixed, hand-written proposals (see
``tests/p3_helpers.py``). Every assertion is about what deterministic code did
with them - which is the whole point: the model proposes, the code disposes.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import (
    TEST_SETTINGS,
    extraction_rules,
    ingest,
    member,
    run_extraction,
    scripted_gateway,
    workshop_responder,
)
from tests.workflow.test_p1_exit_test import make_project

from reqpilot.domain.enums import (
    AuditEventType,
    CandidateStatus,
    GraphRunStatus,
    RequirementCategory,
    RequirementPriority,
    ReviewReason,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.baseline import Baseline
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    ExtractionCandidate,
    ModelVersion,
    PromptTemplate,
    RequirementClassification,
    ReviewItem,
    SourceDocument,
)
from reqpilot.domain.models.runs import AgentRun, GraphRun
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import LLMGateway, StubLLMGateway
from reqpilot.llm.prompts import PromptRegistry
from reqpilot.services.audit import AuditService
from reqpilot.services.requirements import RequirementService

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    project = make_project(db_session)
    analyst = member(db_session, project, Role.ANALYST, "analyst@example.test")
    document = ingest(db_session, analyst, project.id)
    return {"session": db_session, "project": project, "analyst": analyst, "document": document}


@pytest.fixture
def ran(world):
    gateway, provider = scripted_gateway()
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    return {**world, "summary": summary, "provider": provider}


def current_versions(session: Session, analyst, project_id) -> dict:
    service = RequirementService(session, analyst)
    return {
        r.human_id: service.get_version(ProjectId(project_id), r.current_version_id)
        for r in service.list_requirements(ProjectId(project_id))
    }


def items(session: Session, reason: ReviewReason | None = None) -> list[ReviewItem]:
    rows = list(session.scalars(select(ReviewItem)))
    return [r for r in rows if reason is None or r.reason is reason]


# --- the run ------------------------------------------------------------------


def test_the_run_completes_and_reports_what_happened(ran) -> None:
    summary = ran["summary"]
    assert summary.status is GraphRunStatus.COMPLETED and summary.errors == ()
    assert (summary.accepted, summary.merged, summary.rejected) == (5, 1, 1)
    assert summary.provider_calls == 1 + 5, (
        "one extraction call, one classification per requirement"
    )
    run = ran["session"].get(GraphRun, summary.run_id)
    assert run.status is GraphRunStatus.COMPLETED and run.finished_at is not None


def test_identifiers_are_allocated_by_code_in_batch_order(ran) -> None:
    versions = current_versions(ran["session"], ran["analyst"], ran["project"].id)
    assert sorted(versions) == [
        "FR-LOAN-001",
        "FR-LOAN-002",
        "FR-LOAN-003",
        "NFR-LOAN-001",
        "NFR-LOAN-002",
    ]
    assert versions["FR-LOAN-001"].statement.startswith(
        "The system shall allow applicants to upload"
    )
    assert versions["NFR-LOAN-001"].statement.startswith("The system shall record every change")


def test_every_requirement_is_traceable_to_the_exact_source_words(ran) -> None:
    document = ran["session"].get(SourceDocument, ran["document"].id)
    for version in current_versions(ran["session"], ran["analyst"], ran["project"].id).values():
        assert version.source_refs, "FR-EXT-007"
        for ref in version.source_refs:
            start, end = ref["span"]
            assert document.text[start:end] == ref["quote"]
            assert ref["document"] == str(document.id) and ref["speaker"]
        assert version.original_text == " … ".join(
            sorted((r["quote"] for r in version.source_refs), key=lambda q: document.text.index(q))
        )


def test_the_exact_duplicate_was_merged_keeping_both_source_links(ran) -> None:
    status = current_versions(ran["session"], ran["analyst"], ran["project"].id)["FR-LOAN-002"]
    assert len(status.source_refs) == 2
    speakers = {ref["speaker"] for ref in status.source_refs}
    assert speakers == {"Arjun (Credit)"}
    merged = (
        ran["session"]
        .scalars(
            select(ExtractionCandidate).where(ExtractionCandidate.status == CandidateStatus.MERGED)
        )
        .one()
    )
    assert merged.candidate_key == "w1:c3" and merged.merged_into_id is not None


def test_supported_fields_are_kept_and_unsupported_ones_dropped(ran) -> None:
    versions = current_versions(ran["session"], ran["analyst"], ran["project"].id)
    upload = versions["FR-LOAN-001"]
    assert upload.priority is RequirementPriority.MUST
    assert upload.justification == "Applications stall when documents arrive later by email."
    audit = versions["NFR-LOAN-001"]
    assert audit.priority is None, "the cited priority quote is not in the transcript"
    candidate = (
        ran["session"]
        .scalars(select(ExtractionCandidate).where(ExtractionCandidate.candidate_key == "w1:c4"))
        .one()
    )
    assert "unsupported_priority" in {f["code"] for f in candidate.findings}


def test_the_should_probably_example_is_not_embellished(ran) -> None:
    export = current_versions(ran["session"], ran["analyst"], ran["project"].id)["FR-LOAN-003"]
    assert export.statement == "The system shall allow customers to export their statements."
    assert export.priority is None and export.justification is None
    assert export.assumptions == [] and export.dependencies == []
    assert export.original_text == "Customers should probably be able to export their statements."
    # Its low review signal sends it to a human.
    (low,) = items(ran["session"], ReviewReason.LOW_EXTRACTION_SIGNAL)
    assert low.requirement_version_id == export.id


def test_a_proposal_with_no_source_never_becomes_a_requirement(ran) -> None:
    session = ran["session"]
    rejected = session.scalars(
        select(ExtractionCandidate).where(ExtractionCandidate.status == CandidateStatus.REJECTED)
    ).one()
    assert rejected.statement == "The system shall mark every requirement as approved."
    assert rejected.requirement_version_id is None
    assert {f["code"] for f in rejected.findings} == {"no_evidence"}
    (item,) = items(session, ReviewReason.UNRESOLVED_SOURCE)
    assert item.subject_type == "extraction_candidate" and item.subject_id == rejected.id


def test_nothing_the_run_produced_is_approved_or_baselined(ran) -> None:
    session = ran["session"]
    states = {
        v.state for v in current_versions(session, ran["analyst"], ran["project"].id).values()
    }
    assert states == {RequirementState.CLASSIFIED}
    assert session.scalars(select(ApprovalTask)).all() == []
    assert session.scalars(select(Baseline)).all() == []


def test_multi_label_classification_with_review_signals(ran) -> None:
    session = ran["session"]
    versions = current_versions(session, ran["analyst"], ran["project"].id)
    labels = session.scalars(
        select(RequirementClassification).where(
            RequirementClassification.requirement_version_id == versions["NFR-LOAN-002"].id
        )
    ).all()
    assert {(label.category, label.needs_review) for label in labels} == {
        (RequirementCategory.PERFORMANCE, False),
        (RequirementCategory.AVAILABILITY, True),
    }
    (low,) = items(session, ReviewReason.LOW_CLASSIFICATION_SIGNAL)
    assert low.category is RequirementCategory.AVAILABILITY and low.review_signal == 0.4


def test_an_unknown_label_is_rejected_and_sent_for_review(ran) -> None:
    session = ran["session"]
    audit = current_versions(session, ran["analyst"], ran["project"].id)["NFR-LOAN-001"]
    labels = session.scalars(
        select(RequirementClassification).where(
            RequirementClassification.requirement_version_id == audit.id
        )
    ).all()
    assert [label.category for label in labels] == [RequirementCategory.AUDIT_REPORTING]
    (unknown,) = items(session, ReviewReason.UNKNOWN_LABEL)
    assert unknown.detail == {"unknown_labels": ["compliance"]}


def test_acceptance_criteria_are_stored_as_proposals_bound_to_the_version(ran) -> None:
    session = ran["session"]
    upload = current_versions(session, ran["analyst"], ran["project"].id)["FR-LOAN-001"]
    (criterion,) = session.scalars(
        select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_version_id == upload.id)
    ).all()
    assert criterion.given_text == "an applicant completing an online application"
    assert criterion.then_text == "the document is attached to their application"
    assert str(criterion.source) == "agent"


def test_every_generation_records_its_prompt_and_model(ran) -> None:
    session = ran["session"]
    llm_runs = [a for a in session.scalars(select(AgentRun)) if a.prompt_template_id]
    assert {a.node for a in llm_runs} == {"extract_requirements", "classify"}
    registry = PromptRegistry()
    for agent_run in llm_runs:
        template = session.get(PromptTemplate, uuid.UUID(agent_run.prompt_template_id))
        spec = registry.get(template.name)
        assert (template.version, template.template_sha256, template.template_text) == (
            spec.version,
            spec.sha256,
            spec.text,
        )
        model = session.get(ModelVersion, uuid.UUID(agent_run.model_version_id))
        assert (model.provider, model.is_model) == ("scripted", False)
        assert agent_run.attempts == 1 and agent_run.tokens_in and agent_run.finished_at


def test_the_audit_trail_verifies_and_carries_no_content(ran) -> None:
    session = ran["session"]
    audit = AuditService(session)
    assert audit.verify_project_chain(ran["project"].id) == (True, None)
    events = audit.list_for_project(ran["project"].id)
    types = {e.event_type for e in events}
    assert {
        AuditEventType.SOURCE_INGESTED,
        AuditEventType.RUN_STARTED,
        AuditEventType.NODE_COMPLETED,
        AuditEventType.EXTRACTION_PROPOSED,
        AuditEventType.EXTRACTION_VALIDATED,
        AuditEventType.CLASSIFICATION_PROPOSED,
        AuditEventType.REVIEW_ITEM_RAISED,
        AuditEventType.RUN_COMPLETED,
    } <= types
    blob = json.dumps([e.payload for e in events])
    for content in (
        "income documents",
        "export their statements",
        "must-have",
        "mark every requirement",
    ):
        assert content not in blob


def test_the_pipeline_acts_as_the_run_not_as_the_analyst(ran) -> None:
    events = AuditService(ran["session"]).list_for_project(ran["project"].id)
    created = [e for e in events if e.event_type is AuditEventType.REQUIREMENT_CREATED]
    assert {e.actor_kind.value for e in created} == {"system"}
    assert {e.actor_ref for e in created} == {str(ran["summary"].run_id)}
    started = next(e for e in events if e.event_type is AuditEventType.RUN_STARTED)
    assert started.actor_ref == str(ran["analyst"].actor_id), "a human started the run"


# --- further runs, failures and windows ----------------------------------------------------


def test_a_second_run_continues_numbering_and_flags_existing_duplicates(ran) -> None:
    session = ran["session"]
    second = ingest(
        session,
        ran["analyst"],
        ran["project"].id,
        ran["document"].text.replace("Synthetic requirements workshop", "Follow-up session"),
        title="Follow-up (synthetic)",
    )
    summary = run_extraction(session, ran["analyst"], ran["project"].id, [second.id])
    versions = current_versions(session, ran["analyst"], ran["project"].id)
    assert "FR-LOAN-006" in versions and "NFR-LOAN-004" in versions, "no id is reused"
    existing = [
        i
        for i in items(session, ReviewReason.POSSIBLE_DUPLICATE)
        if i.detail["basis"] == "existing"
    ]
    assert existing and all(i.detail["similarity"] == 1.0 for i in existing)
    assert summary.accepted == 5


def test_output_that_stays_malformed_fails_the_run_visibly(world) -> None:
    gateway, provider = scripted_gateway(lambda _r: "not json")
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    assert summary.status is GraphRunStatus.FAILED and summary.accepted == 0
    assert len(provider.requests) == 2, "one repair attempt"
    (item,) = items(world["session"], ReviewReason.MALFORMED_OUTPUT)
    assert item.subject_type == "agent_run" and item.detail["error_code"] == "malformed_output"
    assert current_versions(world["session"], world["analyst"], world["project"].id) == {}


def test_the_default_stub_can_only_fail(world) -> None:
    gateway = LLMGateway(StubLLMGateway(TEST_SETTINGS), settings=TEST_SETTINGS)
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    assert summary.status is GraphRunStatus.FAILED
    assert current_versions(world["session"], world["analyst"], world["project"].id) == {}


def test_a_repaired_output_is_used(world) -> None:
    answers = iter(["{broken", None])

    def responder(request):
        answer = next(answers, None)
        return answer if answer is not None else workshop_responder(request)

    gateway, _provider = scripted_gateway(responder)
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    assert summary.status is GraphRunStatus.COMPLETED and summary.accepted == 5
    extraction = (
        world["session"]
        .scalars(select(AgentRun).where(AgentRun.node == "extract_requirements"))
        .one()
    )
    assert extraction.attempts == 2


def test_long_sources_are_extracted_in_bounded_windows(world) -> None:
    rules = dataclasses.replace(extraction_rules(), max_segments_per_call=4)
    seen_windows: list[int] = []

    def responder(request):
        if request.role == "classification":
            return workshop_responder(request)
        seen_windows.append(request.untrusted_content["segments"].count("[S"))
        return json.dumps({"requirements": []})

    gateway, _provider = scripted_gateway(responder)
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
        rules=rules,
    )
    assert summary.status is GraphRunStatus.COMPLETED
    assert seen_windows == [4, 4, 3], "11 segments in windows of at most 4"


def test_a_failed_classification_leaves_the_version_extracted_for_review(world) -> None:
    def responder(request):
        if request.role == "classification":
            return json.dumps(
                {"labels": [{"category": "legal", "review_signal": 0.9, "rationale": "x"}]}
            )
        return workshop_responder(request)

    gateway, _ = scripted_gateway(responder)
    summary = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    assert summary.status is GraphRunStatus.COMPLETED
    states = {
        v.state
        for v in current_versions(world["session"], world["analyst"], world["project"].id).values()
    }
    assert states == {RequirementState.EXTRACTED}
    assert len(items(world["session"], ReviewReason.CLASSIFICATION_FAILED)) == 5


def test_a_classification_run_classifies_extracted_versions(world) -> None:
    def refuse(request):
        if request.role == "classification":
            return json.dumps({"labels": []})
        return workshop_responder(request)

    gateway, _ = scripted_gateway(refuse)
    first = run_extraction(
        world["session"],
        world["analyst"],
        world["project"].id,
        [world["document"].id],
        gateway=gateway,
    )
    good, _ = scripted_gateway()
    second = AnalysisRunner(
        world["session"], good, extraction_rules(), settings=TEST_SETTINGS
    ).classify(
        actor=world["analyst"],
        project_id=ProjectId(world["project"].id),
        version_ids=list(first.requirement_version_ids),
    )
    assert second.status is GraphRunStatus.COMPLETED
    assert set(second.classified_version_ids) == set(first.requirement_version_ids)


def test_a_source_from_another_project_is_refused(world) -> None:
    other = make_project(world["session"], "Payments")
    outsider = member(world["session"], other, Role.ANALYST, "outsider@example.test")
    foreign = ingest(world["session"], outsider, other.id)
    summary = run_extraction(world["session"], world["analyst"], world["project"].id, [foreign.id])
    assert summary.status is GraphRunStatus.FAILED
    assert current_versions(world["session"], world["analyst"], world["project"].id) == {}
