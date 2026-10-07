"""Periodic reconciliation of internal records against Kora (PRODUCT.md §24).

    Our database → Kora transaction query → compare → MATCH / MISMATCH
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.integrations.kora import KoraAPIError, get_kora_client
from app.models.campaign import Campaign
from app.models.contribution import Contribution
from app.models.payout import Payout
from app.services.activity import log_audit


def reconcile_campaign(db: Session, campaign: Campaign, actor_id: int | None = None) -> dict:
    kora = get_kora_client()
    checked = 0
    matched = 0
    mismatches: list[dict] = []

    contributions = (
        db.query(Contribution)
        .filter(
            Contribution.campaign_id == campaign.id,
            Contribution.status.in_(("SUCCESS", "FAILED")),
        )
        .all()
    )
    for contribution in contributions:
        checked += 1
        try:
            verified = kora.verify_transaction(contribution.kora_reference)
        except KoraAPIError as exc:
            mismatches.append(
                {
                    "type": "contribution",
                    "reference": contribution.kora_reference,
                    "reason": exc.message,
                }
            )
            continue
        provider_amount = _to_decimal(verified.get("amount"))
        provider_status = str(verified.get("status", "")).lower()
        our_expected = contribution.status == "SUCCESS"
        provider_success = provider_status in ("success", "successful")
        if provider_amount == contribution.amount and our_expected == provider_success:
            matched += 1
        else:
            mismatches.append(
                {
                    "type": "contribution",
                    "reference": contribution.kora_reference,
                    "reason": (
                        f"amount {provider_amount} vs {contribution.amount}, "
                        f"status {provider_status} vs {contribution.status}"
                    ),
                }
            )

    payouts = (
        db.query(Payout)
        .filter(
            Payout.campaign_id == campaign.id,
            Payout.status.in_(("SUCCESS", "FAILED", "PROCESSING", "UNKNOWN")),
            Payout.kora_reference.isnot(None),
        )
        .all()
    )
    for payout in payouts:
        checked += 1
        try:
            verified = kora.verify_payout(payout.kora_reference)
        except KoraAPIError as exc:
            mismatches.append(
                {
                    "type": "payout",
                    "reference": payout.kora_reference,
                    "reason": exc.message,
                }
            )
            continue
        provider_status = str(verified.get("status", "")).lower()
        provider_success = provider_status in ("success", "successful")
        our_success = payout.status == "SUCCESS"
        if provider_success == our_success:
            matched += 1
        else:
            mismatches.append(
                {
                    "type": "payout",
                    "reference": payout.kora_reference,
                    "reason": f"status {provider_status} vs {payout.status}",
                }
            )

    report = {
        "checked": checked,
        "matched": matched,
        "mismatches": mismatches,
        "clean": len(mismatches) == 0,
    }
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=actor_id,
        action="reconciliation.ran",
        description=(
            f"Reconciled {checked} transactions — {matched} matched, "
            f"{len(mismatches)} need review"
        ),
        commit=True,
    )
    return report


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
