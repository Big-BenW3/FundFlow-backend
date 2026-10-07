"""Contribution ORM model (PRODUCT.md §18).

Represents a financial contribution to a campaign, tracking payment status and attribution.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Contribution(Base):
    __tablename__ = "contributions"

    # status: PENDING | SUCCESS | FAILED | REFUNDED
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    kora_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    contributor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    contributor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anonymous: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    payment_method: Mapped[str] = mapped_column(String(32), default="bank_transfer")
    payer_account_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payer_bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transaction_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
