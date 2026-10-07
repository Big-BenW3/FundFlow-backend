"""Milestone ORM model (PRODUCT.md §12, §25).

Represents fund release conditions and stages within a campaign lifecycle.
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Milestone(Base):
    """Programmable fundraising milestone (PRODUCT.md §25).

    status: PENDING (not yet approved) | APPROVED (ready/awaiting payout)
          | PAID (payout completed)
    trigger_type: APPROVAL | THRESHOLD | HYBRID | DATE
    """

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    beneficiary_id: Mapped[int | None] = mapped_column(
        ForeignKey("beneficiaries.id"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(String(16), default="APPROVAL")
    threshold_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    threshold_percentage: Mapped[float | None] = mapped_column(nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    beneficiary = relationship("Beneficiary", lazy="joined")
