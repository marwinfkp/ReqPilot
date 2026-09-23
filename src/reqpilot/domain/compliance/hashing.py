"""Content hashes that bind a G2/G3 decision to exactly what the reviewer saw.

A G2 task is raised against one compliance mapping, a G3 task against one
security/privacy finding. The task records the subject's content hash; the
approval service recomputes it at decision time, so a decision can never cover
content the decider did not see. Both hashes include the requirement version's
own content hash, which ties the decision to one exact, immutable version: a
later version is a different subject with its own analysis, and nothing
transfers across versions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

#: Bumping this invalidates every stored P6 hash, so it is versioned deliberately.
P6_HASH_VERSION = "p6-1"


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mapping_hash(
    *,
    project_id: str,
    requirement_version_id: str,
    version_content_hash: str,
    control_key: str,
    checklist_ref: str,
    relationship: str,
    rationale: str,
    candidate_text: str | None,
    implied_obligation: str | None,
    jurisdiction: str,
    source_type: str,
    evidence_ids: Sequence[str],
    is_high_impact: bool,
) -> str:
    return _digest(
        {
            "v": P6_HASH_VERSION,
            "kind": "compliance_mapping",
            "project_id": project_id,
            "requirement_version_id": requirement_version_id,
            "version_content_hash": version_content_hash,
            "control_key": control_key,
            "checklist_ref": checklist_ref,
            "relationship": relationship,
            "rationale": rationale,
            "candidate_text": candidate_text,
            "implied_obligation": implied_obligation,
            "jurisdiction": jurisdiction,
            "source_type": source_type,
            "evidence_ids": sorted(evidence_ids),
            "is_high_impact": is_high_impact,
        }
    )


def finding_hash(
    *,
    project_id: str,
    requirement_version_id: str,
    version_content_hash: str,
    category: str,
    family: str,
    derived_requirement: str,
    proposed_risk_level: str | None,
    risk_level: str,
    risk_rules_version: str,
    evidence_ids: Sequence[str],
) -> str:
    return _digest(
        {
            "v": P6_HASH_VERSION,
            "kind": "security_privacy_finding",
            "project_id": project_id,
            "requirement_version_id": requirement_version_id,
            "version_content_hash": version_content_hash,
            "category": category,
            "family": family,
            "derived_requirement": derived_requirement,
            "proposed_risk_level": proposed_risk_level,
            "risk_level": risk_level,
            "risk_rules_version": risk_rules_version,
            "evidence_ids": sorted(evidence_ids),
        }
    )
