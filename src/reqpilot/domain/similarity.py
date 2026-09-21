"""Deterministic near-duplicate measures for requirement statements (``FR-EXT-005``).

Deliberately simple and explainable, because they decide what a human is asked
to look at:

* **Exact duplicate** - the normalised token sequences are identical. Case,
  punctuation, whitespace and the shared "The system shall" opening are
  ignored; every word and its order must match. This is the only relation that
  is merged without a human, and nothing is lost when it is.
* **Similarity** - Jaccard overlap of the normalised token *sets*. It is a
  prompt for review, never a reason to merge: "shall encrypt" and "shall not
  encrypt" differ by one token.

No embeddings are used. Project text is not embedded until masking exists
(architecture J.2), and a lexical measure is reproducible across machines.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")

#: The declarative opening every normalised statement shares (``FR-EXT-003``).
#: Removed before comparison so that it does not inflate similarity.
_OPENING = ("the", "system", "shall")


def normalised_tokens(statement: str) -> tuple[str, ...]:
    tokens = tuple(_TOKEN.findall(statement.lower()))
    if tokens[: len(_OPENING)] == _OPENING:
        tokens = tokens[len(_OPENING) :]
    return tokens


def is_exact_duplicate(a: str, b: str) -> bool:
    """Whether two statements are identical up to case, punctuation and spacing."""
    left, right = normalised_tokens(a), normalised_tokens(b)
    return bool(left) and left == right


def token_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of the two statements' normalised token sets, in [0, 1]."""
    left, right = set(normalised_tokens(a)), set(normalised_tokens(b))
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)
