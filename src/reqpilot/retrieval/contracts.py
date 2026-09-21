"""Typed retrieval contracts (``FR-RAG-002``, ``FR-RAG-003``, ``FR-RAG-005``).

Three properties are carried by the types themselves rather than by convention:

* **Classification narrows, it never widens.** :class:`QueryClassification` can
  restrict source types and applicability. There is no field that names a
  source, a jurisdiction or a KB version: those come from the project, inside
  the database query (architecture J.4). A caller - or a model, later - cannot
  steer retrieval outside what the project is permitted to see.
* **An empty retrieval is an explicit outcome.** :class:`RetrievalResult` is
  either ``RETRIEVAL_SUCCESS`` with at least one chunk, or ``RETRIEVAL_EMPTY``
  with a reason and ``requires_human_review=True``. There is no third shape,
  and in particular no "success with nothing in it" for later code to treat as
  permission to answer from parametric memory (``FR-RAG-005``).
* **Provenance travels with every chunk**: source title, C.1 type, issuing
  body, jurisdiction, source version, effective date, curation date and exact
  offsets (architecture J.5).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    ChunkStrategy,
    LicenceClass,
    NormativeSourceType,
    RequirementCategory,
    TextOrigin,
)
from reqpilot.domain.errors import UngroundedRetrievalError


class ClassificationOrigin(StrEnum):
    """Who classified the query. P2 infers nothing itself."""

    #: No classification: retrieval uses only the project's own scope.
    UNCLASSIFIED = "unclassified"
    #: Supplied by the caller - later, the Classification role's validated
    #: output (P3) or the deterministic applicable-source step (architecture K.1).
    CALLER_SUPPLIED = "caller_supplied"


class QueryClassification(BaseModel):
    """The ``FR-RAG-002`` query classification contract.

    Every field only narrows the candidate set. Empty means "no restriction
    beyond the project's scope".
    """

    model_config = ConfigDict(frozen=True)

    source_types: frozenset[NormativeSourceType] = frozenset()
    #: Applicability tags; an item matches if it shares one, or declares none.
    applicability: frozenset[str] = frozenset()
    #: Recorded for provenance. It does not filter on its own.
    requirement_category: RequirementCategory | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def origin(self) -> ClassificationOrigin:
        if self.source_types or self.applicability or self.requirement_category:
            return ClassificationOrigin.CALLER_SUPPLIED
        return ClassificationOrigin.UNCLASSIFIED


class RetrievalQuery(BaseModel):
    """One retrieval request, scoped to exactly one project."""

    model_config = ConfigDict(frozen=True)

    project_id: uuid.UUID
    text: str = Field(min_length=1, max_length=2000)
    classification: QueryClassification = QueryClassification()
    #: Evaluate effective dates as of this day. Defaults to today; a future date
    #: is refused, because it would admit sources not yet in force.
    as_of: dt.date | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)

    @property
    def query_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class RetrievalOutcome(StrEnum):
    """The deterministic ``FR-RAG-005`` signal."""

    SUCCESS = "RETRIEVAL_SUCCESS"
    EMPTY = "RETRIEVAL_EMPTY"


class EmptyReason(StrEnum):
    """Why a retrieval came back empty. Each one is a distinct, testable path."""

    NO_JURISDICTION_SCOPE = "no_jurisdiction_scope"
    NO_ALLOWLISTED_SOURCES = "no_allowlisted_sources"
    NOTHING_RELEVANT = "nothing_relevant"


class RetrievedChunk(BaseModel):
    """One ranked chunk with the provenance J.5 requires on every claim."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    chunk_id: uuid.UUID
    knowledge_item_id: uuid.UUID
    item_key: str
    item_version_no: int
    item_title: str | None
    clause_ref: str | None
    kb_version: int
    normative_source_id: uuid.UUID
    source_title: str
    source_type: NormativeSourceType
    issuing_body: str
    jurisdiction: str
    source_version: str
    effective_date: dt.date | None
    retrieved_at: dt.date
    source_url: str | None
    licence_class: LicenceClass
    text_origin: TextOrigin
    char_start: int = Field(ge=0)
    char_end: int
    text: str
    strategy: ChunkStrategy
    structure_label: str | None
    #: Fused reciprocal-rank score (higher is better).
    fused_score: float
    #: Cosine similarity to the query vector.
    vector_similarity: float
    vector_rank: int | None
    keyword_rank: int | None

    @property
    def binding(self) -> str:
        """How binding the source is, per C.1 - so a standard never reads as law."""
        return SOURCE_TYPE_BINDING[self.source_type]


