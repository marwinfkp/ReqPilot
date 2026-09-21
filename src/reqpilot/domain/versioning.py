"""Exact-version binding for approvals (architecture G.7, M.3).

``approval_decision.subject_version_hash`` is what makes "approved *exactly this
version*" verifiable. The architecture's rule: a later edit changes the hash, so
the earlier decision no longer covers it, and a change gate is required.

The hash therefore covers **everything a reviewer would have read** when they
approved. If a field is not in this list, changing it would silently keep an old
approval valid - so anything governance-relevant belongs here.

Pure functions, no I/O, so the binding is unit-testable without a database.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Bumping this invalidates every stored hash, so it is versioned deliberately
#: rather than changed in place.
VERSION_HASH_ALGORITHM = "sha256-v1"


def canonical_version_payload(
    *,
    requirement_id: str,
    human_id: str,
    version_no: int,
    statement: str,
    category: str | None,
    priority: str | None,
    justification: str | None,
    dependencies: list[str] | None,
    assumptions: list[str] | None,
    source_refs: list[dict[str, Any]] | None,
) -> str:
    """Serialise the governed content of a version deterministically.

    Sorted keys, no insignificant whitespace, and list order preserved (order is
    meaningful for dependencies), so the same logical version always hashes
    identically regardless of dictionary ordering.

    Deliberately **excluded**: the lifecycle state, timestamps, the creator, and
    the review signal. State changes are transitions rather than content edits,
    and an approval must survive the very transition it causes -
    ``PENDING_APPROVAL → APPROVED`` must not invalidate the decision that drove it.
    """
    payload = {
        "algorithm": VERSION_HASH_ALGORITHM,
        "requirement_id": requirement_id,
        "human_id": human_id,
        "version_no": version_no,
        "statement": statement,
        "category": category or "",
        "priority": priority or "",
        "justification": justification or "",
        "dependencies": list(dependencies or []),
        "assumptions": list(assumptions or []),
        "source_refs": list(source_refs or []),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_version_hash(**kwargs: Any) -> str:
    """Return the governed-content hash of a requirement version.

    Accepts the same keyword arguments as :func:`canonical_version_payload`.
    """
    canonical = canonical_version_payload(**kwargs)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hashes_match(stored: str | None, current: str | None) -> bool:
    """Compare two version hashes in a way that treats ``None`` as no match.

    A missing hash is never equal to anything, including another missing hash:
    an approval that recorded no binding cannot be said to cover any version.
    """
    if not stored or not current:
        return False
    return stored == current
