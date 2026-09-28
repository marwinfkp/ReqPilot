"""Redaction of retained audit payloads after a project is deleted (``FR-ADM-006``; P.2).

Architecture P.2: deletion retains audit events "with content-bearing payload
fields redacted" - who did what, when, survives; what the text said does not.

**Why redaction happens on read.** ``audit_event`` is append-only at three
levels (ADR-010): the application has no update path, the database refuses
``UPDATE``/``DELETE`` on the table, and every row's hash covers its payload. A
rewritten payload would therefore be *both* a bypass of the immutability the
architecture relies on *and* a broken hash chain an auditor could no longer
verify. P11 resolves this conflict in ADR-010's favour: stored rows are never
changed, and every path that reads a deleted project's audit trail - the API,
the UI viewer, replay and chain verification output - passes each payload
through :func:`redact_payload`. The O.1 rule (payloads carry references only,
enforced at append time) and P11's masking of payload strings before hashing
are what keep the stored bytes free of content in the first place; redaction
removes whatever free text remains from view. This is recorded as a
deliberate, documented deviation from a literal reading of P.2.

**The rule.** A value survives only if it has the shape of a reference:
``None``, a boolean, a number, or a single token (no whitespace) of at most 100
characters - ids, hashes, enum values, version tags, timestamps. Anything else
is replaced by :data:`REDACTED`, as is every value under a key that names
free text, whatever its shape.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"

#: Keys whose values are free text by name (or could be), redacted whatever their shape.
CONTENT_KEYS = frozenset(
    {
        "label",
        "detail",
        "note",
        "explanation",
        "before",
        "after",
        "resolution",
        "notice",
        "errors",
        "title",
        "name",
        "question",
        "answer",
        "rationale",
        "justification",
        "comment",
        "description",
        "quote",
        "statement",
        "reason",
        "text",
    }
)

_TOKEN = re.compile(r"^[^\s]{1,100}$")


def _value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if _TOKEN.match(value) else REDACTED
    if isinstance(value, list | tuple):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return redact_payload(value)
    return REDACTED


def redact_payload(payload: Any) -> dict[str, Any]:
    """``payload`` with every content-bearing value replaced (see module docstring)."""
    if not isinstance(payload, dict):
        return {"payload": REDACTED}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).lower() in CONTENT_KEYS and value is not None:
            out[str(key)] = REDACTED
        else:
            out[str(key)] = _value(value)
    return out
