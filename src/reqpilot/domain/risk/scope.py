"""The ``FR-RSK-011`` scope guard: ReqPilot risk is never borrower credit risk.

> ReqPilot is a **requirements-engineering and SDLC-recommendation system for a
> loan-origination software project**. It is **not** a loan-origination system.
> (approved Phase 0 D.1)

Adding risk analysis to a lending case study creates a specific, foreseeable
scope-drift hazard (approved Phase 0 N, risk R5): because the requirements are
about loans, a model - or a person - starts producing *borrower* risk. A
``RiskItem`` in ReqPilot is always a project, engineering, security, privacy,
compliance or operational risk arising from a requirement. It is never a
borrower's creditworthiness, a customer risk rating, a probability of default
or a fraud score.

Architecture I.1 puts the guard in three places, and all three are structural:

1. ``risk.category`` admits only the six approved values, so there is no
   ``credit``, ``financial`` or ``borrower`` category to put such a risk in;
2. **this module** - the validation-stage check that refuses a proposal whose
   text indicates borrower-level scoring;
3. no risk row has a foreign key to any customer or applicant entity, *because
   no such entity exists in the schema*.

Like the P6 language rules, this is **code, not configuration**: there is no
setting that disables it, no threshold to tune it down, and a proposal it
refuses is dropped and audited rather than stored with a warning.

**What it deliberately does not refuse.** A loan-origination project has
perfectly legitimate project risks that mention credit systems - "the credit
bureau integration may time out under load", "a defect could cause valid
applications to be rejected", "the fraud controls may be insufficient". Those
are technical, operational and security risks about *the system being built*,
and blocking them would make the guard useless. Every pattern below therefore
requires an unambiguous borrower-level scoring or creditworthiness sense, and
the false-alarm cases are part of the frozen benchmark.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: Bumped whenever a pattern changes, and recorded on every risk row, so a
#: later change never silently re-judges history.
SCOPE_RULES_VERSION = "1.0.0"

#: U+2019, the typographic apostrophe, as a codepoint so this source stays ASCII
#: (the P6 language detector keeps its own typographic characters the same way).
_RIGHT_QUOTE = chr(0x2019)

#: What a refusal says. Template text, never a model's words.
SCOPE_REFUSAL_NOTICE = (
    "Refused as out of scope: ReqPilot analyses project, engineering, security, "
    "privacy, compliance and operational risk. It does not compute borrower "
    "credit risk, customer risk ratings, probability of default or fraud scores, "
    "and it makes no lending decision (FR-RSK-011)."
)


@dataclass(frozen=True)
class ScopeHit:
    """One out-of-scope phrase, with the rule that caught it."""

    rule_id: str
    #: The offending phrase, as written, bounded so an audit payload stays a
    #: reference rather than a copy of the text.
    phrase: str
    field: str


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    pattern: re.Pattern[str]
    why: str


def _rule(rule_id: str, pattern: str, why: str) -> _Rule:
    return _Rule(rule_id, re.compile(pattern, re.IGNORECASE), why)


#: The patterns. Each one is a borrower-level credit, default or fraud-scoring
#: sense that cannot be read as a risk to the project or the system.
_RULES: tuple[_Rule, ...] = (
    _rule(
        "credit_scoring",
        r"\bcredit[\s\-]?(scor(?:e|es|ing)|ratings?|risks?|grades?|grading|assessments?|"
        r"decisions?|decisioning|limit\s+decisions?|worthiness)\b",
        "credit scoring, credit rating or credit risk of a borrower",
    ),
    _rule(
        "creditworthiness",
        r"\bcredit[\s\-]?worth(?:y|iness)\b",
        "an assessment of a borrower's creditworthiness",
    ),
    _rule(
        "default_probability",
        r"\b(?:probability|likelihood|chance)\s+of\s+(?:default|non[\s\-]?payment|repayment)\b"
        r"|\bdefault\s+(?:probability|rate)\b"
        r"|\bdefault\s+risk\b(?!\s*(?:level|value|setting|config|configuration))"
        r"|\bloan\s+default\b",
        "a probability or rate of borrower default",
    ),
    _rule(
        "credit_loss_modelling",
        r"\b(?:loss\s+given\s+default|exposure\s+at\s+default|expected\s+credit\s+loss"
        r"|delinquenc(?:y|ies)|charge[\s\-]?off)\b",
        "credit-loss modelling of a borrower portfolio",
    ),
    _rule(
        "borrower_risk_rating",
        # The apostrophe class covers both the ASCII and the typographic form;
        # the latter is written as a codepoint so the source stays ASCII.
        r"\b(?:borrower|applicant|customer|consumer|client)(?:['" + _RIGHT_QUOTE + r"]s)?\s+"
        r"(?:credit\s+)?(?:risk\s+(?:rating|score|grade|profile|band|tier)|"
        r"creditworthiness|default\s+risk|risk\s+segment)\b"
        r"|\brisk\s+(?:rating|score|grade|profile|band|tier)\s+(?:for|of)\s+"
        r"(?:the\s+|each\s+|an?\s+)?(?:borrower|applicant|customer|consumer|client)\b",
        "a risk rating assigned to a borrower or customer",
    ),
    _rule(
        "fraud_scoring",
        # "fraud controls" and "fraud risk" are legitimate security concerns and
        # are deliberately not matched; a fraud *score* about a person is not.
        r"\bfraud\s+(?:scor(?:e|es|ing)|ratings?|probabilit(?:y|ies)|propensity)\b",
        "a fraud score computed about a person",
    ),
    _rule(
        "lending_eligibility",
        r"\b(?:credit|financial|loan)\s+eligibility\s+(?:decisions?|scores?|scoring|model)\b"
        r"|\bunderwrit(?:e|ing)\s+(?:risks?|decisions?|scores?|scoring)\b"
        r"|\brisk[\s\-]based\s+pricing\b",
        "a lending eligibility, underwriting or pricing decision",
    ),
)


def find_out_of_scope(text: str | None, *, field: str = "text") -> list[ScopeHit]:
    """Every out-of-scope phrase in ``text``. Empty means the text is in scope."""
    if not text:
        return []
    hits: list[ScopeHit] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            hits.append(ScopeHit(rule.rule_id, match.group(0)[:80], field))
    return hits


def check_fields(fields: dict[str, str | None]) -> list[ScopeHit]:
    """Run the guard over several named fields of one proposal, in a stable order."""
    hits: list[ScopeHit] = []
    for name, value in fields.items():
        hits.extend(find_out_of_scope(value, field=name))
    return hits


def why(rule_ids: Iterable[str]) -> list[str]:
    """The human-readable reason for each rule id, for an audit payload."""
    reasons = {rule.rule_id: rule.why for rule in _RULES}
    return [reasons[rule_id] for rule_id in dict.fromkeys(rule_ids) if rule_id in reasons]


def rule_ids() -> tuple[str, ...]:
    """Every rule id, so a test can assert the set has not silently shrunk."""
    return tuple(rule.rule_id for rule in _RULES)