class RetrievalResult(BaseModel):
    """The outcome of one retrieval, with everything needed to reproduce it."""

    model_config = ConfigDict(frozen=True)

    retrieval_id: uuid.UUID
    project_id: uuid.UUID
    outcome: RetrievalOutcome
    empty_reason: EmptyReason | None = None
    requires_human_review: bool
    query_hash: str
    as_of: dt.date
    #: The KB version retrieval saw: the project's pin, or the current version.
    kb_version: int
    kb_version_pinned: bool
    embedding_model: str
    ruleset_version: str
    classification: QueryClassification
    chunks: tuple[RetrievedChunk, ...] = ()

    @model_validator(mode="after")
    def _outcome_is_explicit(self) -> RetrievalResult:
        if self.outcome is RetrievalOutcome.SUCCESS:
            if not self.chunks:
                raise ValueError("a successful retrieval must contain at least one chunk")
            if self.empty_reason is not None or self.requires_human_review:
                raise ValueError(
                    "a successful retrieval has no empty reason and needs no escalation"
                )
        else:
            if self.chunks:
                raise ValueError("an empty retrieval cannot carry chunks")
            if self.empty_reason is None or not self.requires_human_review:
                raise ValueError("an empty retrieval must state why and require human review")
        ranks = [c.rank for c in self.chunks]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("chunk ranks must be 1..n in order")
        return self

    @classmethod
    def empty(cls, reason: EmptyReason, **fields: object) -> RetrievalResult:
        return cls(
            outcome=RetrievalOutcome.EMPTY,
            empty_reason=reason,
            requires_human_review=True,
            chunks=(),
            **fields,  # type: ignore[arg-type]
        )


def require_grounding(result: RetrievalResult) -> RetrievalResult:
    """Return ``result`` if it carries evidence; otherwise refuse to proceed.

    The call later analysis steps make before supplying anything to a model.
    ``RETRIEVAL_EMPTY`` raises, so "nothing relevant was found" can only lead to
    human review, never to an ungrounded answer (``FR-RAG-005``).
    """
    if result.outcome is RetrievalOutcome.EMPTY:
        raise UngroundedRetrievalError(
            f"retrieval {result.retrieval_id} found nothing relevant "
            f"({result.empty_reason}); escalate for human review rather than answering "
            "without evidence"
        )
    return result


class Citation(BaseModel):
    """A resolved citation: exactly what a run was shown, and where it came from.

    Everything J.5 requires a compliance, security or risk claim to surface -
    source title, C.1 type, issuing body, jurisdiction, version, effective date,
    curation date and the exact quoted span - plus the evidence, retrieval and
    KB-version identities that make it reproducible (``FR-RAG-003``).
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: uuid.UUID
    project_id: uuid.UUID
    retrieval_id: uuid.UUID
    graph_run_id: uuid.UUID | None
    knowledge_chunk_id: uuid.UUID
    knowledge_item_id: uuid.UUID
    item_key: str
    item_version_no: int
    #: The item's status *now*. A superseded item's evidence still resolves: the
    #: citation records what was true when the evidence was supplied.
    item_status_now: str
    item_title: str | None
    clause_ref: str | None
    normative_source_id: uuid.UUID
    source_title: str
    source_type: NormativeSourceType
    binding: str
    issuing_body: str
    jurisdiction: str
    source_version: str
    effective_date: dt.date | None
    #: Curation date: when a human consulted the source.
    retrieved_at: dt.date
    source_url: str | None
    licence_class: LicenceClass
    char_start: int
    char_end: int
    quote: str
    kb_version: int
    rank: int
    retrieval_score: float


class CitationCheck(BaseModel):
    """The deterministic verdict on a set of cited evidence ids.

    What the later validation stage (architecture F.2 stage 3) consumes: every
    cited id either resolves to evidence this run was given, or is rejected
    with a reason. A rejected citation drops the claim carrying it (J.5).
    """

    model_config = ConfigDict(frozen=True)

    resolved: tuple[Citation, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()

    @property
    def all_resolved(self) -> bool:
        return not self.rejected
