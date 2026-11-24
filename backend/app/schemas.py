"""Pydantic request bodies for the REST API (input validation)."""
from typing import Literal

from pydantic import BaseModel, Field

from app.security import ROLES


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    domain: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=5000)
    jurisdiction: str = Field(default="India", max_length=60)
    scale: Literal["small", "medium", "large", "enterprise"] = "medium"
    budget: str = Field(default="", max_length=200)
    schedule: str = Field(default="", max_length=200)
    stakeholder_availability: Literal["", "low", "medium", "high", "very high"] = ""


class RequirementReview(BaseModel):
    action: Literal["accept", "reject", "modify"]
    statement: str | None = Field(default=None, max_length=3000)
    comment: str = Field(default="", max_length=2000)


class MappingReview(BaseModel):
    action: Literal["approve", "flag"]
    comment: str = Field(default="", max_length=2000)


class SdlcReview(BaseModel):
    action: Literal["approve", "override"]
    model: str | None = None
    comment: str = Field(default="", max_length=2000)


class WorkflowReview(BaseModel):
    action: Literal["approve", "request_changes"]
    comment: str = Field(default="", max_length=2000)


class CommentBody(BaseModel):
    comment: str = Field(default="", max_length=2000)


class DocumentUpdate(BaseModel):
    allowlisted: bool


class SettingsUpdate(BaseModel):
    retention_days: int | None = Field(default=None, ge=7, le=3650)
    sdlc_weights: dict[str, float] | None = None


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(min_length=2, max_length=200)
    role: Literal[tuple(ROLES)]  # type: ignore[valid-type]
    password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    role: Literal[tuple(ROLES)] | None = None  # type: ignore[valid-type]
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)
