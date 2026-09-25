"""The typed trace graph's vocabulary (architecture N.1, N.2; ``FR-TRC-001``).

Pure data, no I/O. Two things live here:

* the **node types** a trace link may join, and the **link types**;
* the **closed allowlist** of ``(from_type, link_type, to_type)`` triples. An edge
  outside it is a programming error, not a data variation (architecture N.1) -
  it is refused by the trace service *and* by a database ``CHECK`` built from
  this same table, so the graph stays queryable and E6 stays well defined.

The allowlist is architecture N.2 as far as P8 can realise it, plus the edges P8
needs to make the full ``FR-TRC-001`` chain navigable. Every addition is marked
``P8`` below and recorded in the P8 report. Nothing is renamed:

* N.2 #8 ``requirement_version MAPPED_TO control`` targets the **checklist
  control key** that P6 actually records (``compliance_mapping.control_key``), so
  the node type is named ``checklist_control`` rather than implying a row of the
  P2 ``control`` table that the mapping does not reference.
* N.2 #11 (``security_privacy_finding DERIVED requirement_version``) has no
  producer: P6 records a derived requirement as text on the finding, never as a
  requirement version. The triple is not allowlisted, so no edge can pretend it.
* N.2 #20-#26 belong to P9 (SDLC factors) and P10 (workflow) and are absent.
"""

from __future__ import annotations

from enum import StrEnum


class TraceNodeType(StrEnum):
    """What a trace link may point at. Values are the persisted table names."""

    STAKEHOLDER = "stakeholder"
    UTTERANCE = "utterance"
    SOURCE_DOCUMENT = "source_document"
    SOURCE_CHUNK = "source_chunk"
    REQUIREMENT_VERSION = "requirement_version"
    CLASSIFICATION = "requirement_classification"
    QUALITY_FINDING = "quality_finding"
    CLARIFICATION = "clarification"
    CONFLICT = "conflict"
    COMPLIANCE_MAPPING = "compliance_mapping"
    CHECKLIST_CONTROL = "checklist_control"
    SECURITY_PRIVACY_FINDING = "security_privacy_finding"
    RISK = "risk"
    RISK_MITIGATION = "risk_mitigation"
    AGENT_RUN = "agent_run"
    ACCEPTANCE_CRITERION = "acceptance_criterion"
    APPROVAL_DECISION = "approval_decision"
    BASELINE = "baseline"
    ARTIFACT_VERSION = "artifact_version"
    ARTIFACT_SECTION = "artifact_section"
    EVIDENCE = "evidence"
    KNOWLEDGE_ITEM = "knowledge_item"
    NORMATIVE_SOURCE = "normative_source"


class TraceLinkType(StrEnum):
    """The relationship an edge asserts (architecture N.2, plus the P8 additions)."""

    SOURCES = "SOURCES"
    STATED = "STATED"
    CLASSIFIED_AS = "CLASSIFIED_AS"
    HAS_FINDING = "HAS_FINDING"
    RAISED = "RAISED"
    ANSWERED_BY = "ANSWERED_BY"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    HAS_CONFLICT = "HAS_CONFLICT"
    MAPPED_TO = "MAPPED_TO"
    HAS_MAPPING = "HAS_MAPPING"
    EVIDENCED_BY = "EVIDENCED_BY"
    HAS_SECURITY_FINDING = "HAS_SECURITY_FINDING"
    HAS_RISK = "HAS_RISK"
    RISK_ASSESSED_BY = "RISK_ASSESSED_BY"
    MITIGATED_BY = "MITIGATED_BY"
    SATISFIED_BY = "SATISFIED_BY"
    APPROVED_BY = "APPROVED_BY"
    MEMBER_OF = "MEMBER_OF"
    RENDERED_IN = "RENDERED_IN"
    CITES = "CITES"
    CONTAINS = "CONTAINS"
    SUPERSEDES = "SUPERSEDES"
    DRAWN_FROM = "DRAWN_FROM"
    ISSUED_UNDER = "ISSUED_UNDER"


N = TraceNodeType
L = TraceLinkType

