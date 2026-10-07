"""Immutable campaign ledger (PRODUCT.md §23-24).

Balance model (balance = SUM of all entry amounts):

    CONTRIBUTION     +amount   verified money in
    REFUND / FEE     -amount   money out
    ADJUSTMENT       ±amount   manual corrections
    PAYOUT_PENDING   -amount   hold when a payout starts
    PAYOUT_SUCCESS    0        marker — hold settles (money left)
    PAYOUT_FAILED    +amount   release of a failed hold

Entries are append-only: never updated, never deleted (Rule 6).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.ledger import LedgerEntry
from app.models.payout import Payout

RAISED_TYPES = ("CONTRIBUTION", "REFUND", "ADJUSTMENT")


def add_entry(
    db: Session,
    *,
    campaign_id: int,
    type: str,
    amount: Decimal | float,
    currency: str = "NGN",
    reference: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    description: str | None = None,
) -> LedgerEntry:
    """Append a ledger entry (flushed, not committed — caller commits)."""
    entry = LedgerEntry(
        campaign_id=campaign_id,
        type=type,
        amount=Decimal(str(amount)),
        currency=currency,
        reference=reference,
        source_type=source_type,
        source_id=source_id,
        description=description,
    )
    db.add(entry)
    db.flush()
    return entry


def balance(db: Session, campaign_id: int) -> Decimal:
    """Net campaign balance = what is available to spend right now.

    Includes pending payout holds, so a payout in flight can never be
    double-spent (Rule 5).
    """
    total = (
        db.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .filter(LedgerEntry.campaign_id == campaign_id)
        .scalar()
    )
    return Decimal(str(total))


def sync_campaign_totals(db: Session, campaign: Campaign) -> None:
    """Recalculate the campaign's projected totals from the ledger.

    ``raised_amount``  = sum of CONTRIBUTION/REFUND/ADJUSTMENT entries
    ``disbursed_amount`` = sum of SUCCESS payouts
    """
    raised = (
        db.query(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .filter(
            LedgerEntry.campaign_id == campaign.id,
            LedgerEntry.type.in_(RAISED_TYPES),
        )
        .scalar()
    )
    disbursed = (
        db.query(func.coalesce(func.sum(Payout.amount), 0))
        .filter(Payout.campaign_id == campaign.id, Payout.status == "SUCCESS")
        .scalar()
    )
    campaign.raised_amount = Decimal(str(raised))
    campaign.disbursed_amount = Decimal(str(disbursed))
    db.flush()
