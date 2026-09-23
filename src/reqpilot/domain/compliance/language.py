"""Mandated output language, enforced deterministically (``FR-CMP-006``, ``FR-CMP-007``; K.3).

Approved Phase 0 C.1: ReqPilot says *"potentially applicable"*, *"candidate
mapping"*, *"suggested control"*, *"requires review by a qualified compliance
professional"*. It never says *"is compliant"*, *"satisfies the regulation"* or
*"meets the legal requirement"*.

Two things live here, and both are code, not configuration, so that neither can
be switched off by editing a data file:

* **The prohibited-assertion detector.** A match is a *validation failure*: the
  claim carrying it is dropped and a ``COMPLIANCE_CLAIM_DROPPED`` event is
  written. The text is **never rewritten** into something that looks safe
  (architecture K.3: "a match is a validation failure, not a rewrite"). Prompt
  instructions ask for hedged language too, but the prompt is the request and
  this module is the control.
* **The standing advisory notice** shown on every compliance view and every
  generated compliance artefact. It is a template constant - a model never
  writes it, and a model's output is never shown in its place.

The detector also refuses *authority claims* - text asserting that an approval,
a sign-off or a human review has happened or is unnecessary. Those are decisions
that only a human records, through a gate (architecture M.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Version of the language rules below. Stamped on every mapping it checked.
LANGUAGE_RULES_VERSION = "1.0.0"

#: The standing advisory notice (``FR-CMP-007``). Deterministic, never generated.
COMPLIANCE_ADVISORY_NOTICE = (
    "ReqPilot provides requirements-engineering and evidence-based compliance analysis "
    "support. It does not provide legal advice or a final legal determination. Mappings "
    "are candidate mappings to potentially applicable sources, drawn from a curated, "
    "educational reference corpus that is neither complete nor authoritative. Regulatory "
    "interpretations requiring human judgement must be reviewed by the authorised "
    "Compliance Officer, and high-risk security and privacy requirements by the "
    "authorised Security Reviewer."
)

#: Hedged wording the prompts ask for (C.1). Informational: the control is the detector.
MANDATED_PHRASES: tuple[str, ...] = (
    "potentially applicable",
    "candidate mapping",
    "suggested control",
    "requires review by a qualified compliance professional",
)

# Words that may sit between "is"/"are" and "compliant": "is fully compliant",
# "is now compliant", "are 100% compliant".
_QUALIFIER = (
    r"(?:(?:fully|now|completely|entirely|totally|wholly|already|therefore|thus|clearly|"
    r"legally|100\s*%|100\s+percent)\s+)*"
)
_BE = r"\b(?:is|are|was|were|will\s+be|would\s+be|be|being|been|remains?|becomes?)\b"
_COMPLIANCE_OBJECT = (
    r"(?:the\s+|all\s+(?:the\s+)?|every\s+|any\s+|applicable\s+|relevant\s+)?"
    r"(?:law|laws|regulation|regulations|regulatory\s+requirements?|legal\s+requirements?|"
    r"legal\s+obligations?|statutory\s+requirements?|statute|act|rules?|directions?|"
    r"guidelines?|policy|policies|standard|standards|control|controls|compliance\s+requirements?|"
    r"requirements?\s+of\s+(?:the\s+|this\s+|that\s+|all\s+)?"
    r"(?:law|laws|regulation|regulations|act|regulator|policy|policies|standard|standards|rules?))"
)

#: ``(rule id, pattern)``. Case-insensitive; whitespace-tolerant.
_PROHIBITED: tuple[tuple[str, str], ...] = (
    # "is compliant", "is fully compliant", "are compliant with"
    ("LANG-IS-COMPLIANT", rf"{_BE}\s+{_QUALIFIER}(?:in\s+)?(?:full\s+)?compli(?:ant|ance)\b"),
    # "the system complies with", "fully complies"
    ("LANG-COMPLIES", r"\b(?:fully\s+|completely\s+)?compl(?:ies|y)\s+(?:fully\s+)?with\b"),
    # "satisfies the regulation", "satisfies all legal requirements"
    (
        "LANG-SATISFIES",
        rf"\b(?:satisf(?:y|ies|ied|ying)|fulfil+s?|fulfilled)\s+{_COMPLIANCE_OBJECT}",
    ),
    # "meets the legal requirement", "meets all regulatory requirements"
    ("LANG-MEETS", rf"\b(?:meet|meets|met|meeting)\s+{_COMPLIANCE_OBJECT}"),
    # "guarantees compliance", "ensures full compliance", "achieves compliance"
    (
        "LANG-GUARANTEES",
        r"\b(?:guarantee[sd]?|guaranteeing|ensure[sd]?|ensuring|achieve[sd]?|achieving|"
        r"certif(?:y|ies|ied))\s+(?:full\s+|complete\s+|total\s+|legal\s+|regulatory\s+)?compliance\b",
    ),
    # "in (full) compliance with", "compliance is (assured|confirmed)"
    ("LANG-IN-COMPLIANCE", r"\bin\s+(?:full\s+|complete\s+)?compliance\s+with\b"),
    (
        "LANG-COMPLIANCE-CONFIRMED",
        r"\bcompliance\s+(?:is|has\s+been)\s+(?:assured|confirmed|guaranteed|established|achieved|verified)\b",
    ),
    # "legally compliant", "no compliance gap", "no compliance issues"
    ("LANG-LEGALLY-COMPLIANT", r"\b(?:legally|regulatorily|fully)\s+compliant\b"),
    (
        "LANG-NO-GAP",
        r"\bno\s+(?:compliance|regulatory|legal)\s+(?:gaps?|issues?|risks?|concerns?)\b",
    ),
    # a final legal determination
    (
        "LANG-LEGAL-DETERMINATION",
        r"\b(?:this|it)\s+is\s+(?:lawful|legal|permitted\s+by\s+law|not\s+in\s+breach)\b"
        r"|\blegal\s+(?:determination|opinion|advice)\s*:",
    ),
)

#: Authority claims: the model asserting a decision only a human records.
_AUTHORITY: tuple[tuple[str, str], ...] = (
    (
        "AUTH-APPROVED",
        r"\b(?:is|are|has\s+been|have\s+been|was|were)\s+(?:hereby\s+)?"
        r"(?:approved|signed[\s-]off|baselined|authori[sz]ed\s+for\s+release)\b",
    ),
    (
        "AUTH-MARK",
        r"\bmark(?:ed)?\s+(?:this|it|the\s+requirement)?\s*(?:as\s+)?(?:compliant|approved)\b",
    ),
    (
        "AUTH-NO-REVIEW",
        r"\bno\s+(?:human\s+|further\s+|compliance\s+(?:officer\s+)?|security\s+)?"
        r"(?:review|approval|sign[\s-]?off)\s+(?:is\s+)?(?:needed|required|necessary)\b"
        r"|\b(?:skip|bypass|ignore)\s+(?:the\s+)?(?:compliance\s+officer|security\s+reviewer|"
        r"review|approval|gate|g2|g3)\b",
    ),
    (
        "AUTH-GATE",
        r"\b(?:g2|g3|gate)\s+(?:is\s+|has\s+been\s+)?(?:approved|passed|cleared|not\s+required)\b",
    ),
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule_id, re.compile(pattern, re.IGNORECASE)) for rule_id, pattern in _PROHIBITED + _AUTHORITY
)

#: Every rule id the detector can report. Asserted in tests.
RULE_IDS: frozenset[str] = frozenset(rule_id for rule_id, _ in _COMPILED)


@dataclass(frozen=True)
class LanguageViolation:
    """One prohibited assertion found in generated compliance text."""

    rule_id: str
    #: The matched words, for the reviewer. Never used to rewrite anything.
    matched: str
    #: Which field of the proposal it was found in.
    field: str

    @property
    def is_authority_claim(self) -> bool:
        return self.rule_id.startswith("AUTH-")


#: Typographic quotes, hyphens and the no-break space, folded to ASCII.
_FOLD = str.maketrans(
    {0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"', 0x2010: "-", 0x2011: "-", 0x00A0: " "}
)


def _normalise(text: str) -> str:
    """Collapse whitespace and fold typographic quotes and hyphens, nothing else."""
    folded = text.translate(_FOLD)
    return " ".join(folded.split())


def find_prohibited(text: str | None, *, field: str = "text") -> list[LanguageViolation]:
    """Every prohibited assertion or authority claim in ``text`` (empty if none).

    Deterministic and side-effect free. The caller decides what a violation
    means - for a compliance claim it is always a drop, never a rewrite.
    """
    if not text:
        return []
    normalised = _normalise(text)
    found: list[LanguageViolation] = []
    for rule_id, pattern in _COMPILED:
        match = pattern.search(normalised)
        if match:
            found.append(LanguageViolation(rule_id, match.group(0)[:200], field))
    return found


def check_fields(fields: dict[str, str | None]) -> list[LanguageViolation]:
    """Run :func:`find_prohibited` over every named field of one claim."""
    out: list[LanguageViolation] = []
    for name, value in fields.items():
        out.extend(find_prohibited(value, field=name))
    return out


def assert_artefact_language(text: str) -> None:
    """Refuse to emit a generated compliance artefact containing a prohibited assertion.

    Artefact text is assembled deterministically from validated claims, so this
    should never fire; it is the last line of defence, and it raises rather
    than repairing.
    """
    violations = find_prohibited(text, field="artefact")
    if violations:
        rules = ", ".join(sorted({v.rule_id for v in violations}))
        raise ValueError(f"generated compliance text contains prohibited assertions ({rules})")