#: The closed allowlist, keyed by triple, valued by where the triple comes from:
#: an N.2 row number, or ``"P8"`` for an edge this phase adds.
ALLOWED_TRIPLES: dict[tuple[TraceNodeType, TraceLinkType, TraceNodeType], str] = {
    # stakeholder input -> requirement
    (N.UTTERANCE, L.SOURCES, N.REQUIREMENT_VERSION): "N.2 #1",
    (N.SOURCE_CHUNK, L.SOURCES, N.REQUIREMENT_VERSION): "N.2 #2",
    # A P3/P6 source reference that names a document but no chunk (P8).
    (N.SOURCE_DOCUMENT, L.SOURCES, N.REQUIREMENT_VERSION): "P8",
    # Who said it: the stakeholder behind an interview answer (P8).
    (N.STAKEHOLDER, L.STATED, N.UTTERANCE): "P8",
    # requirement -> analysis
    (N.REQUIREMENT_VERSION, L.CLASSIFIED_AS, N.CLASSIFICATION): "N.2 #3",
    (N.REQUIREMENT_VERSION, L.HAS_FINDING, N.QUALITY_FINDING): "N.2 #4",
    (N.QUALITY_FINDING, L.RAISED, N.CLARIFICATION): "N.2 #5",
    (N.CLARIFICATION, L.ANSWERED_BY, N.UTTERANCE): "N.2 #6",
    (N.REQUIREMENT_VERSION, L.CONFLICTS_WITH, N.REQUIREMENT_VERSION): "N.2 #7",
    (N.REQUIREMENT_VERSION, L.HAS_CONFLICT, N.CONFLICT): "P8",
    (N.REQUIREMENT_VERSION, L.MAPPED_TO, N.CHECKLIST_CONTROL): "N.2 #8",
    (N.REQUIREMENT_VERSION, L.HAS_MAPPING, N.COMPLIANCE_MAPPING): "P8",
    (N.COMPLIANCE_MAPPING, L.MAPPED_TO, N.CHECKLIST_CONTROL): "P8",
    (N.COMPLIANCE_MAPPING, L.EVIDENCED_BY, N.EVIDENCE): "N.2 #9",
    (N.REQUIREMENT_VERSION, L.HAS_SECURITY_FINDING, N.SECURITY_PRIVACY_FINDING): "N.2 #10",
    (N.SECURITY_PRIVACY_FINDING, L.EVIDENCED_BY, N.EVIDENCE): "P8",
    (N.REQUIREMENT_VERSION, L.HAS_RISK, N.RISK): "N.2 #12",
    # N.3's "recorded 'no risk identified' result": the risk-analysis agent run
    # that examined this exact version and finished (P8).
    (N.REQUIREMENT_VERSION, L.RISK_ASSESSED_BY, N.AGENT_RUN): "P8",
    (N.RISK, L.EVIDENCED_BY, N.EVIDENCE): "N.2 #13",
    (N.RISK, L.MITIGATED_BY, N.RISK_MITIGATION): "N.2 #14",
    (N.REQUIREMENT_VERSION, L.SATISFIED_BY, N.ACCEPTANCE_CRITERION): "N.2 #15",
    # approvals
    (N.REQUIREMENT_VERSION, L.APPROVED_BY, N.APPROVAL_DECISION): "N.2 #16",
    (N.COMPLIANCE_MAPPING, L.APPROVED_BY, N.APPROVAL_DECISION): "P8",
    (N.SECURITY_PRIVACY_FINDING, L.APPROVED_BY, N.APPROVAL_DECISION): "P8",
    (N.CONFLICT, L.APPROVED_BY, N.APPROVAL_DECISION): "P8",
    (N.RISK, L.APPROVED_BY, N.APPROVAL_DECISION): "P8",
    # baseline -> artefact
    (N.REQUIREMENT_VERSION, L.MEMBER_OF, N.BASELINE): "N.2 #17",
    (N.BASELINE, L.RENDERED_IN, N.ARTIFACT_VERSION): "N.2 #18",
    (N.ARTIFACT_VERSION, L.CITES, N.REQUIREMENT_VERSION): "N.2 #19",
    (N.ARTIFACT_VERSION, L.CONTAINS, N.ARTIFACT_SECTION): "P8",
    # FR-DOC-008: section-level citations (P8).
    (N.ARTIFACT_SECTION, L.CITES, N.REQUIREMENT_VERSION): "P8",
    (N.ARTIFACT_SECTION, L.CITES, N.RISK): "P8",
    (N.ARTIFACT_SECTION, L.CITES, N.COMPLIANCE_MAPPING): "P8",
    (N.ARTIFACT_SECTION, L.CITES, N.EVIDENCE): "P8",
    # evidence -> normative item (P8)
    (N.EVIDENCE, L.DRAWN_FROM, N.KNOWLEDGE_ITEM): "P8",
    (N.KNOWLEDGE_ITEM, L.ISSUED_UNDER, N.NORMATIVE_SOURCE): "P8",
    # versioning
    (N.REQUIREMENT_VERSION, L.SUPERSEDES, N.REQUIREMENT_VERSION): "N.2 #27",
}


def triple_key(from_type: str, link_type: str, to_type: str) -> str:
    """The string the database ``CHECK`` compares: ``from:LINK:to``."""
    return f"{from_type}:{link_type}:{to_type}"


#: Every allowed triple as the database sees it, for the migration's ``CHECK``.
ALLOWED_TRIPLE_KEYS: frozenset[str] = frozenset(
    triple_key(str(f), str(link), str(t)) for (f, link, t) in ALLOWED_TRIPLES
)


def is_allowed(from_type: str, link_type: str, to_type: str) -> bool:
    return triple_key(from_type, link_type, to_type) in ALLOWED_TRIPLE_KEYS


def allowed_triple_check_sql() -> str:
    """``CHECK`` expression pinning every row to the allowlist (architecture N.1).

    Portable across SQLite and PostgreSQL: string concatenation and ``IN``.
    """
    keys = ", ".join(f"'{k}'" for k in sorted(ALLOWED_TRIPLE_KEYS))
    return f"(from_type || ':' || link_type || ':' || to_type) IN ({keys})"


#: Edges that point *into* a requirement version from its source material.
SOURCE_NODE_TYPES: frozenset[TraceNodeType] = frozenset(
    {N.UTTERANCE, N.SOURCE_CHUNK, N.SOURCE_DOCUMENT}
)
