"""Sensitive-data masking (``FR-ING-003``; architecture J.2, P #5; roadmap P11).

P3 fixed the *position* of the masking stage - after parsing, before anything is
segmented, stored for prompting, embedded or sent to a model - and shipped
:class:`NoMasking`, an honest "nothing was masked". P11 puts a protective
masker, :class:`PatternMasker`, behind the same :class:`Masker` protocol and
makes it the default. It runs in three places:

1. **Ingestion** - every uploaded source document and every recorded interview
   or clarification utterance is masked before it is segmented or stored, so
   stored offsets, prompt text and citations all agree on the masked text.
2. **The LLM gateway** - every data block and every operator parameter is
   masked again immediately before a prompt is assembled (defence in depth:
   whatever reached a prompt by another path is masked there).
3. **The audit log** - string values in an audit payload are masked before the
   row is hashed, so an identifier can never become part of the immutable
   chain.

**What it detects, and how** (deterministic regular expressions; a checksum
wherever the identifier has one, to keep false positives down):

==================  ===========================================================
Category            Rule
==================  ===========================================================
``card_number``     13-19 digits (first 1-9), optional space/hyphen grouping,
                    Luhn-valid
``iban``            country code + check digits + 11-30 alphanumerics, ISO
                    13616 mod-97 valid
``aadhaar``         12 digits (4-4-4 or contiguous), first digit 2-9,
                    Verhoeff-valid
``gstin``           15-character Indian GST identification number layout
``pan``             Indian PAN layout: 5 letters (4th = holder type), 4 digits,
                    1 letter
``ifsc``            Indian bank branch code: 4 letters, ``0``, 6 alphanumerics
``email``           ``local@domain.tld``
``upi_id``          ``handle@psp`` (an ``@`` address with no top-level domain;
                    the handle has no underscore)
``phone``           Indian mobile (optional ``+91``/``0``, 10 digits starting
                    6-9) or an international ``+`` number
``account_number``  9-18 digits following "account"/"a/c"/"acct" (+ "no.")
``loan_account``    8-20 alphanumerics (at least 4 digits) following "loan
                    account/ref/id"
==================  ===========================================================

**What it does not do.** It is pattern- and heuristic-based, not a classifier:
it will not recognise a person's name, an address, a date of birth, a free-text
description of a customer, an identifier in a format it does not list (SWIFT/BIC
codes are omitted deliberately - their shape matches ordinary upper-case words),
an identifier split across lines or written out in words, or a bare digit run
with no context (an account number without the word "account"). A checksum
also means a *mistyped* card number or Aadhaar number is not masked. It may
mask something that is not sensitive (a 10-digit number starting 6-9 that is not
a phone number). ReqPilot handles synthetic data only (approved Phase 0 C.3);
masking is a control on top of that rule, not a substitute for it.

**The unmasking map** is returned with each result (:attr:`MaskResult.entries`)
and stored separately, project-scoped, by the ingestion services - never in a
prompt, a log, an audit payload or an API response (J.2). ``MaskEntry.value`` is
excluded from ``repr``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from reqpilot.domain.enums import MaskCategory, MaskingStatus

#: The identifier of the P11 masker, recorded on every masked source.
PATTERN_MASKER_ID = "pattern-masker@1"

#: The shape of a replacement: ``[MASKED_PAN_1]``. Contains nothing any rule matches,
#: so masking is idempotent.
TOKEN_RE = re.compile(r"\[MASKED_[A-Z_]+_\d+\]")


@dataclass(frozen=True)
class MaskEntry:
    """One replaced value: its token, what kind it was, and (never printed) what it was."""

    token: str
    category: MaskCategory
    value: str = field(repr=False)


@dataclass(frozen=True)
class MaskResult:
    """The text after the masking stage, and what the stage actually did."""

    text: str
    status: MaskingStatus
    masker_id: str
    #: How many values were replaced. Counts only, never the values.
    replacements: int = 0
    #: The unmasking map for this text. Stored apart from everything a prompt reaches.
    entries: tuple[MaskEntry, ...] = field(default=(), repr=False)

    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[str(entry.category)] = counts.get(str(entry.category), 0) + 1
        return dict(sorted(counts.items()))


@runtime_checkable
class Masker(Protocol):
    """A masking stage. ``is_protective`` must be true only if it really masks."""

    @property
    def masker_id(self) -> str: ...

    @property
    def is_protective(self) -> bool: ...

    def mask(self, text: str) -> MaskResult: ...


class NoMasking:
    """The P3 stage: returns the text unchanged and says so.

    Kept for tests and for an explicit opt-out; never the default since P11.
    ``is_protective`` is false and every result is ``NOT_MASKED``, which is what
    the gateway's egress rule reads.
    """

    masker_id = "none"
    is_protective = False

    def mask(self, text: str) -> MaskResult:
        return MaskResult(text=text, status=MaskingStatus.NOT_MASKED, masker_id=self.masker_id)


# --- checksums ---------------------------------------------------------------------


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][int(char)]]
    return check == 0


def verhoeff_check_digit(digits: str) -> str:
    """The digit that makes ``digits + it`` Verhoeff-valid (for building synthetic data)."""
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[(index + 1) % 8][int(char)]]
    return str(_VERHOEFF_INV[check])


def iban_valid(compact: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


# --- the rules ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    category: MaskCategory
    pattern: re.Pattern[str]
    #: Which group is the sensitive value (0 = the whole match).
    group: int = 0
    check: Callable[[str], bool] | None = None


def _digits(value: str) -> str:
    return re.sub(r"[ -]", "", value)


def _card(value: str) -> bool:
    digits = _digits(value)
    return 13 <= len(digits) <= 19 and luhn_valid(digits)


def _aadhaar(value: str) -> bool:
    digits = _digits(value)
    return len(digits) == 12 and verhoeff_valid(digits)


def _iban(value: str) -> bool:
    return iban_valid(value.replace(" ", ""))


def _loan_ref(value: str) -> bool:
    return sum(c.isdigit() for c in value) >= 4


#: Ordered by precedence: where two rules match overlapping text, the earlier
#: start wins, then the longer match, then the rule listed first here.
RULES: tuple[_Rule, ...] = (
    _Rule(
        MaskCategory.ACCOUNT_NUMBER,
        re.compile(
            r"(?i)\b(?:a/c|acct|account)(?:\s*(?:no\.?|number|#))?\s*[:#-]?\s*(\d{9,18})(?!\d)"
        ),
        group=1,
    ),
    _Rule(
        MaskCategory.LOAN_ACCOUNT,
        re.compile(
            r"(?i)\bloan\s+(?:account|a/c|acct|ref(?:erence)?|id)(?:\s*(?:no\.?|number|#))?"
            r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{6,18}[A-Z0-9])\b"
        ),
        group=1,
        check=_loan_ref,
    ),
    _Rule(
        MaskCategory.CARD_NUMBER,
        re.compile(r"(?<![\w-])[1-9](?:[ -]?\d){12,18}(?![\w-])"),
        check=_card,
    ),
    _Rule(
        MaskCategory.IBAN,
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"),
        check=_iban,
    ),
    _Rule(
        MaskCategory.AADHAAR,
        re.compile(r"(?<![\w-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\w-])"),
        check=_aadhaar,
    ),
    _Rule(MaskCategory.GSTIN, re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    _Rule(MaskCategory.PAN, re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b")),
    _Rule(MaskCategory.IFSC, re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    _Rule(
        MaskCategory.EMAIL,
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    ),
    _Rule(
        MaskCategory.UPI_ID,
        # No underscore in the handle (NPCI: alphanumerics, "." and "-"), which
        # keeps ``name_of_ruleset@test``-style references out.
        re.compile(
            r"(?<![\w.@-])[A-Za-z0-9][A-Za-z0-9.-]{1,63}@[A-Za-z]{2,32}\b(?![.@]?[A-Za-z0-9])"
        ),
    ),
    _Rule(
        MaskCategory.PHONE,
        re.compile(
            r"(?<![\w+-])(?:(?:\+91|0091)[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?![\w-])"
            r"|(?<![\w+])\+[1-9]\d{0,2}[ -]?\d{2,5}(?:[ -]?\d{2,5}){1,3}(?![\w-])"
        ),
    ),
)


@dataclass(frozen=True)
class Detection:
    """Where one identifier is in a text. Carries no value."""

    category: MaskCategory
    start: int
    end: int


def detect(text: str) -> list[Detection]:
    """Every identifier the rules recognise, non-overlapping, in text order."""
    if not isinstance(text, str) or not text:
        return []
    candidates: list[tuple[int, int, int, MaskCategory]] = []
    for priority, rule in enumerate(RULES):
        for match in rule.pattern.finditer(text):
            start, end = match.span(rule.group)
            if start < 0 or end <= start:
                continue
            value = text[start:end]
            if TOKEN_RE.fullmatch(value):
                continue
            if rule.check is not None and not rule.check(value):
                continue
            candidates.append((start, end, priority, rule.category))
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    chosen: list[Detection] = []
    cursor = -1
    for start, end, _priority, category in candidates:
        if start < cursor:
            continue
        chosen.append(Detection(category, start, end))
        cursor = end
    return chosen


class PatternMasker:
    """The P11 masker: deterministic, pattern- and checksum-based (see module docstring)."""

    masker_id = PATTERN_MASKER_ID
    is_protective = True

    def mask(self, text: str) -> MaskResult:
        if not isinstance(text, str):
            raise TypeError("the masker takes text")
        detections = detect(text)
        tokens: dict[tuple[MaskCategory, str], str] = {}
        per_category: dict[MaskCategory, int] = {}
        entries: list[MaskEntry] = []
        out: list[str] = []
        cursor = 0
        for found in detections:
            value = text[found.start : found.end]
            key = (found.category, value)
            token = tokens.get(key)
            if token is None:
                per_category[found.category] = per_category.get(found.category, 0) + 1
                token = f"[MASKED_{found.category.value.upper()}_{per_category[found.category]}]"
                tokens[key] = token
                entries.append(MaskEntry(token, found.category, value))
            out.append(text[cursor : found.start])
            out.append(token)
            cursor = found.end
        out.append(text[cursor:])
        return MaskResult(
            text="".join(out),
            status=MaskingStatus.MASKED,
            masker_id=self.masker_id,
            replacements=len(detections),
            entries=tuple(entries),
        )


def default_masker() -> Masker:
    """The masker every ingestion path uses (P11: protective)."""
    return PatternMasker()


def mask_text(text: str) -> str:
    """The masked form of ``text`` (for egress and audit paths that keep no map)."""
    return PatternMasker().mask(text).text if text else text


def mask_structure(value: Any) -> Any:
    """``value`` with every string inside it masked - dicts, lists and tuples walked."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: mask_structure(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_structure(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_structure(v) for v in value)
    return value


def contains_identifier(texts: Iterable[str]) -> bool:
    """Whether any text still contains something the rules recognise."""
    return any(detect(t) for t in texts if isinstance(t, str))
