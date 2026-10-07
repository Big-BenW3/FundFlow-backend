from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Beneficiary(Base):
    """Payout destination, verified through Kora bank resolve (PRODUCT.md §13-14)."""

    __tablename__ = "beneficiaries"
    __table_args__ = (UniqueConstraint("campaign_id", "bank_code", "account_number"),)

    # verification_status: PENDING | VERIFIED | FAILED
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    beneficiary_type: Mapped[str] = mapped_column(String(32), default="individual")
    bank_code: Mapped[str] = mapped_column(String(16))
    bank_name: Mapped[str] = mapped_column(String(128))
    account_number: Mapped[str] = mapped_column(String(32))
    resolved_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    verification_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
