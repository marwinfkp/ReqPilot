"""Evidence persistence and citation resolution (``FR-RAG-003``; architecture J.5).

The chain this module makes deterministic::

    retrieved chunk -> evidence row -> evidence id -> (later) a model cites it
                    -> validation -> citation resolves to the exact source span

Three rules keep it honest:

* **Evidence comes only from retrieval.** :meth:`EvidenceService.record` takes a
  :class:`RetrievalResult`, never a list of chunk ids, and re-checks every chunk
  against the database through the *same* allowlist join retrieval uses. A
  forged or tampered result cannot turn a non-allowlisted chunk into evidence,
  and the stored quote is read from the database, not from the caller.
* **Evidence is self-contained history.** It keeps the exact quote and span, so
  it resolves after the knowledge base changes - a later model output is
  checked against what was shown, not against what retrieval would return today.
* **A citation must be in the run's evidence set.** :meth:`resolve_citation`
  refuses any id not in ``allowed_evidence_ids`` - the run's ``evidence_ids`` -
  with the same answer as for an id that does not exist, so a fabricated,
  cross-project or out-of-run citation cannot resolve (J.5).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence

from sqlalchemy.orm import Session

from reqpilot.domain.enums import SOURCE_TYPE_BINDING
from reqpilot.domain.errors import (
    CitationError,
    EvidenceIntegrityError,
    KnowledgeBaseError,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.knowledge import (
    Evidence,
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
)
from reqpilot.domain.policy import Actor
from reqpilot.domain.refs import EvidenceKind
from reqpilot.repositories.knowledge import EvidenceRepository
from reqpilot.retrieval.contracts import (
    Citation,
    CitationCheck,
    RetrievalResult,
    require_grounding,
)
from reqpilot.services.knowledge.admin import text_hash

_UNRESOLVABLE = "does not resolve to evidence supplied to this run"


class EvidenceService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._actor = actor
        self._repo = EvidenceRepository(session, actor)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record(
        self,
        result: RetrievalResult,
        *,
        graph_run_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> list[Evidence]:
        """Persist every chunk of a successful retrieval as evidence.

        ``RETRIEVAL_EMPTY`` is refused: there is nothing to ground on, and the
        caller must escalate for human review instead (``FR-RAG-005``).
        """
        require_grounding(result)
        project_id = ProjectId(result.project_id)
        scope = self._repo.load_scope(project_id, result.as_of)
        if scope is None:
            raise KnowledgeBaseError("project not found")
        if graph_run_id is not None and not self._repo.graph_run_in_project(
            project_id, graph_run_id
        ):
            raise KnowledgeBaseError("the graph run does not belong to this project")
        if self._repo.list_for_project(project_id, retrieval_id=result.retrieval_id):
            raise KnowledgeBaseError("evidence for this retrieval has already been recorded")

        eligible = self._repo.eligible_chunks(scope, [c.chunk_id for c in result.chunks])
        rows: list[Evidence] = []
        for chunk in result.chunks:
            found = eligible.get(chunk.chunk_id)
            if found is None:
                raise EvidenceIntegrityError(
                    f"chunk {chunk.chunk_id} is not retrievable by this project; evidence is "
                    "only ever recorded from the project's own allowlisted scope"
                )
            db_chunk, item, _source = found
            if (
                db_chunk.text != chunk.text
                or db_chunk.char_start != chunk.char_start
                or db_chunk.char_end != chunk.char_end
                or item.id != chunk.knowledge_item_id
            ):
                raise EvidenceIntegrityError(
                    f"retrieved chunk {chunk.chunk_id} does not match the stored chunk"
                )
            rows.append(
                Evidence(
                    project_id=project_id,
                    kind=EvidenceKind.KNOWLEDGE_ITEM,
                    target_id=item.id,
                    knowledge_chunk_id=db_chunk.id,
                    char_start=db_chunk.char_start,
                    char_end=db_chunk.char_end,
                    quote=db_chunk.text,
                    quote_hash=text_hash(db_chunk.text),
                    retrieval_id=result.retrieval_id,
                    rank=chunk.rank,
                    retrieval_score=chunk.fused_score,
                    kb_version=result.kb_version,
                    embedding_model=result.embedding_model,
                    ruleset_version=result.ruleset_version,
                    query_hash=result.query_hash,
                    graph_run_id=graph_run_id,
                    agent_run_id=agent_run_id,
                    created_by=self._actor.actor_id,
                )
            )
        self._repo.append(project_id, rows)
        return rows

    # ------------------------------------------------------------------
    # Reading and resolving
    # ------------------------------------------------------------------
    def list(
        self,
        project_id: ProjectId,
        *,
        retrieval_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
    ) -> list[Evidence]:
        return self._repo.list_for_project(
            project_id, retrieval_id=retrieval_id, graph_run_id=graph_run_id
        )

    def evidence_ids_for_run(
        self, project_id: ProjectId, graph_run_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        """The run's ``evidence_ids`` - the only ids its outputs may cite (J.5)."""
        return frozenset(
            e.id for e in self._repo.list_for_project(project_id, graph_run_id=graph_run_id)
        )

    def describe(self, project_id: ProjectId, evidence_id: uuid.UUID) -> Citation:
        """Resolve one evidence row for display. Integrity is still verified."""
        citation = self._citations(project_id, [evidence_id]).get(evidence_id)
        if not isinstance(citation, Citation):
            raise CitationError(f"evidence {evidence_id} {_UNRESOLVABLE}")
        return citation

    def resolve_citation(
        self,
        project_id: ProjectId,
        evidence_id: uuid.UUID,
        *,
        allowed_evidence_ids: Collection[uuid.UUID],
    ) -> Citation:
        """Resolve a citation made by a run, which may cite only its own evidence."""
        if evidence_id not in allowed_evidence_ids:
            raise CitationError(f"evidence {evidence_id} {_UNRESOLVABLE}")
        return self.describe(project_id, evidence_id)

    def check_citations(
        self,
        project_id: ProjectId,
        cited_ids: Sequence[str],
        *,
        allowed_evidence_ids: Collection[uuid.UUID],
    ) -> CitationCheck:
        """Verdict on every cited id, without raising: the input to claim validation.

        Ids arrive as strings because that is how a model emits them; anything
        that is not a UUID in the allowed set is rejected, never guessed at.
        """
        resolved: list[Citation] = []
        rejected: list[tuple[str, str]] = []
        candidates: dict[str, uuid.UUID] = {}
        for raw in cited_ids:
            try:
                parsed = uuid.UUID(str(raw))
            except ValueError:
                rejected.append((str(raw), "not an evidence id"))
                continue
            if parsed not in allowed_evidence_ids:
                rejected.append((str(raw), _UNRESOLVABLE))
                continue
            candidates[str(raw)] = parsed

        citations = self._citations(project_id, list(candidates.values()), strict=False)
        for raw, parsed in candidates.items():
            citation = citations.get(parsed)
            if isinstance(citation, Citation):
                resolved.append(citation)
            else:
                rejected.append((raw, citation or _UNRESOLVABLE))
        return CitationCheck(resolved=tuple(resolved), rejected=tuple(rejected))

    def _citations(
        self, project_id: ProjectId, evidence_ids: Sequence[uuid.UUID], *, strict: bool = True
    ) -> dict[uuid.UUID, Citation | str]:
        """Citations by id; with ``strict=False`` an integrity failure maps to its reason."""
        out: dict[uuid.UUID, Citation | str] = {}
        for evidence_id, row in self._repo.resolve(project_id, evidence_ids).items():
            try:
                out[evidence_id] = _citation(*row)
            except EvidenceIntegrityError as exc:
                if strict:
                    raise
                out[evidence_id] = str(exc)
        return out


