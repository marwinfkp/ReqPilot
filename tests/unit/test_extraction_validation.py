"""Deterministic validation of extraction proposals (architecture F.2; FR-EXT-001..007).

Pure: segments and proposals are built in memory; no database, no model.
"""

from __future__ import annotations

import uuid

import pytest
from tests.p3_helpers import extraction_rules

from reqpilot.agents.contracts.extraction import ExtractedRequirement, SegmentView
from reqpilot.agents.validation import RecordedProposal, decide, validate_proposal
from reqpilot.domain.enums import RequirementPriority, ReviewReason
from reqpilot.domain.proposals import FindingCode, RejectedCandidate, ValidatedCandidate
from reqpilot.domain.requirement_ids import RequirementKind

pytestmark = pytest.mark.unit

RULES = extraction_rules()
DOC = uuid.uuid4()
S1_TEXT = "Applicants must be able to upload their income documents. That is a must-have for us."
S2_TEXT = "Half of our applications stall because the documents arrive later by email."
SEGMENTS = {
    "S1": SegmentView("S1", uuid.uuid4(), DOC, 100, S1_TEXT, "Priya", False, True),
    "S2": SegmentView("S2", uuid.uuid4(), DOC, 300, S2_TEXT, "Priya", False, True),
}


def proposal(key: str = "c1", **fields) -> RecordedProposal:
    base = {
        "candidate_key": key,
        "statement": "The system shall allow applicants to upload their income documents.",
        "requirement_type": "functional",
        "evidence": [{"segment_id": "S1", "quote": "upload their income documents"}],
        "review_signal": 0.9,
    }
    base.update(fields)
    return RecordedProposal(
        candidate_id=uuid.uuid4(),
        run_key=f"w1:{key}",
        model_key=key,
        ordinal=int(key[1:]) if key[1:].isdigit() else 0,
        requirement=ExtractedRequirement.model_validate(base),
    )


def validate(p: RecordedProposal, keys: dict[str, str] | None = None):
    return validate_proposal(
        p, segments=SEGMENTS, key_map=keys or {p.model_key: p.run_key}, rules=RULES
    )


def codes(outcome) -> set[str]:
    return {str(f.code) for f in outcome.findings}


# --- provenance (FR-EXT-001, FR-EXT-007) ------------------------------------------


def test_a_supported_proposal_resolves_to_exact_document_offsets() -> None:
    outcome = validate(proposal())
    assert isinstance(outcome, ValidatedCandidate)
    (span,) = outcome.spans
    start = 100 + S1_TEXT.index("upload")
    assert (span.char_start, span.char_end) == (start, start + len("upload their income documents"))
    assert span.speaker == "Priya" and span.document_id == DOC
    assert outcome.original_text == "upload their income documents"
    assert outcome.kind is RequirementKind.FUNCTIONAL


def test_no_evidence_means_no_requirement() -> None:
    outcome = validate(proposal(evidence=[]))
    assert isinstance(outcome, RejectedCandidate)
    assert outcome.reason is ReviewReason.UNRESOLVED_SOURCE
    assert codes(outcome) == {"no_evidence"}


@pytest.mark.parametrize(
    ("evidence", "code"),
    [
        ([{"segment_id": "S9", "quote": "upload their income documents"}], "unknown_segment"),
        ([{"segment_id": "S2", "quote": "upload their income documents"}], "quote_not_found"),
        ([{"segment_id": "S1", "quote": "upload their"}], "quote_too_short"),
        ([{"segment_id": "S1", "quote": "upload their tax returns quickly"}], "quote_not_found"),
    ],
)
def test_evidence_that_does_not_resolve_is_rejected(evidence, code) -> None:
    outcome = validate(proposal(evidence=evidence))
    assert isinstance(outcome, RejectedCandidate) and code in codes(outcome)


