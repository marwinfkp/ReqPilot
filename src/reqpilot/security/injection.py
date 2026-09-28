"""Heuristic prompt-injection detection (architecture Q.4; ``FR-ADM-005``; roadmap P11).

**Detection is a supplement; the structural controls are the defence** (Q.4).
Nothing here blocks anything and nothing here is relied on for a governance
guarantee. What stops an injected document from approving a baseline is that
the gate is not reading the document (J.1, Q.3): approvals need a human
``ApprovalDecision``, agent roles hold no approval capability, and every model
output is a typed proposal that deterministic code validates.

What detection adds is visibility. A hit at ingestion tags the chunk or
utterance, raises ``INJECTION_SUSPECTED`` (codes and positions only, never the
text) and shows on the source page, so an analyst sees that a document tried
to talk to the machine. The gateway scans every data block again before a
prompt is assembled and reports the labels of flagged blocks with the call.

The rules are deterministic regular expressions for the idioms of Q.4:
imperative phrasing aimed at an assistant, instruction-override idioms,
role-play framing, fence and chat-template spoofing, and encoded blocks - plus
the attempts this domain cares about (approval and state manipulation,
permission escalation, exfiltration). They are knowingly incomplete: a
paraphrase, another language, or an instruction split across chunks can pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reqpilot.domain.enums import InjectionSignal

_I = re.IGNORECASE

_RULES: tuple[tuple[InjectionSignal, re.Pattern[str]], ...] = (
    (
        InjectionSignal.INSTRUCTION_OVERRIDE,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?\b"
            r"(?:previous|prior|above|earlier|all|your|the|any|system)\b[^.\n]{0,20}?\b"
            r"(?:instructions?|rules?|prompts?|guidelines|directives|constraints)\b"
            r"|\bnew instructions?\s*:"
            r"|\binstead,? (?:you (?:must|should|will)|do the following)\b",
            _I,
        ),
    ),
    (
        InjectionSignal.ROLE_PLAY,
        re.compile(
            r"\byou are now\b|\bfrom now on,? you\b|\bpretend (?:to be|you are)\b"
            r"|\bact as (?:an? |the )?(?:admin|administrator|system|developer|root|"
            r"superuser|compliance officer|approver)\b"
            r"|\b(?:jailbreak|dan mode|developer mode)\b",
            _I,
        ),
    ),
    (
        InjectionSignal.SYSTEM_PROMPT_PROBE,
        re.compile(
            r"\b(?:reveal|print|show|repeat|output|leak|disclose|tell me)\b[^.\n]{0,20}?\b"
            r"(?:system prompt|hidden prompt|your (?:instructions|prompt|rules)|"
            r"the (?:instructions|prompt) (?:above|you were given))",
            _I,
        ),
    ),
    (
        InjectionSignal.APPROVAL_MANIPULATION,
        re.compile(
            r"\b(?:approve|sign[- ]off|baseline)\s+(?:this|the|these|all|every|my)\s+"
            r"(?:\w+\s+)?(?:requirements?|baselines?|risks?|gates?|tasks?|mappings?|findings?|"
            r"changes?|workflows?|documents?|versions?|controls?)\b"
            r"|\bmark (?:it|this|them|(?:the|every|all|each) \w+)(?: \w+)? as (?:approved|"
            r"baselined|passed|accepted|compliant|low risk|resolved|validated)\b"
            r"|\bset (?:the |its )?(?:status|state|decision|severity|risk level|lifecycle)"
            r"(?: \w+)? to\b"
            r"|\b(?:gate|g[1-8]) (?:is |has )?(?:passed|approved|cleared)\b"
            r"|\bauto-?approve\b|\bbypass (?:the )?(?:gate|approval|review|g[1-8])\b"
            r"|\b(?:suppress|omit|drop|hide) (?:the |this |any )?(?:security |compliance |"
            r"privacy )?(?:finding|risk|gap|control)s?\b",
            _I,
        ),
    ),
    (
        InjectionSignal.PERMISSION_ESCALATION,
        re.compile(
            r"\bgrant (?:yourself|me|the (?:agent|model|assistant))\b"
            r"|\b(?:escalate|elevate|widen) (?:your |my |the )?(?:privileges?|permissions?|"
            r"access|capabilit(?:y|ies))\b"
            r"|\byou (?:now )?have (?:admin|root|full|superuser) (?:access|rights|"
            r"permissions?)\b"
            r"|\b(?:is_superuser|may_write|capability token|roles_by_project)\b",
            _I,
        ),
    ),
    (
        InjectionSignal.DATA_EXFILTRATION,
        re.compile(
            r"\b(?:send|email|e-mail|export|upload|post|leak|exfiltrate|forward|reveal)\b"
            r"[^.\n]{0,30}?\b(?:all (?:the )?(?:data|records|requirements|documents)|"
            r"confidential|secrets?|api keys?|credentials|passwords?|customer data|"
            r"personal data)\b"
            r"|\b(?:other|another|every|all) projects?(?:'s)? (?:data|requirements|records|"
            r"documents|risks)\b",
            _I,
        ),
    ),
    (
        InjectionSignal.DELIMITER_SPOOF,
        re.compile(
            r"<<<\s*(?:END|UNTRUSTED)\b|</?\s*(?:system|assistant|user|instructions?)\s*>"
            r"|\[/?INST\]|<\|(?:im_start|im_end|system|endoftext)\|>"
            r"|^\s*#{2,}\s*(?:system|instructions?)\b|\bBEGIN SYSTEM PROMPT\b",
            _I | re.MULTILINE,
        ),
    ),
    (
        InjectionSignal.ENCODED_BLOCK,
        re.compile(
            r"(?<![A-Za-z0-9+/=])(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[a-z])"
            r"(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/=])"
        ),
    ),
)


@dataclass(frozen=True)
class InjectionHit:
    """One signal and where it matched. Carries no text."""

    signal: InjectionSignal
    start: int
    end: int


def scan(text: str) -> list[InjectionHit]:
    """Every rule match in ``text``, in text order."""
    if not isinstance(text, str) or not text:
        return []
    hits = [
        InjectionHit(signal, m.start(), m.end())
        for signal, pattern in _RULES
        for m in pattern.finditer(text)
    ]
    return sorted(hits, key=lambda h: (h.start, h.end, h.signal.value))


def signals(text: str) -> tuple[InjectionSignal, ...]:
    """The distinct signals in ``text``, in a stable order (empty when none)."""
    found = {hit.signal for hit in scan(text)}
    return tuple(sorted(found, key=lambda s: s.value))


def is_suspected(text: str) -> bool:
    return bool(scan(text))
