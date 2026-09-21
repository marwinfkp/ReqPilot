"""Deterministic validation of extraction proposals (architecture F.2, E #3).

The five stages of F.2, as they apply to the Requirement Extraction role:

1. **Schema** - done by the gateway: an output that is not a valid
   :class:`ExtractionOutput` never reaches this module.
2. **Referential integrity** - every segment id, dependency and duplicate
   reference must name something that exists in this batch.
3. **Provenance** (``FR-EXT-007``) - every requirement needs at least one quote
   that is found, by code, in the segment it names. No resolved source, no
   requirement. Optional fields (justification, priority, assumptions) survive
   only with a resolving quote of their own; otherwise they are dropped, never
   guessed.
4. **Role rules** - the declarative "The system shall ..." form
   (``FR-EXT-003``), length limits, and acceptance-criterion shape
   (``FR-EXT-006``). Invalid criteria are dropped, not repaired.
5. **Capability** - the extraction role writes nothing. Its output reaches the
   database only through the persistence service, as the types in
   :mod:`reqpilot.domain.proposals`.

Near-duplicates (``FR-EXT-005``) are handled after validation, over the whole
run: exact duplicates are merged with every source span kept; anything merely
similar is left for a human.

Pure functions: no database, no model, no clock.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from reqpilot.agents.contracts.extraction import (
    ExtractedRequirement,
    ProposedCriterion,
    SegmentView,
)
from reqpilot.domain.enums import RequirementPriority, ReviewReason
from reqpilot.domain.proposals import (
    CriterionDraft,
    ExtractionDecision,
    Finding,
    FindingCode,
    NearDuplicate,
    RejectedCandidate,
    ValidatedCandidate,
)
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.domain.similarity import is_exact_duplicate, token_jaccard
from reqpilot.domain.source_spans import (
    ResolvedSpan,
    original_wording,
    resolve_in_segment,
    word_count,
)
from reqpilot.rules.extraction import ExtractionRules

#: A shorter quote could be found almost anywhere, which would make it
#: evidence of nothing.
MIN_QUOTE_WORDS = 3

_KIND = {"functional": RequirementKind.FUNCTIONAL, "non_functional": RequirementKind.NON_FUNCTIONAL}


@dataclass(frozen=True)
class RecordedProposal:
    """One proposal as recorded before validation.

    ``run_key`` is unique within the run (the model's key, namespaced by its
    extraction window); ``model_key`` is what the model called it, and is what
    its references to other proposals use.
    """

    candidate_id: uuid.UUID
    run_key: str
    model_key: str
    ordinal: int
    requirement: ExtractedRequirement
    #: The model used this key for an earlier proposal in the same output.
    key_collision: bool = False


def _resolve(
    segments: Mapping[str, SegmentView], segment_id: str, quote: str, rules: ExtractionRules
) -> tuple[ResolvedSpan | None, Finding | None]:
    segment = segments.get(segment_id)
    if segment is None:
        return None, Finding(
            FindingCode.UNKNOWN_SEGMENT, f"segment {segment_id!r} was not supplied"
        )
    if word_count(quote) < MIN_QUOTE_WORDS:
        return None, Finding(
            FindingCode.QUOTE_TOO_SHORT,
            f"a quote from {segment_id} has fewer than {MIN_QUOTE_WORDS} words",
        )
    if len(quote) > rules.max_quote_chars:
        return None, Finding(FindingCode.QUOTE_NOT_FOUND, f"a quote from {segment_id} is too long")
    span = resolve_in_segment(
        segment_text=segment.text,
        segment_start=segment.char_start,
        chunk_id=segment.chunk_id,
        document_id=segment.document_id,
        quote=quote,
        speaker=segment.speaker,
        source_kind=segment.source_kind,
    )
    if span is None:
        return None, Finding(
            FindingCode.QUOTE_NOT_FOUND, f"a quote attributed to {segment_id} is not in it"
        )
    return span, None


def _criteria(
    proposed: Sequence[ProposedCriterion], rules: ExtractionRules
) -> tuple[tuple[CriterionDraft, ...], Finding | None]:
    if len(proposed) > rules.max_criteria_per_requirement:
        return (), Finding(
            FindingCode.TOO_MANY_CRITERIA,
            f"{len(proposed)} criteria proposed; at most "
            f"{rules.max_criteria_per_requirement} are accepted, so none were stored",
        )
    drafts: list[CriterionDraft] = []
    for index, criterion in enumerate(proposed, start=1):
        parts = (criterion.given.strip(), criterion.when.strip(), criterion.then.strip())
        if not all(parts) or any(len(p) > rules.max_criterion_clause_chars for p in parts):
            return (), Finding(
                FindingCode.CRITERION_INVALID,
                f"criterion {index} has an empty or over-long Given/When/Then clause, "
                "so no criteria were stored",
            )
        drafts.append(CriterionDraft(given=parts[0], when=parts[1], then=parts[2]))
    return tuple(drafts), None


def validate_proposal(
    proposal: RecordedProposal,
    *,
    segments: Mapping[str, SegmentView],
    key_map: Mapping[str, str],
    rules: ExtractionRules,
) -> ValidatedCandidate | RejectedCandidate:
    """Validate one proposal. ``key_map`` maps the output's model keys to run keys."""
    req = proposal.requirement
    findings: list[Finding] = []

    def reject(reason: ReviewReason, spans: Sequence[ResolvedSpan] = ()) -> RejectedCandidate:
        return RejectedCandidate(
            candidate_id=proposal.candidate_id,
            candidate_key=proposal.run_key,
            reason=reason,
            findings=tuple(findings),
            spans=tuple(spans),
        )

    if proposal.key_collision:
        findings.append(
            Finding(
                FindingCode.DUPLICATE_CANDIDATE_KEY,
                f"key {proposal.model_key!r} was used by an earlier proposal in the same output",
            )
        )
        return reject(ReviewReason.EXTRACTION_INVALID)

    # --- stage 3: provenance (FR-EXT-007) ------------------------------------
    spans: list[ResolvedSpan] = []
    if not req.evidence:
        findings.append(Finding(FindingCode.NO_EVIDENCE, "no source evidence was cited"))
    for evidence in req.evidence:
        span, problem = _resolve(segments, evidence.segment_id, evidence.quote, rules)
        if problem is not None:
            findings.append(problem)
        if span is not None and span.key not in {s.key for s in spans}:
            spans.append(span)
    if not spans:
        return reject(ReviewReason.UNRESOLVED_SOURCE)
    dropped_evidence = any(
        f.code
        in {FindingCode.UNKNOWN_SEGMENT, FindingCode.QUOTE_TOO_SHORT, FindingCode.QUOTE_NOT_FOUND}
        for f in findings
    )

    # --- stage 4: the statement (FR-EXT-003) ---------------------------------
    statement = " ".join(req.statement.split())
    if not statement.startswith(rules.statement_prefix + " "):
        findings.append(
            Finding(
                FindingCode.STATEMENT_NOT_DECLARATIVE,
                f"the statement does not begin with {rules.statement_prefix!r}",
            )
        )
        return reject(ReviewReason.EXTRACTION_INVALID, spans)
    if len(statement) > rules.max_statement_chars:
        findings.append(Finding(FindingCode.STATEMENT_TOO_LONG, "the statement is too long"))
        return reject(ReviewReason.EXTRACTION_INVALID, spans)

    # --- optional fields: kept only with their own resolving support ----------
    justification: str | None = None
    if req.justification is not None:
        support, _ = _resolve(
            segments, req.justification.segment_id, req.justification.quote, rules
        )
        if support is None:
            findings.append(
                Finding(FindingCode.UNSUPPORTED_JUSTIFICATION, "justification dropped: unsupported")
            )
        else:
            justification = req.justification.text.strip()

    priority: RequirementPriority | None = None
    if req.priority is not None:
        support, _ = _resolve(segments, req.priority.segment_id, req.priority.quote, rules)
        if support is None:
            findings.append(
                Finding(FindingCode.UNSUPPORTED_PRIORITY, "priority dropped: unsupported")
            )
        else:
            priority = RequirementPriority(req.priority.value)

    assumptions: list[str] = []
    for assumption in req.assumptions:
        support, _ = _resolve(segments, assumption.segment_id, assumption.quote, rules)
        if support is None:
            findings.append(
                Finding(FindingCode.UNSUPPORTED_ASSUMPTION, "an assumption dropped: unsupported")
            )
        else:
            assumptions.append(assumption.text.strip())

    # --- stage 2: references within the batch ---------------------------------
    def references(keys: Sequence[str], code: FindingCode, what: str) -> tuple[str, ...]:
        kept: list[str] = []
        for key in keys:
            target = key_map.get(key)
            if target is None or key == proposal.model_key:
                findings.append(Finding(code, f"{what} reference {key!r} is not another proposal"))
            elif target not in kept:
                kept.append(target)
        return tuple(kept)

    depends_on = references(req.depends_on, FindingCode.UNKNOWN_DEPENDENCY, "dependency")
    duplicate_of = references(
        req.duplicate_of, FindingCode.UNKNOWN_DUPLICATE_REFERENCE, "duplicate"
    )

    # --- acceptance criteria (FR-EXT-006): dropped, never repaired -------------
    criteria, criteria_problem = _criteria(req.acceptance_criteria, rules)
    if criteria_problem is not None:
        findings.append(criteria_problem)

    low_signal = req.review_signal < rules.min_extraction_signal
    if low_signal:
        findings.append(
            Finding(
                FindingCode.LOW_REVIEW_SIGNAL,
                f"review signal {req.review_signal:.2f} is below {rules.min_extraction_signal:.2f}",
            )
        )

    return ValidatedCandidate(
        candidate_id=proposal.candidate_id,
        candidate_key=proposal.run_key,
        ordinal=proposal.ordinal,
        kind=_KIND[req.requirement_type],
        statement=statement,
        spans=tuple(spans),
        original_text=original_wording(spans),
        review_signal=req.review_signal,
        justification=justification,
        priority=priority,
        assumptions=tuple(assumptions),
        depends_on_keys=depends_on,
        criteria=criteria,
        criteria_rejected=criteria_problem is not None,
        duplicate_of_keys=duplicate_of,
        findings=tuple(findings),
        low_signal=low_signal,
        dropped_evidence=dropped_evidence,
    )