def test_partly_unresolved_evidence_keeps_the_resolved_source_and_flags_the_rest() -> None:
    outcome = validate(
        proposal(
            evidence=[
                {"segment_id": "S1", "quote": "upload their income documents"},
                {"segment_id": "S2", "quote": "words that are not there at all"},
            ]
        )
    )
    assert isinstance(outcome, ValidatedCandidate)
    assert len(outcome.spans) == 1 and outcome.dropped_evidence is True


def test_a_validated_candidate_cannot_exist_without_a_source() -> None:
    with pytest.raises(ValueError, match="FR-EXT-007"):
        ValidatedCandidate(
            candidate_id=uuid.uuid4(),
            candidate_key="w1:c1",
            ordinal=0,
            kind=RequirementKind.FUNCTIONAL,
            statement="The system shall x.",
            spans=(),
            original_text="",
            review_signal=0.5,
        )


# --- the statement (FR-EXT-003) -------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["Applicants can upload documents.", "the system shall upload.", "The system should upload."],
)
def test_a_statement_not_in_declarative_form_is_rejected(statement) -> None:
    outcome = validate(proposal(statement=statement))
    assert isinstance(outcome, RejectedCandidate)
    assert outcome.reason is ReviewReason.EXTRACTION_INVALID
    assert "statement_not_declarative" in codes(outcome)


def test_whitespace_in_a_statement_is_normalised() -> None:
    outcome = validate(proposal(statement="The system shall   allow\n uploads."))
    assert isinstance(outcome, ValidatedCandidate)
    assert outcome.statement == "The system shall allow uploads."


# --- optional fields: supported or dropped, never invented --------------------------


def test_supported_priority_and_justification_are_kept() -> None:
    outcome = validate(
        proposal(
            priority={"value": "must", "segment_id": "S1", "quote": "That is a must-have for us."},
            justification={
                "text": "Applications stall.",
                "segment_id": "S2",
                "quote": "applications stall because the documents arrive later",
            },
            assumptions=[
                {"text": "Email exists.", "segment_id": "S2", "quote": "arrive later by email"}
            ],
        )
    )
    assert isinstance(outcome, ValidatedCandidate)
    assert outcome.priority is RequirementPriority.MUST
    assert outcome.justification == "Applications stall."
    assert outcome.assumptions == ("Email exists.",)


def test_unsupported_optional_fields_are_dropped_not_kept() -> None:
    outcome = validate(
        proposal(
            priority={"value": "must", "segment_id": "S1", "quote": "our number one priority"},
            justification={
                "text": "Regulator X requires it.",
                "segment_id": "S2",
                "quote": "as the regulator requires",
            },
            assumptions=[
                {"text": "PDF format.", "segment_id": "S1", "quote": "in PDF format only"}
            ],
        )
    )
    assert isinstance(outcome, ValidatedCandidate)
    assert outcome.priority is None and outcome.justification is None and outcome.assumptions == ()
    assert {"unsupported_priority", "unsupported_justification", "unsupported_assumption"} <= codes(
        outcome
    )


def test_the_contract_has_no_field_for_regulations_risk_or_an_identifier() -> None:
    fields = set(ExtractedRequirement.model_fields)
    for authority in (
        "applicable_regulations",
        "risk_level",
        "approval_status",
        "state",
        "human_id",
        "id",
        "category",
    ):
        assert authority not in fields


# --- references ----------------------------------------------------------------------


def test_dependencies_must_name_another_proposal_in_the_batch() -> None:
    a = proposal("c1", depends_on=["c2", "c9", "c1"])
    outcome = validate(a, {"c1": "w1:c1", "c2": "w1:c2"})
    assert isinstance(outcome, ValidatedCandidate)
    assert outcome.depends_on_keys == ("w1:c2",)
    assert "unknown_dependency" in codes(outcome)


def test_a_reused_key_is_rejected() -> None:
    p = proposal()
    reused = RecordedProposal(p.candidate_id, "w1:c1#2", "c1", 1, p.requirement, key_collision=True)
    outcome = validate(reused)
    assert isinstance(outcome, RejectedCandidate) and "duplicate_candidate_key" in codes(outcome)


