from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PayoutRule(Base):
    """A rule describing how collected funds are eventually distributed.

    trigger_type: THRESHOLD | PERCENTAGE | MILESTONE | DATE | MANUAL | HYBRID
    status:       LOCKED (conditions unmet) | TRIGGERED | EXECUTED | CANCELLED
    """

    __tablename__ = "payout_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id"), nullable=True, index=True
    )
    beneficiary_id: Mapped[int | None] = mapped_column(
        ForeignKey("beneficiaries.id"), nullable=True
    )
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    percentage: Mapped[float | None] = mapped_column(nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), default="MANUAL")
    threshold_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    threshold_percentage: Mapped[float | None] = mapped_column(nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requires_approval: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="LOCKED", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    beneficiary = relationship("Beneficiary", lazy="joined")


class Payout(Base):
    """A payout execution record.

    State machine (PRODUCT.md §26):
      DRAFT (awaiting approval) -> READY -> PROCESSING -> SUCCESS | FAILED
      DRAFT -> LOCKED (funds unavailable) -> READY
      PROCESSING -> UNKNOWN (timeout/5xx) -> verify -> SUCCESS | FAILED
    """

    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    beneficiary_id: Mapped[int] = mapped_column(ForeignKey("beneficiaries.id"))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("payout_rules.id"), nullable=True
    )
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id"), nullable=True, index=True
    )
    kora_reference: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    beneficiary = relationship("Beneficiary", lazy="joined")
    milestone = relationship("Milestone", lazy="joined")
