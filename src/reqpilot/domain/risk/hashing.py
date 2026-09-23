"""The content hash that binds a G8 decision to exactly the risk a reviewer saw.

A G8 task is raised against one risk row. The task records the risk's content
hash; the approval service recomputes it at decision time, so a decision can
never cover content the decider did not see, and an edit after the task was
raised invalidates the task rather than silently widening the approval.

For a **requirement-level** risk the hash includes the requirement version's own
content hash, which ties the decision to one exact, immutable version: a later
version is a different subject with its own analysis, and no risk, task or
decision transfers across versions. A **project-level** risk has no version, so
its hash covers the project and the risk's own content instead - the binding is
the same mechanism, with one fewer component.

The authoritative severity, the two ratings and the matrix version are all in the
hash, so a risk cannot be re-rated under a decision taken on the old rating.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

#: Bumping this invalidates every stored P7 hash, so it is versioned deliberately.
P7_HASH_VERSION = "p7-1"


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def risk_hash(
    *,
    project_id: str,
    scope: str,
    requirement_version_id: str | None,
    version_content_hash: str | None,
    category: str,
    title: str,
    description: str,
    likelihood: str,
    impact: str,
    severity: str,
    matrix_version: str,
    likelihood_rationale: str,
    impact_rationale: str,
    evidence_ids: Sequence[str],
) -> str:
    """The hash a G8 task binds to. Covers content, ratings and the computed severity."""
    return _digest(
        {
            "v": P7_HASH_VERSION,
            "kind": "risk",
            "project_id": project_id,
            "scope": scope,
            "requirement_version_id": requirement_version_id,
            "version_content_hash": version_content_hash,
            "category": category,
            "title": title,
            "description": description,
            "likelihood": likelihood,
            "impact": impact,
            "severity": severity,
            "matrix_version": matrix_version,
            "likelihood_rationale": likelihood_rationale,
            "impact_rationale": impact_rationale,
            "evidence_ids": sorted(evidence_ids),
        }
    )
