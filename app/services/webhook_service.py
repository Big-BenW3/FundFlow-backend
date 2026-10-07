"""Kora webhook pipeline (PRODUCT.md §21-22).

    raw body → verify HMAC signature → parse → dedupe (unique constraint)
    → dispatch (charge/transfer/refund) → verify against Kora → ledger
    → evaluate milestones + payout rules → mark processed → 200

Signature failures are rejected with 401 (Rule 2). Duplicates get 200 with
``{"status": "duplicate"}`` so Kora stops retrying them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.integrations.kora.webhooks import verify_signature
from app.models.webhook import WebhookEvent
from app.services import contribution_service, milestone_service, payout_service


class WebhookSignatureError(Exception):
    """Raised when the HMAC signature is missing or invalid (Rule 2)."""


def handle_kora_webhook(
    raw_body: bytes, signature: str | None, db: Session | None = None
) -> dict:
    """Full pipeline. Pass ``db`` inside a request; omit for background tasks."""
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise WebhookSignatureError("Webhook body is not valid JSON")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise WebhookSignatureError("Webhook payload has no data object")
    if not verify_signature(data, signature):  # HMAC SHA-256 over data (§21)
        raise WebhookSignatureError("Invalid webhook signature")

    if db is not None:
        return _process(db, payload)
    session = get_session_factory()()
    try:
        return _process(session, payload)
    finally:
        session.close()


def _process(db: Session, payload: dict) -> dict:
    event = str(payload.get("event") or "")
    data = payload.get("data") or {}
    reference = str(data.get("reference") or "unknown")

    # ---- Dedupe (§22): provider + reference + event is unique ----------
    existing = (
        db.query(WebhookEvent)
        .filter(
            WebhookEvent.provider == "kora",
            WebhookEvent.provider_reference == reference,
            WebhookEvent.event_type == event,
        )
        .first()
    )
    if existing is not None:
        return {"status": "duplicate", "event": event, "reference": reference}

    record = WebhookEvent(
        provider="kora",
        event_type=event,
        provider_reference=reference,
        payload=json.dumps(payload),
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # concurrent duplicate won the race
        return {"status": "duplicate", "event": event, "reference": reference}

    # ---- Dispatch ------------------------------------------------------
    if event.startswith("charge."):
        result = contribution_service.process_charge_event(db, data, event)
        if result.get("status") == "processed":
            campaign_id = _campaign_for_contribution(db, reference)
            if campaign_id is not None:
                _reevaluate(db, campaign_id)  # triggers §25 / §12
    elif event.startswith("transfer."):
        result = payout_service.process_transfer_event(db, data, event)
        if result.get("status") in ("processed_failed", "verification_failed"):
            campaign_id = _campaign_for_payout(db, reference)
            if campaign_id is not None:
                _reevaluate(db, campaign_id)  # failed payouts free funds
    elif event.startswith("refund."):
        result = _process_refund(db, data, event)
    else:
        result = {"status": "ignored", "event": event}

    record.processed = True
    record.processed_at = datetime.now(timezone.utc)
    db.commit()
    result["event"] = event
    return result


def _reevaluate(db: Session, campaign_id: int) -> None:
    from app.models.campaign import Campaign

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return
    payout_service.evaluate_rules(db, campaign)
    milestone_service.evaluate_milestones(db, campaign)
    db.commit()


def _campaign_for_contribution(db: Session, reference: str) -> int | None:
    from app.models.contribution import Contribution

    row = (
        db.query(Contribution).filter(Contribution.kora_reference == reference).first()
    )
    return row.campaign_id if row else None


def _campaign_for_payout(db: Session, reference: str) -> int | None:
    from app.models.payout import Payout

    row = db.query(Payout).filter(Payout.kora_reference == reference).first()
    return row.campaign_id if row else None


def _process_refund(db: Session, data: dict, event: str) -> dict:
    """Refund webhooks reverse a contribution in the ledger (§53)."""
    from decimal import Decimal

    from app.models.campaign import Campaign
    from app.models.contribution import Contribution
    from app.services.activity import log_audit
    from app.services.ledger_service import add_entry, sync_campaign_totals

    reference = data.get("reference")
    contribution = (
        db.query(Contribution).filter(Contribution.kora_reference == reference).first()
    )
    if contribution is None:
        return {"status": "unmatched"}
    if contribution.status == "REFUNDED":
        return {"status": "duplicate"}
    if event == "refund.failed":
        return {"status": "refund_failed"}

    campaign = db.get(Campaign, contribution.campaign_id)
    contribution.status = "REFUNDED"
    add_entry(
        db,
        campaign_id=contribution.campaign_id,
        type="REFUND",
        amount=-Decimal(str(contribution.amount)),
        currency=contribution.currency,
        reference=str(reference),
        source_type="contribution",
        source_id=str(contribution.id),
        description="Contribution refunded",
    )
    sync_campaign_totals(db, campaign)
    log_audit(
        db,
        campaign_id=contribution.campaign_id,
        action="contribution.refunded",
        description=f"₦{contribution.amount:,.2f} refunded ({reference})",
        reference=str(reference),
    )
    db.commit()
    return {"status": "processed"}

