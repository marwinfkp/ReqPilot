"""The P3 demonstration, and the honest state of the P3 roadmap exit.

**The demonstration** runs the whole batch path on the synthetic development
transcript with the scripted provider: source -> extraction -> validation ->
persistence -> classification -> review queue -> human override -> the P1 path
to G1. It proves every mechanism; it measures nothing, because the scripted
provider is not a model.

**The roadmap exit** is "E1 computed against gold transcript #1". The last test
looks for that gold set and, if it is not there, **skips** - visibly, with the
reason - rather than pretending. It also shows that the harness refuses to call
a scripted run E1 even against a gold set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from tests.p3_helpers import REPO_ROOT, ingest, member, run_extraction, workshop_text

from reqpilot.domain.enums import (
    GraphRunStatus,
    RequirementCategory,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.classification import ClassificationService
from reqpilot.services.evaluation.extraction_eval import (
    GOLD_TRANSCRIPT_ONE,
    compute_e1,
    load_gold_set,
    propose_pairs,
)
from reqpilot.services.evaluation.extraction_predictions import predictions_for_run
from reqpilot.services.extraction import RequirementRecordService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.review.service import ReviewQueueService

pytestmark = pytest.mark.workflow


def test_p3_demonstration(db_session: Session) -> None:
    from tests.workflow.test_p1_exit_test import make_project

    session = db_session
    project = make_project(session)
    pid = ProjectId(project.id)
    analyst = member(session, project, Role.ANALYST, "analyst@example.test")

    # 1. A synthetic transcript is added and segmented by speaker turn.
    document = ingest(session, analyst, project.id)

    # 2. One batch run: the model proposes, deterministic code disposes.
    summary = run_extraction(session, analyst, project.id, [document.id])
    assert summary.status is GraphRunStatus.COMPLETED
    assert (summary.accepted, summary.merged, summary.rejected) == (5, 1, 1)

    requirements = RequirementService(session, analyst)
    by_id = {r.human_id: r for r in requirements.list_requirements(pid)}
    record = RequirementRecordService(session, analyst).record(pid, by_id["FR-LOAN-001"].id)
    assert record.version.state is RequirementState.CLASSIFIED
    assert record.stakeholders == ("Priya (Operations)",)
    assert record.version.priority is not None and record.criteria
    for ref in record.version.source_refs:
        start, end = ref["span"]
        assert document.text[start:end] == ref["quote"]

    # 3. A human works the review queue - which approves nothing.
    queue = ReviewQueueService(session, analyst)
    for item in queue.list(pid, status=ReviewStatus.OPEN):
        if item.reason is ReviewReason.UNKNOWN_LABEL:
            queue.resolve(
                project_id=pid,
                item_id=item.id,
                resolution=ReviewResolution.OVERRIDDEN,
                note="an audit trail shown to auditors is audit/reporting and regulatory",
                categories=[RequirementCategory.AUDIT_REPORTING, RequirementCategory.REGULATORY],
            )
        elif item.reason in (
            ReviewReason.LOW_CLASSIFICATION_SIGNAL,
            ReviewReason.LOW_EXTRACTION_SIGNAL,
        ):
            queue.resolve(project_id=pid, item_id=item.id, resolution=ReviewResolution.ACCEPTED)
        else:
            queue.resolve(project_id=pid, item_id=item.id, resolution=ReviewResolution.ACKNOWLEDGED)
    assert queue.list(pid, status=ReviewStatus.OPEN) == []

    # 4. A human override is a new classification revision.
    upload = record.version
    ClassificationService(session, analyst).override(
        project_id=pid,
        version_id=upload.id,
        categories=[RequirementCategory.FUNCTIONAL, RequirementCategory.DATA_MANAGEMENT],
        reason="documents are data",
    )

    # 5. Approval is still G1, by the P1 path, with both roles.
    for target in (RequirementState.ANALYZED, RequirementState.VALIDATED):
        requirements.transition(project_id=pid, version_id=upload.id, target=target)
    tasks = ApprovalService(session, analyst).submit_versions_for_baseline(
        project_id=pid, version_ids=[upload.id]
    )
    assert {t.required_role for t in tasks} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}

    assert AuditService(session).verify_project_chain(project.id) == (True, None)


def _write_tiny_gold(root: Path, text: str) -> Path:
    """A throwaway gold set over the dev transcript - NOT gold transcript #1."""
    directory = root / "dev_harness_check_v1"
    (directory / "transcripts").mkdir(parents=True)
    (directory / "transcripts" / "workshop.md").write_text(text, encoding="utf-8")
    phrase = "Applicants must be able to upload their income documents when they apply online."
    start = text.index(phrase)
    row = {
        "gold_id": "G1",
        "transcript": "workshop.md",
        "char_start": start,
        "char_end": start + len(phrase),
        "statement": "The system shall allow applicants to upload income documents online.",
        "kind": "FR",
    }
    (directory / "requirements.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    files = {
        p.relative_to(directory).as_posix(): file_canonical_sha256(p)
        for p in directory.rglob("*")
        if p.is_file()
    }
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "name": "dev-harness-check",
                "version": "1",
                "frozen_at": "-",
                "frozen_by": "test",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_a_scripted_run_is_never_reported_as_e1(db_session: Session, tmp_path: Path) -> None:
    from tests.workflow.test_p1_exit_test import make_project

    project = make_project(db_session)
    analyst = member(db_session, project, Role.ANALYST, "analyst@example.test")
    document = ingest(db_session, analyst, project.id)
    summary = run_extraction(db_session, analyst, project.id, [document.id])

    gold = load_gold_set(_write_tiny_gold(tmp_path, workshop_text().replace("\r\n", "\n")))
    predictions, facts = predictions_for_run(
        db_session, analyst, ProjectId(project.id), summary.run_id, gold
    )
    assert len(predictions) == 5 and facts.all_real_models is False
    assert {p.transcript for p in predictions} == {"workshop.md"}, "sources matched by content hash"
    pairs = propose_pairs(gold, predictions)
    assert [p.prediction_ref for p in pairs] == ["FR-LOAN-001"]

    report = compute_e1(gold, predictions, [], facts)
    assert not report.counts_as_e1 and report.f1 is None
    assert any("real model" in r for r in report.reasons_not_e1)
    assert any("gold transcript #1" in r for r in report.reasons_not_e1)


def test_gold_transcript_one_for_the_p3_roadmap_exit() -> None:
    """The P3 exit needs a frozen, team-annotated gold transcript #1 and a real model.

    The real model now exists (the OpenAI provider, selected at P3 closure). The gold
    set does not: it is human work (Phase 0 O.1, O.3; data/gold/README.md). This test
    skips until the set is added, and then verifies it is frozen and declared as gold
    transcript #1.
    """
    gold_root = REPO_ROOT / "data" / "gold"
    candidates = [
        manifest.parent
        for manifest in gold_root.glob("*/manifest.json")
        if json.loads(manifest.read_text(encoding="utf-8")).get("role") == GOLD_TRANSCRIPT_ONE
    ]
    if not candidates:
        pytest.skip(
            "gold transcript #1 has not been authored and frozen (data/gold/); "
            "E1 - the P3 roadmap exit - is PENDING"
        )
    gold = load_gold_set(candidates[0])
    assert gold.is_gold_transcript_one and gold.requirements
