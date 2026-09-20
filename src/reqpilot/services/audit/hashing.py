"""Audit hash-chain computation (architecture ADR-010).

Pure functions, no I/O, no database. Kept separate from the service so the
chain logic is unit-testable offline - which matters because this is one of the
mechanisms the architecture's accountability claim rests on.

The chain is **per project**: each project's events form an independent chain.
That keeps verification O(events in one project) and means deleting a project
cannot invalidate another project's chain.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

#: Bumping this invalidates existing chains, so it is versioned deliberately.
HASH_ALGORITHM = "sha256"
GENESIS_HASH: str | None = None


def canonical_payload(payload: dict[str, Any]) -> str:
    """Serialise a payload deterministically.

    Sorted keys and no insignificant whitespace, so the same logical payload
    always hashes identically regardless of dictionary ordering.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def canonical_timestamp(value: dt.datetime) -> str:
    """Render a timestamp so it survives a database round-trip unchanged.

    This matters more than it looks. The hash is computed on write from a
    timezone-aware value, and recomputed on verification from whatever the
    database returned. PostgreSQL returns an aware value; SQLite returns a naive
    one. Formatting the raw object would make the two differ and every chain
    would appear tampered with.

    So: aware values are converted to UTC, naive values are assumed to be UTC
    already - which they are, because :func:`reqpilot.domain.models.base.utc_now`
    is the only writer - and both render without an offset suffix.
    """
    if value.tzinfo is not None:
        value = value.astimezone(dt.UTC)
    return value.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")


def compute_row_hash(
    *,
    event_id: str,
    project_id: str | None,
    occurred_at: dt.datetime,
    actor_kind: str,
    actor_ref: str,
    event_type: str,
    subject_type: str | None,
    subject_id: str | None,
    payload: dict[str, Any],
    prev_hash: str | None,
) -> str:
    """Return the hash binding this event to its predecessor.

    Every field that an auditor would care about is covered, so altering any of
    them breaks the chain. ``prev_hash`` is included, which is what makes the
    structure a chain rather than a set of independent checksums: changing an
    early event invalidates every subsequent hash.
    """
    parts = [
        event_id,
        project_id or "",
        canonical_timestamp(occurred_at),
        actor_kind,
        actor_ref,
        event_type,
        subject_type or "",
        subject_id or "",
        canonical_payload(payload),
        prev_hash or "",
    ]
    # A separator that cannot appear in a UUID, an ISO timestamp or a JSON
    # document, so concatenation is unambiguous.
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def verify_chain(events: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """Verify a project's chain in order.

    Returns ``(True, None)`` when intact, or ``(False, index)`` naming the first
    event whose stored hash does not match its recomputed value, or whose
    ``prev_hash`` does not match its predecessor.
    """
    expected_prev: str | None = GENESIS_HASH
    for index, event in enumerate(events):
        if event.get("prev_hash") != expected_prev:
            return False, index
        recomputed = compute_row_hash(
            event_id=event["id"],
            project_id=event.get("project_id"),
            occurred_at=event["occurred_at"],
            actor_kind=event["actor_kind"],
            actor_ref=event["actor_ref"],
            event_type=event["event_type"],
            subject_type=event.get("subject_type"),
            subject_id=event.get("subject_id"),
            payload=event.get("payload", {}),
            prev_hash=event.get("prev_hash"),
        )
        if recomputed != event["row_hash"]:
            return False, index
        expected_prev = event["row_hash"]
    return True, None
