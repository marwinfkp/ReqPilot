"""Documentation (role #11, architecture C.6; ``FR-DOC-001``..``-010``).

Deterministic assembly of the P8 artefacts from an approved baseline, their
immutable versions and section-level traceability, and their Markdown / DOCX /
CSV exports. The structured model and the renderers live in
:mod:`reqpilot.artifacts` (module M8).
"""

from reqpilot.services.documents.service import (
    DEFAULT_SET,
    ArtifactService,
    Export,
    GenerationOutcome,
)

__all__ = ["DEFAULT_SET", "ArtifactService", "Export", "GenerationOutcome"]
