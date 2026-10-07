"""Request schemas (input validation for the REST API)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------------ auth
class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


# --------------------------------------------------------------- campaign
Visibility = Literal["PUBLIC", "UNLISTED", "PRIVATE"]
Category = Literal[
    "Medical", "Education", "Community", "NGO", "Charity", "Emergency",
    "Family", "Business", "Religious", "Event", "Other",
]


class CampaignCreateIn(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    short_description: str | None = Field(default=None, max_length=500)
    description: str = ""
    category: Category = "Other"
    visibility: Visibility = "PUBLIC"
    currency: str = Field(default="NGN", max_length=8)
    target_amount: float = Field(gt=0)
    deadline: datetime | None = None
    cover_image: str | None = Field(default=None, max_length=1024)
    is_diaspora: bool = False


class CampaignUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=255)
    short_description: str | None = Field(default=None, max_length=500)
    description: str | None = None
    category: Category | None = None
    visibility: Visibility | None = None
    target_amount: float | None = Field(default=None, gt=0)
    deadline: datetime | None = None
    cover_image: str | None = None
    is_diaspora: bool | None = None
    status: Literal["ACTIVE", "PAUSED", "COMPLETED"] | None = None


class MemberAddIn(BaseModel):
    email: str
    role: Literal["ADMIN", "CONTRIBUTOR", "VIEWER"] = "VIEWER"


class UpdateCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = ""
    image_url: str | None = Field(default=None, max_length=1024)
    milestone_id: int | None = None


# ---------------------------------------------------------- contributions
class ContributeIn(BaseModel):
    amount: float = Field(gt=0)
    anonymous: bool = False
    payment_method: Literal["bank_transfer", "card"] = "bank_transfer"


# ------------------------------------------------------------ beneficiaries
class BeneficiaryCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    bank_code: str = Field(min_length=1, max_length=16)
    account_number: str = Field(min_length=6, max_length=32)
    beneficiary_type: Literal["individual", "business"] = "individual"


# ---------------------------------------------------------------- milestones
class MilestoneCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    amount: float = Field(gt=0)
    beneficiary_id: int | None = None
    trigger_type: Literal["APPROVAL", "THRESHOLD", "HYBRID", "DATE"] = "APPROVAL"
    threshold_amount: float | None = None
    threshold_percentage: float | None = Field(default=None, ge=0, le=100)
    due_date: datetime | None = None
    requires_approval: bool = True
    order_index: int | None = None


class MilestoneUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    amount: float | None = Field(default=None, gt=0)
    beneficiary_id: int | None = None
    threshold_amount: float | None = None
    threshold_percentage: float | None = Field(default=None, ge=0, le=100)
    due_date: datetime | None = None
    requires_approval: bool | None = None


# -------------------------------------------------------------- payout rules
class PayoutRuleCreateIn(BaseModel):
    """Standalone payout rule (PRODUCT.md §11-12).

    MILESTONE-trigger rules are created together with milestones instead.
    """

    beneficiary_id: int
    label: str | None = Field(default=None, max_length=255)
    amount: float | None = Field(default=None, gt=0)
    percentage: float | None = Field(default=None, gt=0, le=100)
    trigger_type: Literal["THRESHOLD", "PERCENTAGE", "DATE", "MANUAL", "HYBRID"]
    threshold_amount: float | None = Field(default=None, gt=0)
    threshold_percentage: float | None = Field(default=None, gt=0, le=100)
    due_date: datetime | None = None
    requires_approval: bool = False


