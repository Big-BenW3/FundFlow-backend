"""Campaign ORM model (PRODUCT.md §9-11).

Represents a fundraising campaign with collection account, goals, rules, and lifecycle management.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.user import User


class Campaign(Base):
    __tablename__ = "campaigns"

    # visibility: PUBLIC | UNLISTED | PRIVATE  (PRODUCT.md §9)
    # status:     DRAFT | ACTIVE | PAUSED | COMPLETED | CANCELLED
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    short_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="Other")
    visibility: Mapped[str] = mapped_column(String(16), default="PUBLIC")
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    target_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    raised_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    disbursed_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0")
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    is_diaspora: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[CampaignAccount | None] = relationship(
        back_populates="campaign", uselist=False, cascade="all, delete-orphan"
    )
    members: Mapped[list[CampaignMember]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    owner: Mapped["User"] = relationship(foreign_keys=[owner_id], lazy="joined")


class CampaignAccount(Base):
    """Kora collection account for a campaign (PRODUCT.md §38).

    NOTE: Kora virtual accounts do not hold balances like bank accounts —
    this is a collection reference only; FundFlow's ledger is the source
    of truth for campaign funds (PRODUCT.md §5).
    """

    __tablename__ = "campaign_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="kora")
    kora_account_reference: Mapped[str] = mapped_column(String(255), index=True)
    kora_account_number: Mapped[str] = mapped_column(String(32), index=True)
    bank_name: Mapped[str] = mapped_column(String(128))
    bank_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="account")


class CampaignMember(Base):
    """Access control for campaign collaboration (PRODUCT.md §29).

    Roles: OWNER | ADMIN | CONTRIBUTOR | VIEWER
    """

    __tablename__ = "campaign_members"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="VIEWER")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="members")


class CampaignUpdate(Base):
    """Progress updates posted by campaign owners (PRODUCT.md §36)."""

    __tablename__ = "campaign_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