def _citation(
    evidence: Evidence, chunk: KnowledgeChunk, item: KnowledgeItem, source: NormativeSource
) -> Citation:
    """Build a citation, verifying that the stored evidence still matches its span."""
    span = item.text[evidence.char_start : evidence.char_end]
    if (
        evidence.quote != chunk.text
        or evidence.quote != span
        or text_hash(evidence.quote) != evidence.quote_hash
        or evidence.target_id != item.id
        or (evidence.char_start, evidence.char_end) != (chunk.char_start, chunk.char_end)
    ):
        raise EvidenceIntegrityError(
            f"evidence {evidence.id} no longer matches the chunk and span it records"
        )
    return Citation(
        evidence_id=evidence.id,
        project_id=evidence.project_id,
        retrieval_id=evidence.retrieval_id,
        graph_run_id=evidence.graph_run_id,
        knowledge_chunk_id=chunk.id,
        knowledge_item_id=item.id,
        item_key=item.item_key,
        item_version_no=item.version_no,
        item_status_now=str(item.status),
        item_title=item.title,
        clause_ref=item.clause_ref,
        normative_source_id=source.id,
        source_title=source.title,
        source_type=source.source_type,
        binding=SOURCE_TYPE_BINDING[source.source_type],
        issuing_body=source.issuing_body,
        jurisdiction=source.jurisdiction,
        source_version=source.version,
        effective_date=source.effective_date,
        retrieved_at=source.retrieved_at,
        source_url=source.source_url,
        licence_class=source.licence_class,
        char_start=evidence.char_start,
        char_end=evidence.char_end,
        quote=evidence.quote,
        kb_version=evidence.kb_version,
        rank=evidence.rank,
        retrieval_score=evidence.retrieval_score,
    )
