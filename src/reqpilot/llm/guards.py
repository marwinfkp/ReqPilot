"""Egress guards: what may never cross the gateway (``FR-ING-003``, QA-PRV, P #5).

Two deterministic rules, checked on every request before any provider sees it:

1. **No application secret leaves in a prompt.** The configured API key, the
   application secret key and the database password are searched for in the
   instructions and in every content block. A hit refuses the request.
2. **No unmasked project content leaves the machine unless it is synthetic.**
   Masking (``FR-ING-003``) is not implemented yet (roadmap P11). Until it is,
   project content may be sent to a provider that ``leaves_machine`` only if
   every block came from a source its uploader declared *synthetic*. Real or
   unclassified data therefore cannot reach an external model - the OpenAI
   provider included - until masking exists.

Both refusals raise :class:`EgressRefusedError`, a security event rather than a
data-quality event. Neither rule depends on the prompt asking nicely.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from reqpilot.config import Settings
from reqpilot.domain.errors import EgressRefusedError
from reqpilot.llm.types import ContentBlock, TrustClass

#: Shorter values would collide with ordinary words; none of the real secrets
#: this protects is this short.
MIN_SECRET_LENGTH = 8


def configured_secrets(settings: Settings) -> frozenset[str]:
    """The secret values in the configuration that must never appear in a prompt."""
    values: set[str] = set()
    if settings.llm_api_key:
        values.add(settings.llm_api_key)
    if settings.secret_key:
        values.add(settings.secret_key)
    try:
        password = make_url(settings.database_url).password
    except ArgumentError:  # pragma: no cover - a malformed URL fails elsewhere first
        password = None
    if password:
        values.add(str(password))
    return frozenset(v for v in values if len(v) >= MIN_SECRET_LENGTH)


def assert_no_secrets(texts: Iterable[str], secrets: frozenset[str]) -> None:
    for text in texts:
        for secret in secrets:
            if secret in text:
                raise EgressRefusedError(
                    "an application secret appeared in an outgoing prompt; the request was "
                    "refused before reaching any provider"
                )


def assert_egress_permitted(blocks: Iterable[ContentBlock], *, leaves_machine: bool) -> None:
    """Refuse unmasked, non-synthetic project content bound for another machine."""
    if not leaves_machine:
        return
    for block in blocks:
        if block.trust_class is not TrustClass.PROJECT_CONTENT:
            continue
        if not (block.masked or block.synthetic):
            raise EgressRefusedError(
                f"content block {block.label!r} is unmasked project content that is not "
                "declared synthetic; it may not leave this machine until masking "
                "(FR-ING-003) is in place"
            )
