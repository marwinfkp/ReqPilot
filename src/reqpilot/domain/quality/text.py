"""Text primitives for the deterministic quality and conflict rules (P5).

Pure, explainable, reproducible: word-boundary phrase matching that returns the
exact span it matched, a crude stemmer for content-word overlap, and a small
quantity parser that turns "at least 500 concurrent sessions" or "within 2
seconds" into a unit class, a normalised value and a bound. Nothing here is
semantic; the rules built on it are deliberately conservative, and anything
they cannot settle goes to a human (or, for conflicts, to the adjudicator).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")

#: Function words, modal verbs and comparators: never content.
STOPWORDS: frozenset[str] = frozenset(
    """a an the of to for and or in on at by with from into onto as than then so that this
    these those it its their them they which who whom be is are was were been being shall
    must will should may might can could would system able any all each every also per via
    such least most maximum minimum max min up within above below over under more less only
    not no never cannot exceed exceeding after before during while when if unless except
    same other use using used do does done have has had one""".split()  # noqa: SIM905
)

_SUFFIXES = ("ations", "ation", "itions", "ition", "ions", "ion", "ings", "ing",
             "ies", "ied", "ed", "es", "s", "e")  # fmt: skip


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def stem(word: str) -> str:
    """A deliberately crude stemmer: enough to match *delete/deleted/deletion*."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            return base + "y" if suffix in ("ies", "ied") else base
    return word


def content_stems(text: str, *, extra_stop: Iterable[str] = ()) -> frozenset[str]:
    """Stemmed content words: no function words, no numbers, no unit words."""
    stop = STOPWORDS | UNIT_WORDS | frozenset(extra_stop)
    return frozenset(stem(w) for w in words(text) if w not in stop and not w[0].isdigit())


def overlap_coefficient(a: frozenset[str], b: frozenset[str]) -> float:
    """|A ∩ B| / min(|A|, |B|) - generous to a short statement inside a long one."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    quote: str


def find_phrases(text: str, phrases: Iterable[str]) -> list[Span]:
    """Every occurrence of any phrase, on word boundaries, case-insensitively.

    Overlapping matches keep the longest (``easy to use`` beats ``easy``).
    """
    found: list[Span] = []
    for phrase in phrases:
        pattern = r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])"
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            found.append(Span(match.start(), match.end(), text[match.start() : match.end()]))
    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    kept: list[Span] = []
    for span in found:
        if kept and span.start < kept[-1].end:
            if span.end - span.start > kept[-1].end - kept[-1].start:
                kept[-1] = span
            continue
        kept.append(span)
    return kept


def has_prefix_stem(text: str, stems: Iterable[str]) -> bool:
    """Whether any word starts with a stem, or any multi-word stem occurs."""
    lowered = text.lower()
    tokens = words(lowered)
    for item in stems:
        if " " in item or "/" in item:
            if item in lowered:
                return True
        elif any(t.startswith(item) for t in tokens):
            return True
    return False


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------

_TIME_UNITS: dict[str, float] = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400, "week": 604800, "weeks": 604800,
    "month": 2_592_000, "months": 2_592_000, "year": 31_536_000, "years": 31_536_000,
}  # fmt: skip
_SIZE_UNITS: dict[str, float] = {"kb": 0.001, "mb": 1, "gb": 1000, "tb": 1_000_000}
_MONEY_UNITS = {"eur", "euro", "euros", "usd", "gbp", "dollars", "pounds"}
_COUNT_NOUNS = {
    "user", "users", "session", "sessions", "request", "requests", "transaction",
    "transactions", "attempt", "attempts", "application", "applications", "record",
    "records", "entry", "entries", "document", "documents", "item", "items", "call", "calls",
    "file", "files", "login", "logins", "account", "accounts", "retry", "retries",
}  # fmt: skip

#: Words that are units of some quantity - excluded from content overlap.
UNIT_WORDS: frozenset[str] = frozenset(
    set(_TIME_UNITS) | set(_SIZE_UNITS) | _MONEY_UNITS | {"percent", "working", "business"}
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "sixty": 60, "hundred": 100,
}  # fmt: skip

_LOWER = ("at least", "minimum of", "a minimum of", "no fewer than", "no less than", "more than",
          "greater than", "over", "above", "exceeding", "from", "support")  # fmt: skip
_UPPER = ("at most", "maximum of", "a maximum of", "maximum size of", "a maximum size of",
          "up to", "no more than", "within", "under", "below", "less than", "not exceed",
          "only", "limit", "limited to", "max")  # fmt: skip

_NUMBER = re.compile(
    r"(?<![\w.:€$])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d:])"
    r"|(?<![\w-])(?P<word>" + "|".join(_NUMBER_WORDS) + r")(?![\w-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Quantity:
    """A bound on one attribute: ``lower <= value <= upper``, in base units."""

    unit_class: str  # time | size | money | percent | count:<noun>
    lower: float
    upper: float
    quote: str

    def disjoint_from(self, other: Quantity) -> bool:
        return self.upper < other.lower or other.upper < self.lower

    @property
    def point(self) -> float:
        return self.lower if math.isfinite(self.lower) else self.upper


def _unit(after: str) -> tuple[str, float] | None:
    tokens = words(after)[:4]
    if after.lstrip().startswith("%"):
        return "percent", 1.0
    if not tokens:
        return None
    first = tokens[0]
    if first in ("working", "business") and len(tokens) > 1 and tokens[1] in ("day", "days"):
        return "time", 86400.0
    if first in _TIME_UNITS:
        return "time", float(_TIME_UNITS[first])
    if first in _SIZE_UNITS:
        return "size", _SIZE_UNITS[first]
    if first in _MONEY_UNITS:
        return "money", 1.0
    if first in ("percent",):
        return "percent", 1.0
    for token in tokens[:3]:
        if token in _COUNT_NOUNS:
            return f"count:{stem(token)}", 1.0
    return None


def _around(text: str, start: int, end: int) -> str:
    """A few words either side of a match, cut on word boundaries (a substring of text)."""
    left = text.rfind(" ", 0, max(0, start - 18))
    left = 0 if left < 0 else left + 1
    right = text.find(" ", min(len(text), end + 14))
    right = len(text) if right < 0 else right
    return text[left:right].strip(" ,.;")


def quantities(text: str) -> list[Quantity]:
    """The measurable bounds a statement states, in base units (seconds, MB, ...)."""
    found: list[Quantity] = []
    for match in _NUMBER.finditer(text):
        if match.group("num"):
            value = float(match.group("num").replace(",", ""))
        else:
            value = float(_NUMBER_WORDS[match.group("word").lower()])
        before = text[max(0, match.start() - 30) : match.start()]
        unit = _unit(text[match.end() : match.end() + 40])
        if unit is None and before.rstrip().endswith(("€", "$", "£")):
            unit = ("money", 1.0)
        if unit is None:
            continue
        unit_class, scale = unit
        value *= scale
        window = " ".join(words(before)[-4:])
        lower, upper = value, value
        if any(window.endswith(p) or f" {p} " in f" {window} " for p in _UPPER):
            lower = -math.inf
        elif any(window.endswith(p) or f" {p} " in f" {window} " for p in _LOWER):
            upper = math.inf
        found.append(Quantity(unit_class, lower, upper, _around(text, match.start(), match.end())))
    return found
