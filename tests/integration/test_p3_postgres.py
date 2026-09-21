"""P3 on a live PostgreSQL: the pipeline, the id lock, and the database triggers.

The ORM guards are one layer; these tests attack the second - the triggers of
migration ``0005`` - with raw SQL that bypasses the ORM entirely. Skips without
``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.p3_helpers import ingest, member, run_extraction
from tests.workflow.test_p1_exit_test import make_project

from reqpilot.domain.enums import GraphRunStatus, Role
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.extraction import ExtractionCandidate, ReviewItem
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.services.audit import AuditService

pytestmark = pytest.mark.integration


@pytest.fixture
def ran(pg_session: Session):
    session = pg_session
    project = make_project(session, "P3 on PostgreSQL")
    analyst = member(session, project, Role.ANALYST, "pg-p3-analyst@example.test")
    document = ingest(session, analyst, project.id)
    summary = run_extraction(session, analyst, project.id, [document.id])
    return {
        "session": session,
        "project": project,
        "analyst": analyst,
        "document": document,
        "summary": summary,
    }


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
    savepoint.rollback()
    return str(excinfo.value)


def test_the_pipeline_runs_on_postgresql(ran) -> None:
    summary = ran["summary"]
    assert summary.status is GraphRunStatus.COMPLETED
    assert (summary.accepted, summary.merged, summary.rejected) == (5, 1, 1)
    states = {v.state for v in ran["session"].scalars(select(RequirementVersion))}
    assert states == {RequirementState.CLASSIFIED}
    assert AuditService(ran["session"]).verify_project_chain(ran["project"].id) == (True, None)


def test_a_second_run_allocates_after_the_first_under_the_lock(ran) -> None:
    session = ran["session"]
    second = ingest(
        session,
        ran["analyst"],
        ran["project"].id,
        ran["document"].text + "\nSam (IT): Nothing else.",
    )
    run_extraction(session, ran["analyst"], ran["project"].id, [second.id])
    ids = sorted(
        session.scalars(
            text("SELECT human_id FROM requirement WHERE project_id = :p"), {"p": ran["project"].id}
        )
    )
    assert len(ids) == len(set(ids)) == 10


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE source_document SET title = 'x' WHERE project_id = :p",
        "DELETE FROM source_chunk WHERE project_id = :p",
        "UPDATE requirement_classification SET category = 'SECURITY' WHERE project_id = :p",
        "DELETE FROM acceptance_criterion WHERE project_id = :p",
    ],
)
def test_project_content_is_append_only_in_the_database(ran, sql) -> None:
    assert "append-only" in refused(ran["session"], sql, {"p": ran["project"].id})


def test_provenance_records_are_append_only_in_the_database(ran) -> None:
    for sql in (
        "UPDATE prompt_template SET template_text = 'rewritten'",
        "DELETE FROM model_version",
    ):
        assert "append-only" in refused(ran["session"], sql)


def test_a_proposal_cannot_be_rewritten_or_redecided_in_the_database(ran) -> None:
    session = ran["session"]
    candidate = session.scalars(select(ExtractionCandidate)).first()
    assert "immutable" in refused(
        session,
        "UPDATE extraction_candidate SET statement = 'x' WHERE id = :i",
        {"i": candidate.id},
    )
    assert "decided once" in refused(
        session,
        "UPDATE extraction_candidate SET findings = '[]'::jsonb WHERE id = :i",
        {"i": candidate.id},
    )
    assert "never deleted" in refused(
        session, "DELETE FROM extraction_candidate WHERE id = :i", {"i": candidate.id}
    )


def test_a_review_item_resolves_once_and_keeps_its_subject(ran) -> None:
    session = ran["session"]
    item = session.scalars(select(ReviewItem)).first()
    assert "immutable" in refused(
        session, "UPDATE review_item SET detail = '{}'::jsonb WHERE id = :i", {"i": item.id}
    )
    session.execute(
        text(
            "UPDATE review_item SET status = 'RESOLVED', resolution = 'ACKNOWLEDGED', "
            "resolved_by = :u, resolved_at = now() WHERE id = :i"
        ),
        {"i": item.id, "u": ran["analyst"].actor_id},
    )
    assert "resolved once" in refused(
        session,
        "UPDATE review_item SET resolution = 'ACCEPTED' WHERE id = :i",
        {"i": item.id},
    )


def test_the_p3_tables_and_triggers_exist(ran) -> None:
    triggers = set(
        ran["session"].scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    )
    assert {
        "source_document_append_only",
        "source_chunk_append_only",
        "requirement_classification_append_only",
        "acceptance_criterion_append_only",
        "prompt_template_append_only",
        "model_version_append_only",
        "extraction_candidate_guard",
        "review_item_guard",
    } <= triggers