def deduplicate(
    candidates: Sequence[ValidatedCandidate], rules: ExtractionRules
) -> tuple[
    tuple[ValidatedCandidate, ...],
    tuple[tuple[uuid.UUID, uuid.UUID], ...],
    tuple[NearDuplicate, ...],
]:
    """Merge exact duplicates; flag near-duplicates for a human (``FR-EXT-005``).

    Candidates are processed in batch order. A candidate that is an exact
    duplicate (same kind, identical normalised statement) of one already kept is
    folded into it: its spans are added, so no source link is lost, and the
    merge is recorded. Nothing else is ever merged by code.
    """
    kept: list[ValidatedCandidate] = []
    merges: list[tuple[uuid.UUID, uuid.UUID]] = []
    merged_into: dict[str, str] = {}
    for candidate in sorted(candidates, key=lambda c: c.ordinal):
        target_index = next(
            (
                i
                for i, other in enumerate(kept)
                if other.kind is candidate.kind
                and is_exact_duplicate(other.statement, candidate.statement)
            ),
            None,
        )
        if target_index is None:
            kept.append(candidate)
            continue
        target = kept[target_index]
        spans = list(target.spans)
        for span in candidate.spans:
            if span.key not in {s.key for s in spans}:
                spans.append(span)
        kept[target_index] = dataclasses.replace(
            target,
            spans=tuple(spans),
            original_text=original_wording(spans),
            merged_candidate_ids=(*target.merged_candidate_ids, candidate.candidate_id),
            findings=(
                *target.findings,
                Finding(
                    FindingCode.EXACT_DUPLICATE_MERGED,
                    f"{candidate.candidate_key} is an exact duplicate; its sources were added",
                ),
            ),
        )
        merges.append((candidate.candidate_id, target.candidate_id))
        merged_into[candidate.candidate_key] = target.candidate_key

    def canonical(key: str) -> str:
        return merged_into.get(key, key)

    # A dependency on a merged duplicate is a dependency on what it merged into.
    kept = [
        dataclasses.replace(
            c,
            depends_on_keys=tuple(
                dict.fromkeys(
                    canonical(k) for k in c.depends_on_keys if canonical(k) != c.candidate_key
                )
            ),
        )
        for c in kept
    ]

    near: list[NearDuplicate] = []
    seen: set[tuple[str, str]] = set()

    def flag(a: str, b: str, similarity: float, basis: str) -> None:
        pair = (a, b) if a < b else (b, a)
        if a == b or pair in seen:
            return
        seen.add(pair)
        near.append(NearDuplicate(key_a=pair[0], key_b=pair[1], similarity=similarity, basis=basis))

    by_key = {c.candidate_key: c for c in kept}
    for i, a in enumerate(kept):
        for b in kept[i + 1 :]:
            similarity = token_jaccard(a.statement, b.statement)
            if similarity >= rules.duplicate_review_similarity:
                flag(a.candidate_key, b.candidate_key, round(similarity, 4), "similarity")
    for candidate in kept:
        for other_key in candidate.duplicate_of_keys:
            other = by_key.get(canonical(other_key))
            if other is not None:
                similarity = token_jaccard(candidate.statement, other.statement)
                flag(candidate.candidate_key, other.candidate_key, round(similarity, 4), "model")

    return tuple(kept), tuple(merges), tuple(near)


def decide(
    windows: Sequence[tuple[Sequence[RecordedProposal], Mapping[str, SegmentView]]],
    rules: ExtractionRules,
) -> ExtractionDecision:
    """Validate every window's proposals, then deduplicate across the run."""
    accepted: list[ValidatedCandidate] = []
    rejected: list[RejectedCandidate] = []
    for proposals, segments in windows:
        key_map = {p.model_key: p.run_key for p in proposals if not p.key_collision}
        for proposal in proposals:
            outcome = validate_proposal(proposal, segments=segments, key_map=key_map, rules=rules)
            if isinstance(outcome, ValidatedCandidate):
                accepted.append(outcome)
            else:
                rejected.append(outcome)
    kept, merges, near = deduplicate(accepted, rules)
    return ExtractionDecision(
        accepted=kept,
        rejected=tuple(rejected),
        merged=merges,
        near_duplicates=near,
        ruleset_version=rules.version,
    )
