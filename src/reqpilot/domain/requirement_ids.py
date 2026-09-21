"""Human-readable requirement identifiers (`FR-EXT-004`, architecture G.4).

The approved convention is::

    FR-<DOMAIN>-nnn      functional
    NFR-<DOMAIN>-nnn     non-functional

for example ``FR-LOAN-014``. The identifier is stable for the life of the
requirement and is **independent of the version number**: ``FR-LOAN-014`` has
versions 1, 2, 3 …

Allocation is deterministic and per project: the next free sequence number for a
given (kind, domain) pair. No randomness, so a test can assert the exact id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from reqpilot.domain.errors import RequirementIdError


class RequirementKind(StrEnum):
    """The two identifier prefixes the approved convention allows."""

    FUNCTIONAL = "FR"
    NON_FUNCTIONAL = "NFR"


#: ``FR``/``NFR``, an uppercase alphanumeric domain token, and a zero-padded
#: sequence of at least three digits. Anchored, so partial matches are refused.
REQUIREMENT_ID_PATTERN = re.compile(r"^(FR|NFR)-([A-Z][A-Z0-9]{1,15})-(\d{3,})$")

#: Width the sequence number is padded to when allocating a new identifier.
SEQUENCE_WIDTH = 3


@dataclass(frozen=True)
class RequirementIdParts:
    """A parsed human identifier."""

    kind: RequirementKind
    domain: str
    sequence: int

    def render(self) -> str:
        return f"{self.kind}-{self.domain}-{self.sequence:0{SEQUENCE_WIDTH}d}"


def parse_requirement_id(human_id: str) -> RequirementIdParts:
    """Parse and validate a human identifier.

    Raises :class:`RequirementIdError` with an actionable message rather than
    returning ``None``, because every call site treats a malformed id as a
    hard input error.
    """
    if not isinstance(human_id, str) or not human_id:
        raise RequirementIdError("requirement id must be a non-empty string")

    match = REQUIREMENT_ID_PATTERN.match(human_id)
    if match is None:
        raise RequirementIdError(
            f"{human_id!r} does not match the approved convention "
            "FR-<DOMAIN>-nnn or NFR-<DOMAIN>-nnn (for example FR-LOAN-014)"
        )

    prefix, domain, sequence = match.groups()
    return RequirementIdParts(
        kind=RequirementKind(prefix),
        domain=domain,
        sequence=int(sequence),
    )


def is_valid_requirement_id(human_id: str) -> bool:
    """Return whether ``human_id`` matches the approved convention."""
    try:
        parse_requirement_id(human_id)
    except RequirementIdError:
        return False
    return True


def normalise_domain(domain: str) -> str:
    """Return the domain token in canonical form, or raise.

    Domains are uppercased so that ``loan`` and ``LOAN`` cannot produce two
    parallel identifier series for the same thing.
    """
    if not domain or not isinstance(domain, str):
        raise RequirementIdError("requirement domain must be a non-empty string")
    candidate = domain.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", candidate):
        raise RequirementIdError(
            f"{domain!r} is not a valid requirement domain token: expected 2-16 "
            "alphanumeric characters beginning with a letter, e.g. LOAN"
        )
    return candidate


def next_requirement_id(
    kind: RequirementKind,
    domain: str,
    existing_ids: object,
) -> str:
    """Allocate the next free identifier for ``(kind, domain)``.

    ``existing_ids`` is any iterable of identifiers already in use *within the
    project*. Identifiers from other (kind, domain) pairs are ignored, and
    malformed ones are skipped rather than raising - a stored id that no longer
    parses must not make it impossible to create new requirements.
    """
    canonical_domain = normalise_domain(domain)
    highest = 0
    for existing in existing_ids:  # type: ignore[attr-defined]
        try:
            parts = parse_requirement_id(existing)
        except RequirementIdError:
            continue
        if parts.kind is kind and parts.domain == canonical_domain:
            highest = max(highest, parts.sequence)

    return RequirementIdParts(kind=kind, domain=canonical_domain, sequence=highest + 1).render()
