"""The quality engine: what the P5 nodes record, deterministically (C.3 nodes 6-8).

The engine is the *disposing* half of "the LLM proposes; deterministic code
disposes". It:

* chooses the versions a run analyses (current, not terminal, in the project);
* runs the deterministic checks and duplicate detection, and records what they
  find;
* records a model's quality finding only once validation has accepted it,
  with the ruleset's severity - never the model's;
* builds the conflict shortlist (embeddings computed transiently, in memory,
  never stored) and records conflicts: a definite rule verdict, or a validated
  adjudication;
* never records the same finding or conflict twice - a finding or conflict that
  a human dismissed stays dismissed for that version or pair.

It has no access to a model: the graph nodes call the roles and hand the
engine validated proposals.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    ConflictClass,
    ConflictKind,
    ConflictStatus,
    FindingDetector,
    QualityFindingStatus,
    QualityFindingType,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.lifecycle.states import TERMINAL_STATES
from reqpilot.domain.models.elicitation import QualityFinding, Utterance
from reqpilot.domain.models.extraction import SourceChunk, SourceDocument
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.quality import (
    Candidate,
    DetectedFinding,
    PairItem,
    canonical,
    check_statement,
    duplicates,
    shortlist,
)
from reqpilot.domain.quality.text import Span
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.extraction import ClassificationRepository
from reqpilot.repositories.quality import ConflictRepository, GlossaryRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.retrieval.embeddings import EmbeddingProvider, cosine_similarity
from reqpilot.rules.quality import QualityRules
from reqpilot.services.audit import AuditService
from reqpilot.services.elicitation import source_facts

#: A version in one of these states is not analysed: it is history, or refused.
NOT_ANALYSED: frozenset[RequirementState] = TERMINAL_STATES | {RequirementState.REJECTED}


@dataclass(frozen=True)
class VersionView:
    """One version as the engine uses it within a run (transient, never checkpointed)."""

    version: RequirementVersion
    human_id: str
    stakeholder: str | None
    masked: bool
    synthetic: bool

    @property
    def key(self) -> str:
        return str(self.version.id)


@dataclass(frozen=True)
class ProposedConflict:
    """A conflict about to be recorded - from a rule or a validated adjudication."""

    a: VersionView
    b: VersionView
    conflict_class: ConflictClass
    kind: ConflictKind
    rationale: str
    evidence_a: str
    evidence_b: str
    review_signal: float | None
    detected_by: FindingDetector
    rule_id: str


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def stakeholder_label(refs: Sequence[dict]) -> str | None:
    """Whom a version traces to: the first speaker/stakeholder its sources name."""
    for ref in refs:
        if isinstance(ref, dict):
            for key in ("stakeholder", "speaker"):
                value = ref.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.split())[:300]
    return None


class QualityEngine:
    def __init__(self, session: Session, actor: Actor, rules: QualityRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._findings = QualityFindingRepository(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._glossary = GlossaryRepository(session, actor)
        self._labels = ClassificationRepository(session, actor)
        self._audit = AuditService(session)

    @property
    def rules(self) -> QualityRules:
        return self._rules

    # ------------------------------------------------------------------
    # scope
    # ------------------------------------------------------------------
    def current_versions(self, project_id: ProjectId) -> list[VersionView]:
        """Every requirement's current version that is still being analysed."""
        views: list[VersionView] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is None or version.state in NOT_ANALYSED:
                continue
            views.append(self._view(project_id, version, requirement.human_id))
        views.sort(key=lambda v: v.human_id)
        return views[: self._rules.max_versions_per_run]

    def views(self, project_id: ProjectId, version_ids: Sequence[uuid.UUID]) -> list[VersionView]:
        views = []
        for version_id in dict.fromkeys(version_ids):
            version = self._versions.get(project_id, version_id)
            if version is None:
                raise ValueError("a version in the scope is not in this project")
            requirement = self._requirements.get(project_id, version.requirement_id)
            assert requirement is not None  # a foreign key
            views.append(self._view(project_id, version, requirement.human_id))
        return views

    def _view(
        self, project_id: ProjectId, version: RequirementVersion, human_id: str
    ) -> VersionView:
        refs = version.source_refs or []
        masked, synthetic = source_facts(self._session, self._actor, project_id, refs)
        if not refs:
            masked, synthetic = False, False
        return VersionView(version, human_id, stakeholder_label(refs), masked, synthetic)

    # ------------------------------------------------------------------
    # deterministic checks (C.3 node 6, the rule half)
    # ------------------------------------------------------------------
    def glossary_keys(self, project_id: ProjectId) -> frozenset[str]:
        return frozenset(t.term_key for t in self._glossary.list_for_project(project_id))

    def unresolved_refs(self, project_id: ProjectId, refs: Sequence[dict]) -> int:
        """Source references of a kind ReqPilot stores that point at nothing in this project.

        Only document chunks and utterances are checked; a reference of another
        kind (a P1 manual attribution) is taken as written.
        """
        unresolved = 0
        for ref in refs:
            if not isinstance(ref, dict):
                unresolved += 1
                continue
            kind, raw = ref.get("kind"), ref.get("ref")
            if kind not in ("source_chunk", "utterance"):
                continue
            try:
                ref_id = uuid.UUID(str(raw))
            except ValueError:
                unresolved += 1
                continue
            if kind == "source_chunk":
                chunk = self._session.get(SourceChunk, ref_id)
                document = (
                    self._session.get(SourceDocument, chunk.source_document_id) if chunk else None
                )
                if document is None or document.project_id != project_id:
                    unresolved += 1
            else:
                utterance = self._session.get(Utterance, ref_id)
                if utterance is None or utterance.project_id != project_id:
                    unresolved += 1
        return unresolved

    def rule_findings(
        self, project_id: ProjectId, view: VersionView, glossary_keys: frozenset[str]
    ) -> list[DetectedFinding]:
        version = view.version
        refs = version.source_refs or []
        categories = self._labels.current_categories(project_id, version.id)
        if version.category is not None:
            categories = [*categories, str(version.category)]
        return check_statement(
            version.statement,
            self._rules,
            categories=categories,
            glossary_keys=glossary_keys,
            source_ref_count=len(refs),
            unresolved_ref_count=self.unresolved_refs(project_id, refs),
        )

    def duplicate_findings(
        self, views: Sequence[VersionView], focus: frozenset[str] | None = None
    ) -> list[tuple[VersionView, DetectedFinding]]:
        """``FR-QAL-004``: each duplicate pair yields one finding, on the later version."""
        by_key = {v.key: v for v in views}
        items = [
            PairItem(v.key, v.version.statement, requirement_key=str(v.version.requirement_id))
            for v in views
        ]
        found: list[tuple[VersionView, DetectedFinding]] = []
        for pair in duplicates(items, self._rules):
            if focus is not None and pair.a not in focus and pair.b not in focus:
                continue
            first, second = sorted(
                (by_key[pair.a], by_key[pair.b]),
                key=lambda v: (v.version.created_at, v.human_id),
            )
            finding_type = (
                QualityFindingType.DUPLICATION if pair.exact else QualityFindingType.NEAR_DUPLICATE
            )
            found.append(
                (
                    second,
                    DetectedFinding(
                        finding_type,
                        "DUP-EXACT" if pair.exact else "DUP-NEAR",
                        (
                            f"states the same requirement as {first.human_id}"
                            if pair.exact
                            else f"overlaps {first.human_id} (token similarity "
                            f"{pair.similarity:.2f}); review whether they are one requirement"
                        ),
                        self._rules.duplicate_signal_exact
                        if pair.exact
                        else self._rules.duplicate_signal_near,
                        related_key=first.key,
                    ),
                )
            )
        return found

    # ------------------------------------------------------------------
    # recording findings
    # ------------------------------------------------------------------
    def _already_recorded(
        self,
        project_id: ProjectId,
        version_id: uuid.UUID,
        finding_type: QualityFindingType,
        span: str | None,
        related: uuid.UUID | None,
    ) -> bool:
        """The same defect was recorded before - open, resolved or dismissed."""
        return any(
            f.finding_type is finding_type
            and _norm(f.span_quote) == _norm(span)
            and f.related_version_id == related
            for f in self._findings.for_version(project_id, version_id)
        )

    def record_finding(
        self,
        project_id: ProjectId,
        view: VersionView,
        *,
        finding_type: QualityFindingType,
        rationale: str,
        span: Span | None,
        review_signal: float | None,
        detected_by: FindingDetector,
        rule_id: str,
        graph_run_id: uuid.UUID | None,
        agent_run_id: uuid.UUID | None = None,
        related_version_id: uuid.UUID | None = None,
        extra_evidence: dict | None = None,
    ) -> QualityFinding | None:
        """Record one finding - with the ruleset's severity - unless already recorded."""
        if detected_by is FindingDetector.HUMAN:
            raise ValueError("human findings are recorded by QualityFindingService")
        version = view.version
        quote = span.quote if span else None
        if span is not None and version.statement[span.start : span.end] != span.quote:
            raise ValueError("a finding's span must be words of the version's statement")
        if self._already_recorded(project_id, version.id, finding_type, quote, related_version_id):
            return None
        evidence: list[dict] = []
        if span is not None:
            evidence.append(
                {
                    "kind": "statement_span",
                    "quote": span.quote,
                    "start": span.start,
                    "end": span.end,
                }
            )
        if related_version_id is not None:
            evidence.append({"kind": "requirement_version", "ref": str(related_version_id)})
        if extra_evidence:
            evidence.append({"kind": "proposal", **extra_evidence})
        signal = None if review_signal is None else max(0.0, min(1.0, float(review_signal)))
        finding = self._findings.add(
            QualityFinding(
                project_id=project_id,
                requirement_version_id=version.id,
                finding_type=finding_type,
                severity=self._rules.severity_of(finding_type),
                rationale=" ".join(rationale.split())[:2000] or str(finding_type),
                span_quote=quote,
                status=QualityFindingStatus.OPEN,
                detected_by=detected_by,
                recorded_by=self._actor.actor_id,
                rule_id=rule_id[:100],
                review_signal=signal,
                evidence=evidence,
                related_version_id=related_version_id,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
            ),
            action=Action.QUALITY_FINDING_DETECT,
        )
        self._audit.append(
            event_type=AuditEventType.QUALITY_FINDING_RAISED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "finding_id": str(finding.id),
                "finding_type": str(finding_type),
                "severity": str(finding.severity),
                "detected_by": str(detected_by),
                "rule_id": finding.rule_id,
                "review_priority": str(self._rules.priority_of(signal)),
            },
        )
        return finding

    def record_detected(
        self,
        project_id: ProjectId,
        view: VersionView,
        detected: DetectedFinding,
        *,
        graph_run_id: uuid.UUID | None,
        related: VersionView | None = None,
    ) -> QualityFinding | None:
        return self.record_finding(
            project_id,
            view,
            finding_type=detected.finding_type,
            rationale=detected.rationale,
            span=detected.span,
            review_signal=detected.review_signal,
            detected_by=FindingDetector.RULE,
            rule_id=f"{self._rules.ruleset_ref}:{detected.rule_id}",
            graph_run_id=graph_run_id,
            related_version_id=related.version.id if related else None,
        )

    # ------------------------------------------------------------------
    # conflicts (C.3 nodes 7 and 8)
    # ------------------------------------------------------------------
    def candidates(
        self,
        views: Sequence[VersionView],
        *,
        embedder: EmbeddingProvider | None = None,
        focus: frozenset[str] | None = None,
    ) -> list[Candidate]:
        """The deterministic shortlist (``FR-CNF-004``). Embeddings live in memory only."""
        items = [
            PairItem(
                v.key,
                v.version.statement,
                stakeholder=v.stakeholder,
                requirement_key=str(v.version.requirement_id),
            )
            for v in views
        ]
        similarity: Callable[[str, str], float] | None = None
        if embedder is not None and views:
            vectors = dict(
                zip(
                    [v.key for v in views],
                    embedder.embed_documents([v.version.statement for v in views]),
                    strict=True,
                )
            )

            def similarity(a: str, b: str) -> float:
                return max(0.0, cosine_similarity(vectors[a], vectors[b]))

        return shortlist(items, self._rules, similarity=similarity, focus=focus)

    def pair_recorded(self, project_id: ProjectId, a: VersionView, b: VersionView) -> bool:
        """A conflict row exists for this exact pair - in any status, so a dismissal sticks."""
        return bool(self._conflicts.for_pair(project_id, a.version.id, b.version.id))

    def record_conflict(
        self,
        project_id: ProjectId,
        proposed: ProposedConflict,
        *,
        graph_run_id: uuid.UUID | None,
        agent_run_id: uuid.UUID | None = None,
    ) -> Conflict | None:
        """Record one conflict unless the pair already has one. Validates the pair itself."""
        if (
            proposed.a.version.project_id != project_id
            or proposed.b.version.project_id != project_id
        ):
            raise ValueError("both versions of a conflict must be in its project")
        if proposed.a.version.id == proposed.b.version.id:
            raise ValueError("a conflict needs two different versions")
        if self.pair_recorded(project_id, proposed.a, proposed.b):
            return None
        first_key, _ = canonical(proposed.a.key, proposed.b.key)
        a, b = (proposed.a, proposed.b) if proposed.a.key == first_key else (proposed.b, proposed.a)
        evidence_a, evidence_b = (
            (proposed.evidence_a, proposed.evidence_b)
            if a is proposed.a
            else (proposed.evidence_b, proposed.evidence_a)
        )
        for view, evidence in ((a, evidence_a), (b, evidence_b)):
            if evidence and _norm(evidence) not in _norm(view.version.statement):
                raise ValueError("conflict evidence must be words of the version it cites")
        disagreement = bool(
            a.stakeholder and b.stakeholder and _norm(a.stakeholder) != _norm(b.stakeholder)
        )
        signal = (
            None if proposed.review_signal is None else max(0.0, min(1.0, proposed.review_signal))
        )
        conflict = self._conflicts.add(
            Conflict(
                project_id=project_id,
                version_a_id=a.version.id,
                version_b_id=b.version.id,
                conflict_class=proposed.conflict_class,
                kind=proposed.kind,
                rationale=" ".join(proposed.rationale.split())[:2000] or "conflict",
                evidence_a=evidence_a or a.version.statement,
                evidence_b=evidence_b or b.version.statement,
                severity=self._rules.severity_by_class[proposed.conflict_class],
                review_signal=signal,
                involves_stakeholder_disagreement=disagreement,
                stakeholder_a=a.stakeholder,
                stakeholder_b=b.stakeholder,
                detected_by=proposed.detected_by,
                rule_id=proposed.rule_id[:100],
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                recorded_by=self._actor.actor_id,
                status=ConflictStatus.OPEN,
            )
        )
        self._audit.append(
            event_type=AuditEventType.CONFLICT_PROPOSED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="conflict",
            subject_id=str(conflict.id),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "version_a_id": str(a.version.id),
                "version_b_id": str(b.version.id),
                "conflict_class": str(conflict.conflict_class),
                "kind": str(conflict.kind),
                "severity": str(conflict.severity),
                "detected_by": str(conflict.detected_by),
                "rule_id": conflict.rule_id,
                "involves_stakeholder_disagreement": disagreement,
                "review_priority": str(self._rules.priority_of(signal)),
            },
        )
        return conflict

    def shortlisted(
        self,
        project_id: ProjectId,
        *,
        graph_run_id: uuid.UUID,
        pairs: int,
        compared: int,
    ) -> None:
        """``CONFLICT_SHORTLISTED`` (architecture E #6 audit): counts, not content."""
        require(
            self._actor,
            Action.CONFLICT_DETECT,
            ResourceRef(resource_type=ResourceType.CONFLICT, project_id=project_id),
        )
        self._audit.append(
            event_type=AuditEventType.CONFLICT_SHORTLISTED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="graph_run",
            subject_id=str(graph_run_id),
            graph_run_id=graph_run_id,
            payload={
                "versions": compared,
                "pairs_shortlisted": pairs,
                "ruleset": self._rules.ruleset_ref,
            },
        )
