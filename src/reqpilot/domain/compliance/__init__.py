"""Deterministic compliance and security/privacy logic (roadmap phase P6).

Pure functions, no I/O, no model: the parts of P6 that hold authority.

* :mod:`.language` - mandated output language (``FR-CMP-006``) and the standing
  advisory notice (``FR-CMP-007``).
* :mod:`.gaps` - expected-control gap detection (``FR-CMP-002``; K.2).
* :mod:`.risk` - the authoritative security/privacy risk level that G3 reads
  (``FR-SEC-003``; I.7, I.8).
"""

from reqpilot.domain.compliance.gaps import (
    COVERING_STATUSES,
    CoveringMapping,
    compute_gaps,
    covered_controls,
)
from reqpilot.domain.compliance.language import (
    COMPLIANCE_ADVISORY_NOTICE,
    LANGUAGE_RULES_VERSION,
    LanguageViolation,
    assert_artefact_language,
    check_fields,
    find_prohibited,
)
from reqpilot.domain.compliance.risk import (
    ARCHITECTURE_HIGH_IMPACT_FAMILIES,
    RiskEvaluation,
    catalogue_floor,
    evaluate_risk,
    highest,
    normalise_proposed_level,
)

__all__ = [
    "ARCHITECTURE_HIGH_IMPACT_FAMILIES",
    "COMPLIANCE_ADVISORY_NOTICE",
    "COVERING_STATUSES",
    "LANGUAGE_RULES_VERSION",
    "CoveringMapping",
    "LanguageViolation",
    "RiskEvaluation",
    "assert_artefact_language",
    "catalogue_floor",
    "check_fields",
    "compute_gaps",
    "covered_controls",
    "evaluate_risk",
    "find_prohibited",
    "highest",
    "normalise_proposed_level",
]
