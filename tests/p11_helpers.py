"""Shared fixtures for the P11 guardrails tests. Synthetic data only.

**Every identifier here is synthetic.** The card numbers are the payment
industry's published test numbers; the IBAN is the ISO 13616 published example;
the Aadhaar-format numbers are built here from arbitrary digits plus a computed
Verhoeff check digit; the PAN, GSTIN, IFSC, UPI handle, phone number, e-mail
address and account references are invented. None identifies a person, an
account or an institution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.capabilities import CapabilityToken, mint_capability
from reqpilot.domain.enums import ActorKind, AgentRole, MaskCategory, Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.llm import LLMRequest
from reqpilot.security.masking import verhoeff_check_digit


def _aadhaar(first_eleven: str) -> str:
    digits = first_eleven + verhoeff_check_digit(first_eleven)
    return f"{digits[:4]} {digits[4:8]} {digits[8:]}"


@dataclass(frozen=True)
class SyntheticIdentifier:
    category: MaskCategory
    value: str
    #: How it appears in running text, around the value.
    sentence: str


#: The synthetic financial-identifier corpus (one per masker category, two for the
#: formats that are written more than one way).
SYNTHETIC_IDENTIFIERS: tuple[SyntheticIdentifier, ...] = (
    SyntheticIdentifier(
        MaskCategory.CARD_NUMBER,
        "4111 1111 1111 1111",
        "The applicant paid the fee with card 4111 1111 1111 1111 yesterday.",
    ),
    SyntheticIdentifier(
        MaskCategory.CARD_NUMBER,
        "5555555555554444",
        "A second card, 5555555555554444, was declined.",
    ),
    SyntheticIdentifier(
        MaskCategory.IBAN,
        "GB82 WEST 1234 5698 7654 32",
        "Refunds go to IBAN GB82 WEST 1234 5698 7654 32 for overseas applicants.",
    ),
    SyntheticIdentifier(
        MaskCategory.AADHAAR,
        _aadhaar("23456789012"),
        f"Her Aadhaar {_aadhaar('23456789012')} was verified through e-KYC.",
    ),
    SyntheticIdentifier(
        MaskCategory.AADHAAR,
        _aadhaar("98765432109").replace(" ", ""),
        f"Aadhaar {_aadhaar('98765432109').replace(' ', '')} appears on the scanned form.",
    ),
    SyntheticIdentifier(
        MaskCategory.PAN,
        "ABCPE1234F",
        "The PAN ABCPE1234F must be validated against the tax registry.",
    ),
    SyntheticIdentifier(
        MaskCategory.GSTIN,
        "27ABCPE1234F1Z5",
        "The employer's GSTIN is 27ABCPE1234F1Z5.",
    ),
    SyntheticIdentifier(
        MaskCategory.IFSC,
        "SYNT0004321",
        "Disburse to branch IFSC SYNT0004321 only after the second approval.",
    ),
    SyntheticIdentifier(
        MaskCategory.EMAIL,
        "applicant.one@example.com",
        "Send the sanction letter to applicant.one@example.com.",
    ),
    SyntheticIdentifier(
        MaskCategory.UPI_ID,
        "applicant.one@oksynth",
        "The EMI mandate is registered on UPI handle applicant.one@oksynth.",
    ),
    SyntheticIdentifier(
        MaskCategory.PHONE,
        "+91 98765 43210",
        "Call the applicant on +91 98765 43210 before disbursement.",
    ),
    SyntheticIdentifier(
        MaskCategory.ACCOUNT_NUMBER,
        "123456789012",
        "Salary is credited to account no. 123456789012 every month.",
    ),
    SyntheticIdentifier(
        MaskCategory.LOAN_ACCOUNT,
        "LN-2024-000123",
        "Top-ups are booked against loan account LN-2024-000123.",
    ),
)

RAW_VALUES: tuple[str, ...] = tuple(i.value for i in SYNTHETIC_IDENTIFIERS)


def identifier_text() -> str:
    """A synthetic workshop transcript whose requirements mention every identifier."""
    lines = [
        "Facilitator: Thanks for joining the loan intake workshop (synthetic).",
        "Priya Nair (fictional): Applicants must be able to upload their income documents "
        "when they apply online.",
    ]
    lines += [f"Omar Haddad (fictional): {i.sentence}" for i in SYNTHETIC_IDENTIFIERS]
    return "\n".join(lines) + "\n"


def leaked(texts: Any, values: tuple[str, ...] = RAW_VALUES) -> list[str]:
    """Which raw values appear anywhere in ``texts`` (strings, or repr of anything)."""
    blob = texts if isinstance(texts, str) else repr(texts)
    compact = blob.replace(" ", "")
    return [v for v in values if v in blob or v.replace(" ", "") in compact]


def request_text(request: LLMRequest) -> str:
    return request.instructions + "\n".join(request.untrusted_content.values())


def token_for(
    role: AgentRole, project_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None
) -> CapabilityToken:
    return mint_capability(
        run_id=run_id or uuid.uuid4(), project_id=project_id or uuid.uuid4(), role=role
    )


def agent(project_id: uuid.UUID, token: CapabilityToken | None, *roles: Role) -> Actor:
    """An agent-role actor carrying ``token`` (and, uselessly, human roles)."""
    return Actor(
        actor_id=ActorId(uuid.uuid4()),
        kind=ActorKind.AGENT_ROLE,
        roles_by_project={ProjectId(project_id): frozenset(roles)} if roles else {},
        capability=token,
    )


def count_rows(session: Session, model: Any, **where: Any) -> int:
    from sqlalchemy import func, select

    stmt = select(func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return int(session.execute(stmt).scalar_one())
