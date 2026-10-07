"""Contribution processing (PRODUCT.md §18, §20, §22).

Flow: initiate → (mock or real) Kora payment → charge webhook →
signature verified by the webhook layer → transaction verified against
Kora → idempotency checks → ledger → progress → payout rules evaluated.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.integrations.kora import KoraAPIError, get_kora_client
from app.integrations.kora.webhooks import build_charge_event, deliver_mock_webhook
from app.models.campaign import Campaign
from app.models.contribution import Contribution
from app.models.user import User
from app.services.activity import log_audit, notify
from app.services.ledger_service import add_entry, sync_campaign_totals

MIN_AMOUNT = Decimal("100")  # Kora sandbox minimum credit is NGN 100


def _payload_bytes(payload: dict) -> bytes:
    import json

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def initiate_contribution(
    db: Session,
    *,
    campaign: Campaign,
    user: User,
    amount: Decimal | float,
    anonymous: bool = False,
    payment_method: str = "bank_transfer",
    background_tasks=None,
) -> Contribution:
    """Record a PENDING contribution and simulate/receive its Kora payment."""
    amount = Decimal(str(amount))
    if amount < MIN_AMOUNT:
        raise HTTPException(status_code=400, detail="Minimum contribution is ₦100")
    if campaign.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Campaign is not accepting contributions")
    if campaign.deadline:
        deadline = campaign.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline < datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Campaign deadline has passed")
    if campaign.account is None:
        raise HTTPException(
            status_code=409, detail="Campaign collection account is not ready yet"
        )

    reference = f"KPY-PAY-{secrets.token_hex(5).upper()}"
    contribution = Contribution(
        campaign_id=campaign.id,
        kora_reference=reference,
        amount=amount,
        currency=campaign.currency,
        contributor_id=user.id,
        contributor_name=None if anonymous else user.name,
        anonymous=anonymous,
        status="PENDING",
        payment_method=payment_method,
    )
    db.add(contribution)
    db.commit()
    db.refresh(contribution)

    if not get_kora_client().is_mock:
        # Live mode: contributor transfers to the campaign's Kora virtual
        # account; Kora's charge webhook confirms the payment (§18).
        return contribution

    # Mock mode: simulate the payment landing at Kora, then process the
    # charge.success event through the full verified pipeline.
    #
    # NOTE: we process synchronously AND still schedule a background replay.
    # Kora retries webhooks on outages (§21), so the pipeline is built to
    # dedupe replays (webhook unique constraint + contribution status
    # checks). Processing first guarantees a settled response on every HTTP
    # runner, including ones that don't await background tasks.
    kora = get_kora_client()
    kora.register_charge(reference, float(amount), campaign.currency)
    account = campaign.account
    payload = build_charge_event(
        "charge.success",
        reference=reference,
        amount=float(amount),
        currency=campaign.currency,
        vba={
            "account_name": campaign.title[:64],
            "account_number": account.kora_account_number,
            "account_reference": account.kora_account_reference,
            "bank_name": account.bank_name,
        },
        payer={
            "account_name": user.name[:64],
            "account_number": "90" + secrets.token_hex(4),
            "bank_name": "Donor Bank",
        },
    )
    from app.integrations.kora.webhooks import compute_signature
    from app.services.webhook_service import handle_kora_webhook

    raw = _payload_bytes(payload)
    try:
        handle_kora_webhook(raw, compute_signature(payload["data"]))
    finally:
        deliver_mock_webhook(background_tasks, payload)
    db.refresh(contribution)
    return contribution


def process_charge_event(db: Session, data: dict, event: str) -> dict:
    """Handle a verified charge.success / charge.failed webhook (§20-22)."""
    kora = get_kora_client()
    reference = data.get("reference") or data.get("payment_reference")
    contribution = None
    if reference:
        contribution = (
            db.query(Contribution).filter(Contribution.kora_reference == reference).first()
        )
    if contribution is None:
        contribution = _match_by_account_reference(db, data)

    if contribution is None:
        return {"status": "unmatched", "reference": reference}
    if contribution.status in ("SUCCESS", "REFUNDED"):
        # Domain-level idempotency on top of webhook dedup (§22).
        return {"status": "duplicate", "reference": contribution.kora_reference}

    campaign = db.get(Campaign, contribution.campaign_id)

    if event == "charge.failed":
        contribution.status = "FAILED"
        log_audit(
            db,
            campaign_id=campaign.id,
            action="contribution.failed",
            description=f"Contribution {contribution.kora_reference} failed at Kora",
            reference=contribution.kora_reference,
            commit=True,
        )
        return {"status": "failed", "reference": contribution.kora_reference}

    # Rule 2/§20: never trust the webhook alone — verify against Kora.
    try:
        verified = kora.verify_transaction(contribution.kora_reference)
    except KoraAPIError as exc:
        if exc.retryable:
            # Transient verification failure — keep PENDING for a later retry.
            return {"status": "verification_retry", "reference": contribution.kora_reference}
        contribution.status = "FAILED"
        log_audit(
            db,
            campaign_id=campaign.id,
            action="contribution.verification_failed",
            description=(
                f"Kora could not verify {contribution.kora_reference}: {exc.message}"
            ),
            reference=contribution.kora_reference,
            commit=True,
        )
        return {"status": "verification_failed", "reference": contribution.kora_reference}

    verified_status = str(verified.get("status", "")).lower()
    try:
        provider_amount = Decimal(str(verified.get("amount")))
    except (InvalidOperation, TypeError):
        provider_amount = Decimal("-1")
    try:
        webhook_amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError):
        webhook_amount = provider_amount

    amounts_match = (
        provider_amount == contribution.amount and webhook_amount == provider_amount
    )
    currency_ok = (
        str(verified.get("currency", contribution.currency)) == contribution.currency
    )

    if verified_status not in ("success", "successful") or not amounts_match or not currency_ok:
        contribution.status = "FAILED"
        log_audit(
            db,
            campaign_id=campaign.id,
            action="contribution.verification_mismatch",
            description=(
                f"Verification mismatch for {contribution.kora_reference} "
                f"(provider={verified_status} {provider_amount}, "
                f"webhook={webhook_amount}, expected={contribution.amount})"
            ),
            reference=contribution.kora_reference,
            commit=True,
        )
        return {"status": "verification_failed", "reference": contribution.kora_reference}

    # --- Verified: credit the ledger ------------------------------------
    contribution.status = "SUCCESS"
    contribution.transaction_date = datetime.now(timezone.utc)
    details = data.get("virtual_bank_account_details") or {}
    payer = details.get("payer_bank_account") or {}
    contribution.payer_account_number = payer.get("account_number")
    contribution.payer_bank_name = payer.get("bank_name")
    fee = verified.get("fee") or data.get("fee") or 0
    contribution.fee = Decimal(str(fee))
    add_entry(
        db,
        campaign_id=campaign.id,
        type="CONTRIBUTION",
        amount=contribution.amount,
        currency=contribution.currency,
        reference=contribution.kora_reference,
        source_type="contribution",
        source_id=str(contribution.id),
        description=f"Contribution from {contribution.contributor_name or 'Anonymous'}",
    )
    sync_campaign_totals(db, campaign)
    actor = (
        f"{contribution.contributor_name} (Anonymous)"
        if contribution.anonymous
        else (contribution.contributor_name or "Someone")
    )
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=contribution.contributor_id,
        action="contribution.received",
        description=f"₦{contribution.amount:,.2f} contribution received from {actor}",
        reference=contribution.kora_reference,
    )
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Contribution received",
        body=f"₦{contribution.amount:,.2f} from {actor} — {campaign.title}",
    )
    db.commit()
    return {"status": "processed", "reference": contribution.kora_reference}



def _match_by_account_reference(db: Session, data: dict) -> Contribution | None:
    """Live-mode fallback: Kora assigns its own charge reference, so match the
    payment to a PENDING contribution via the campaign VBA + amount (§20)."""
    from app.models.campaign import CampaignAccount

    details = data.get("virtual_bank_account_details") or {}
    vba = details.get("virtual_bank_account") or {}
    account_reference = vba.get("account_reference")
    if not account_reference or "amount" not in data:
        return None
    account = (
        db.query(CampaignAccount)
        .filter(CampaignAccount.kora_account_reference == account_reference)
        .first()
    )
    if account is None:
        return None
    try:
        amount = Decimal(str(data["amount"]))
    except (InvalidOperation, TypeError):
        return None
    return (
        db.query(Contribution)
        .filter(
            Contribution.campaign_id == account.campaign_id,
            Contribution.status == "PENDING",
            Contribution.amount == amount,
        )
        .order_by(Contribution.created_at.asc())
        .first()
    )
