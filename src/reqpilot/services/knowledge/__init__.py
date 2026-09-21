"""Module M5 services - knowledge-base administration, scope, retrieval, evidence.

The orchestration layer over :mod:`reqpilot.retrieval` (pure components) and
:mod:`reqpilot.repositories.knowledge` (the SQL, including the allowlist join).
Every operation authorises through ``policy.can`` in the repository layer and
audits through the one append-only audit service.
"""

from reqpilot.services.knowledge.admin import ItemSpec, KnowledgeAdminService, SourceSpec
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.knowledge.retrieval import RetrievalService
from reqpilot.services.knowledge.scope import KnowledgeScopeService, ProjectKnowledgeScope
from reqpilot.services.knowledge.seed import SeedSummary, load_manifest, seed_from_manifest

__all__ = [
    "EvidenceService",
    "ItemSpec",
    "KnowledgeAdminService",
    "KnowledgeScopeService",
    "ProjectKnowledgeScope",
    "RetrievalService",
    "SeedSummary",
    "SourceSpec",
    "load_manifest",
    "seed_from_manifest",
]
