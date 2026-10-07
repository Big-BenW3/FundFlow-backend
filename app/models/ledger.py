from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LedgerEntry(Base):
    """Immutable financial ledger (PRODUCT.md §23).

    Entry types: CONTRIBUTION | PAYOUT_PENDING | PAYOUT_SUCCESS |
    PAYOUT_FAILED | REFUND | ADJUSTMENT | FEE

    Accounting convention (balance = SUM(amount)):
    - CONTRIBUTION       +amount   (money in, verified)
    - REFUND / FEE       -amount   (money out to contributor/platform)
    - ADJUSTMENT         ±amount   (manual correction)
    - PAYOUT_PENDING     -amount   (hold when a payout is initiated)
    - PAYOUT_SUCCESS      0        (marker: hold settles, money left)
    - PAYOUT_FAILED      +amount   (release of a failed payout hold)

    Entries are never updated or deleted.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    type: Mapped[str] = mapped_column(String(24), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