# --- acceptance criteria (FR-EXT-006) ---------------------------------------------------


def test_valid_criteria_are_kept_in_given_when_then_form() -> None:
    outcome = validate(
        proposal(
            acceptance_criteria=[
                {"given": "an applicant", "when": "they upload", "then": "it is stored"}
            ]
        )
    )
    assert isinstance(outcome, ValidatedCandidate)
    (criterion,) = outcome.criteria
    assert (criterion.given, criterion.when, criterion.then) == (
        "an applicant",
        "they upload",
        "it is stored",
    )


def test_invalid_criteria_are_dropped_whole_and_flagged_not_repaired() -> None:
    too_many = [{"given": "g", "when": "w", "then": "t"}] * (RULES.max_criteria_per_requirement + 1)
    outcome = validate(proposal(acceptance_criteria=too_many))
    assert isinstance(outcome, ValidatedCandidate)
    assert outcome.criteria == () and outcome.criteria_rejected
    blank = validate(proposal(acceptance_criteria=[{"given": "g", "when": "  ", "then": "t"}]))
    assert isinstance(blank, ValidatedCandidate) and blank.criteria_rejected
    assert "criterion_invalid" in codes(blank)


def test_a_low_review_signal_is_flagged_for_review() -> None:
    outcome = validate(proposal(review_signal=0.2))
    assert isinstance(outcome, ValidatedCandidate) and outcome.low_signal
    assert FindingCode.LOW_REVIEW_SIGNAL in {f.code for f in outcome.findings}


# --- duplicates across the run (FR-EXT-005) -----------------------------------------------


def two(**second) -> list:
    first = proposal("c1")
    other = proposal(
        "c2",
        evidence=[{"segment_id": "S1", "quote": "That is a must-have for us"}],
        **second,
    )
    return [([first, other], SEGMENTS)]


def test_exact_duplicates_are_merged_keeping_every_source_span() -> None:
    decision = decide(two(), RULES)
    assert len(decision.accepted) == 1 and len(decision.merged) == 1
    (kept,) = decision.accepted
    assert len(kept.spans) == 2, "both sources survive the merge"
    assert decision.merged[0][1] == kept.candidate_id


def test_a_different_kind_is_never_merged() -> None:
    decision = decide(two(requirement_type="non_functional"), RULES)
    assert len(decision.accepted) == 2 and decision.merged == ()


def test_similar_but_different_statements_go_to_a_human_not_a_merge() -> None:
    decision = decide(
        two(statement="The system shall not allow applicants to upload their income documents."),
        RULES,
    )
    assert len(decision.accepted) == 2 and decision.merged == ()
    (pair,) = decision.near_duplicates
    assert pair.basis == "similarity" and pair.similarity >= RULES.duplicate_review_similarity


def test_a_model_proposed_duplicate_is_flagged_not_merged() -> None:
    decision = decide(
        two(statement="The system shall retain uploaded files.", duplicate_of=["c1"]), RULES
    )
    assert len(decision.accepted) == 2
    assert [p.basis for p in decision.near_duplicates] == ["model"]


def test_a_dependency_on_a_merged_duplicate_points_at_the_survivor() -> None:
    first = proposal("c1")
    twin = proposal("c2", evidence=[{"segment_id": "S1", "quote": "That is a must-have for us"}])
    dependent = proposal(
        "c3",
        statement="The system shall scan uploaded documents.",
        evidence=[{"segment_id": "S2", "quote": "the documents arrive later by email"}],
        depends_on=["c2"],
    )
    decision = decide([([first, twin, dependent], SEGMENTS)], RULES)
    by_key = {c.candidate_key: c for c in decision.accepted}
    assert by_key["w1:c3"].depends_on_keys == ("w1:c1",)
