"""Audit hash-chain tests (ADR-010).

Pure-function tests: no database, no I/O. The chain is one of the mechanisms
the architecture's accountability claim rests on, so it is tested at the level
where it can be exhaustively exercised.
"""

from __future__ import annotations

import datetime as dt

import pytest

from reqpilot.services.audit.hashing import (
    canonical_payload,
    compute_row_hash,
    verify_chain,
)

pytestmark = pytest.mark.unit

FIXED_TIME = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def make_event(
    event_id: str,
    prev_hash: str | None,
    *,
    payload: dict | None = None,
    event_type: str = "RUN_STARTED",
) -> dict:
    payload = payload if payload is not None else {"ref": event_id}
    row_hash = compute_row_hash(
        event_id=event_id,
        project_id="proj-1",
        occurred_at=FIXED_TIME,
        actor_kind="human",
        actor_ref="user-1",
        event_type=event_type,
        subject_type="project",
        subject_id="proj-1",
        payload=payload,
        prev_hash=prev_hash,
    )
    return {
        "id": event_id,
        "project_id": "proj-1",
        "occurred_at": FIXED_TIME,
        "actor_kind": "human",
        "actor_ref": "user-1",
        "event_type": event_type,
        "subject_type": "project",
        "subject_id": "proj-1",
        "payload": payload,
        "prev_hash": prev_hash,
        "row_hash": row_hash,
    }


def build_chain(length: int) -> list[dict]:
    events: list[dict] = []
    prev: str | None = None
    for i in range(length):
        event = make_event(f"evt-{i}", prev)
        events.append(event)
        prev = event["row_hash"]
    return events


# --- canonicalisation ----------------------------------------------------


def test_payload_serialisation_is_order_independent() -> None:
    """Dict ordering must not change the hash, or chains would be unstable."""
    assert canonical_payload({"a": 1, "b": 2}) == canonical_payload({"b": 2, "a": 1})


def test_hash_is_deterministic() -> None:
    args = {
        "event_id": "e1",
        "project_id": "p1",
        "occurred_at": FIXED_TIME,
        "actor_kind": "human",
        "actor_ref": "u1",
        "event_type": "RUN_STARTED",
        "subject_type": None,
        "subject_id": None,
        "payload": {"x": 1},
        "prev_hash": None,
    }
    assert compute_row_hash(**args) == compute_row_hash(**args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", "e2"),
        ("actor_ref", "u2"),
        ("event_type", "RUN_FAILED"),
        ("payload", {"x": 2}),
        ("prev_hash", "deadbeef"),
    ],
)
def test_every_meaningful_field_changes_the_hash(field: str, value: object) -> None:
    """If a field could be tampered with, altering it must break the hash."""
    base = {
        "event_id": "e1",
        "project_id": "p1",
        "occurred_at": FIXED_TIME,
        "actor_kind": "human",
        "actor_ref": "u1",
        "event_type": "RUN_STARTED",
        "subject_type": None,
        "subject_id": None,
        "payload": {"x": 1},
        "prev_hash": None,
    }
    modified = {**base, field: value}
    assert compute_row_hash(**base) != compute_row_hash(**modified)  # type: ignore[arg-type]


# --- chain verification --------------------------------------------------


def test_intact_chain_verifies() -> None:
    assert verify_chain(build_chain(5)) == (True, None)


def test_empty_chain_verifies() -> None:
    assert verify_chain([]) == (True, None)


def test_tampered_payload_is_detected() -> None:
    chain = build_chain(5)
    chain[2]["payload"] = {"ref": "tampered"}
    ok, index = verify_chain(chain)
    assert ok is False
    assert index == 2


def test_tampered_actor_is_detected() -> None:
    """The case that matters most: rewriting who did something."""
    chain = build_chain(4)
    chain[1]["actor_ref"] = "someone-else"
    ok, index = verify_chain(chain)
    assert ok is False
    assert index == 1


def test_deleted_event_is_detected() -> None:
    """Removing a link breaks the chain at the removal point."""
    chain = build_chain(5)
    del chain[2]
    ok, index = verify_chain(chain)
    assert ok is False
    assert index == 2


def test_reordered_events_are_detected() -> None:
    chain = build_chain(5)
    chain[1], chain[3] = chain[3], chain[1]
    ok, index = verify_chain(chain)
    assert ok is False
    assert index == 1


def test_recomputed_hash_after_tamper_still_fails() -> None:
    """A sophisticated tamper - editing a row *and* rehashing it - still fails.

    This is what the chain buys over per-row checksums: the successor's
    ``prev_hash`` no longer matches, so a single-row rewrite is not enough.
    """
    chain = build_chain(4)
    chain[1]["payload"] = {"ref": "tampered"}
    chain[1]["row_hash"] = compute_row_hash(
        event_id=chain[1]["id"],
        project_id=chain[1]["project_id"],
        occurred_at=chain[1]["occurred_at"],
        actor_kind=chain[1]["actor_kind"],
        actor_ref=chain[1]["actor_ref"],
        event_type=chain[1]["event_type"],
        subject_type=chain[1]["subject_type"],
        subject_id=chain[1]["subject_id"],
        payload=chain[1]["payload"],
        prev_hash=chain[1]["prev_hash"],
    )
    ok, index = verify_chain(chain)
    assert ok is False
    assert index == 2  # the successor's prev_hash no longer matches
