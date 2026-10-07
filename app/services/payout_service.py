"""Payout rule evaluation + payout state machine (PRODUCT.md §12, §26-27).

State machine:
    DRAFT (awaiting approval) -> READY -> PROCESSING -> SUCCESS | FAILED
    DRAFT/READY -> LOCKED (insufficient attributable funds) -> READY
    PROCESSING -> UNKNOWN (5xx/timeout) -> verify -> SUCCESS | FAILED

Kora rule (§26): never treat a 5xx/timeout as a failure — verify instead.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.integrations.kora import KoraAPIError, get_kora_client
from app.integrations.kora.webhooks import build_transfer_event, deliver_mock_webhook
from app.models.campaign import Campaign
from app.models.milestone import Milestone
from app.models.payout import Payout, PayoutRule
from app.models.user import User
from app.services.activity import log_audit, notify
from app.services.ledger_service import add_entry, balance, sync_campaign_totals


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _rule_amount_and_met(db: Session, campaign: Campaign, rule: PayoutRule) -> tuple[bool, Decimal]:
    """Evaluate a non-milestone payout trigger (§12.1–12.6)."""
    raised = Decimal(str(campaign.raised_amount or 0))
    target = Decimal(str(campaign.target_amount or 0))
    pct = float(raised / target * 100) if target > 0 else 0.0
    fixed = Decimal(str(rule.amount)) if rule.amount is not None else Decimal("0")

    if rule.trigger_type == "THRESHOLD":
        if rule.threshold_amount is None:
            return False, fixed
        return raised >= Decimal(str(rule.threshold_amount)), fixed

    if rule.trigger_type == "PERCENTAGE":
        # "When 50% of target is reached, release 20% of collected funds"
        if rule.threshold_percentage is None or pct < float(rule.threshold_percentage):
            return False, fixed
        release = float(rule.percentage or 0) / 100
        return True, Decimal(str(round(float(raised) * release, 2)))

    if rule.trigger_type == "DATE":
        due = _aware(rule.due_date)
        if due is None:
            return False, fixed
        return datetime.now(timezone.utc) >= due, fixed

    if rule.trigger_type == "HYBRID":
        if rule.threshold_amount is None or raised < Decimal(str(rule.threshold_amount)):
            return False, fixed
        return True, fixed

    # MANUAL + MILESTONE are driven explicitly (trigger endpoint / approvals).
    return False, fixed


def evaluate_rules(db: Session, campaign: Campaign) -> list[Payout]:
    """Trigger rules whose conditions just became true; promote LOCKED payouts."""
    created: list[Payout] = []
    rules = (
        db.query(PayoutRule)
        .filter(PayoutRule.campaign_id == campaign.id, PayoutRule.status == "LOCKED")
        .all()
    )
    for rule in rules:
        if rule.trigger_type in ("MILESTONE", "MANUAL"):
            continue  # owned by the milestone engine / explicit trigger endpoint
        already = db.query(Payout).filter(Payout.rule_id == rule.id).first()
        if already:
            rule.status = "TRIGGERED"
            continue
        met, amount = _rule_amount_and_met(db, campaign, rule)
        if not met or amount <= 0:
            continue
        payout = _create_payout_for_rule(db, campaign, rule, amount)
        if payout:
            created.append(payout)

    # Promote LOCKED payouts once funds are attributable (§26 LOCKED -> READY).
    for payout in (
        db.query(Payout).filter(
            Payout.campaign_id == campaign.id, Payout.status == "LOCKED"
        ).all()
    ):
        if balance(db, campaign.id) >= Decimal(str(payout.amount)):
            payout.status = "READY"
            notify(
                db,
                user_id=campaign.owner_id,
                campaign_id=campaign.id,
                title="Payout ready",
                body=f"₦{payout.amount:,.2f} payout is unlocked and ready to execute",
            )
    db.commit()
    return created


def _create_payout_for_rule(
    db: Session, campaign: Campaign, rule: PayoutRule, amount: Decimal
) -> Payout | None:
    if rule.beneficiary_id is None:
        return None
    rule.status = "TRIGGERED"
    available = balance(db, campaign.id)
    payout = Payout(
        campaign_id=campaign.id,
        beneficiary_id=rule.beneficiary_id,
        rule_id=rule.id,
        milestone_id=rule.milestone_id,
        amount=amount,
        currency=campaign.currency,
        status="DRAFT" if rule.requires_approval else (
            "READY" if available >= amount else "LOCKED"
        ),
    )
    db.add(payout)
    db.flush()
    log_audit(
        db,
        campaign_id=campaign.id,
        action="payout.triggered",
        description=(
            f"Payout rule “{rule.label or rule.trigger_type}” triggered — "
            f"₦{amount:,.2f}"
        ),
        reference=rule.label,
    )
    return payout


def trigger_rule(
    db: Session, *, rule: PayoutRule, campaign: Campaign, actor: User
) -> Payout:
    """Explicit trigger for MANUAL rules (§12.5)."""
    if rule.status != "LOCKED":
        raise HTTPException(status_code=409, detail="Rule has already been triggered")
    if rule.beneficiary_id is None:
        raise HTTPException(status_code=400, detail="Rule needs a beneficiary")
    amount = Decimal(str(rule.amount or 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Rule amount must be greater than zero")
    payout = _create_payout_for_rule(db, campaign, rule, amount)
    if payout is None:  # defensive
        raise HTTPException(status_code=400, detail="Could not create payout")
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Payout triggered",
        body=f"₦{amount:,.2f} payout created by {actor.name}",
    )
    db.commit()
    db.refresh(payout)
    return payout


def approve_payout(db: Session, *, payout: Payout, campaign: Campaign, actor: User) -> Payout:
    """Admin approval moves DRAFT -> READY/LOCKED (§12.5 / §27)."""
    if payout.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Payout is not awaiting approval")
    payout.status = "READY" if balance(db, campaign.id) >= Decimal(str(payout.amount)) else "LOCKED"
    payout.approved_by = actor.id
    payout.approved_at = datetime.now(timezone.utc)
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=actor.id,
        action="payout.approved",
        description=f"Payout of ₦{payout.amount:,.2f} approved",
        reference=payout.kora_reference,
    )
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Payout approved",
        body=f"₦{payout.amount:,.2f} approved — ready to execute",
    )
    db.commit()
    db.refresh(payout)
    return payout


# ------------------------------------------------------------ execution
def execute_payout(
    db: Session,
    *,
    payout: Payout,
    campaign: Campaign,
    actor: User,
    background_tasks=None,
) -> Payout:
    """Execute an approved payout through Kora (PRODUCT.md §27).

    Enforces: Rule 4 (timeout ≠ failure → verify), Rule 5 (sufficient
    attributable balance), Rule 8 (verified beneficiary only).
    """
    if payout.status == "LOCKED":
        raise HTTPException(status_code=409, detail="Payout is locked — insufficient funds")
    if payout.status == "DRAFT":
        raise HTTPException(status_code=409, detail="Payout requires approval first")
    if payout.status != "READY":
        raise HTTPException(status_code=409, detail=f"Payout is {payout.status}")

    beneficiary = payout.beneficiary
    if beneficiary.verification_status != "VERIFIED":
        raise HTTPException(status_code=400, detail="Beneficiary is not verified (Rule 8)")

    available = balance(db, campaign.id)
    if available < Decimal(str(payout.amount)):  # Rule 5
        payout.status = "LOCKED"
        db.commit()
        raise HTTPException(status_code=409, detail="Insufficient campaign balance")

    # Hold the funds (immutable ledger entry) BEFORE calling Kora.
    add_entry(
        db,
        campaign_id=campaign.id,
        type="PAYOUT_PENDING",
        amount=-Decimal(str(payout.amount)),
        currency=payout.currency,
        source_type="payout",
        source_id=str(payout.id),
        description=f"Payout initiated to {beneficiary.name}",
    )
    payout.status = "PROCESSING"
    payout.kora_reference = payout.kora_reference or f"KPY-D-{secrets.token_hex(5).upper()}"
    sync_campaign_totals(db, campaign)
    db.commit()

    kora = get_kora_client()
    try:
        kora.create_payout(
            reference=payout.kora_reference,
            amount=float(payout.amount),
            currency=payout.currency,
            bank_code=beneficiary.bank_code,
            account_number=beneficiary.account_number,
            narration=f"FundFlow payout {payout.kora_reference}",
            customer_name=beneficiary.name,
        )
    except KoraAPIError as exc:
        if exc.retryable:
            # Rule 4: outcome UNKNOWN — verify instead of failing (§26).
            payout.status = "UNKNOWN"
            db.commit()
            log_audit(
                db,
                campaign_id=campaign.id,
                action="payout.unknown",
                description=(
                    f"Kora returned {exc.message} for {payout.kora_reference} — "
                    "verifying before concluding"
                ),
                reference=payout.kora_reference,
                commit=True,
            )
            return verify_and_settle(db, payout=payout, campaign=campaign)
        settle_failed(db, payout=payout, campaign=campaign, reason=exc.message)
        return payout

    db.commit()
    if kora.is_mock:
        outcome = kora._mock.payout_outcome(payout.kora_reference)
        event = "transfer.success" if outcome == "success" else "transfer.failed"
        # Process synchronously AND schedule the background replay (deduped).
        # Same reasoning as contributions: settled response on every runner.
        import json as _json

        from app.integrations.kora.webhooks import compute_signature
        from app.services.webhook_service import handle_kora_webhook

        transfer = build_transfer_event(
            event,
            reference=payout.kora_reference,
            amount=float(payout.amount),
            currency=payout.currency,
        )
        raw = _json.dumps(transfer, separators=(",", ":"), ensure_ascii=False).encode()
        try:
            handle_kora_webhook(raw, compute_signature(transfer["data"]))
        finally:
            deliver_mock_webhook(background_tasks, transfer)
    return payout


def verify_and_settle(db: Session, *, payout: Payout, campaign: Campaign) -> Payout:
    """Resolve an UNKNOWN payout by querying Kora (Rule 4 / §26)."""
    kora = get_kora_client()
    try:
        verified = kora.verify_payout(payout.kora_reference)
    except KoraAPIError as exc:
        if exc.retryable:
            return payout  # still unknown — leave for reconciliation
        settle_failed(
            db, payout=payout, campaign=campaign,
            reason=f"Verification failed: {exc.message}",
        )
        return payout

    status_value = str(verified.get("status", "")).lower()
    if status_value in ("success", "successful"):
        settle_success(db, payout=payout, campaign=campaign)
    else:
        settle_failed(
            db, payout=payout, campaign=campaign,
            reason=verified.get("message") or "Payout failed at Kora",
        )
    return payout


# ------------------------------------------------------------ webhooks
def process_transfer_event(db: Session, data: dict, event: str) -> dict:
    """Handle transfer.success / transfer.failed after verification (§27)."""
    reference = data.get("reference")
    payout = None
    if reference:
        payout = db.query(Payout).filter(Payout.kora_reference == reference).first()
    if payout is None:
        return {"status": "unmatched", "reference": reference}
    if payout.status in ("SUCCESS", "FAILED"):
        # Domain-level idempotency for duplicate payout webhooks (§71).
        return {"status": "duplicate", "reference": reference}

    campaign = db.get(Campaign, payout.campaign_id)

    # Rule 2: verify the webhook against Kora before giving value (§27).
    kora = get_kora_client()
    try:
        verified = kora.verify_payout(payout.kora_reference)
    except KoraAPIError as exc:
        if exc.retryable:
            return {"status": "verification_retry", "reference": reference}
        # Provider rejects the reference — trust nothing; keep for review.
        log_audit(
            db,
            campaign_id=campaign.id,
            action="payout.verification_failed",
            description=f"Could not verify {payout.kora_reference}: {exc.message}",
            reference=payout.kora_reference,
            commit=True,
        )
        return {"status": "verification_failed", "reference": reference}

    provider_status = str(verified.get("status", "")).lower()
    if provider_status in ("success", "successful"):
        settle_success(db, payout=payout, campaign=campaign)
        return {"status": "processed", "reference": reference}

    settle_failed(
        db, payout=payout, campaign=campaign,
        reason=verified.get("message") or data.get("message") or "Transfer failed",
    )
    return {"status": "processed_failed", "reference": reference}


# ------------------------------------------------------------ settlement
def settle_success(db: Session, *, payout: Payout, campaign: Campaign) -> None:
    """Money left the merchant balance: settle the hold, close the loop."""
    if payout.status == "SUCCESS":
        return
    payout.status = "SUCCESS"
    payout.completed_at = datetime.now(timezone.utc)
    # Marker entry (amount 0) — the hold from PAYOUT_PENDING stands as the
    # actual outflow, keeping the immutable ledger balanced.
    add_entry(
        db,
        campaign_id=campaign.id,
        type="PAYOUT_SUCCESS",
        amount=Decimal("0"),
        currency=payout.currency,
        reference=payout.kora_reference,
        source_type="payout",
        source_id=str(payout.id),
        description=f"Payout {payout.kora_reference} completed",
    )
    if payout.rule_id:
        rule = db.get(PayoutRule, payout.rule_id)
        if rule:
            rule.status = "EXECUTED"
    if payout.milestone_id:
        milestone = db.get(Milestone, payout.milestone_id)
        if milestone:
            milestone.status = "PAID"
            milestone.paid_at = datetime.now(timezone.utc)
    sync_campaign_totals(db, campaign)
    beneficiary_name = payout.beneficiary.name if payout.beneficiary else "beneficiary"
    log_audit(
        db,
        campaign_id=campaign.id,
        action="payout.success",
        description=(
            f"₦{payout.amount:,.2f} released to {beneficiary_name}"
        ),
        reference=payout.kora_reference,
    )
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Payout successful",
        body=f"₦{payout.amount:,.2f} released to {beneficiary_name}",
    )
    db.commit()


def settle_failed(db: Session, *, payout: Payout, campaign: Campaign, reason: str) -> None:
    """Release the hold and record the failure (funds return to the campaign)."""
    if payout.status == "SUCCESS":
        return  # never regress a settled payout
    from app.models.ledger import LedgerEntry

    payout.status = "FAILED"
    payout.failure_reason = reason
    # Only release if the hold was actually placed (pre-acceptance failures).
    hold = (
        db.query(LedgerEntry)
        .filter_by(campaign_id=campaign.id, type="PAYOUT_PENDING", source_id=str(payout.id))
        .first()
    )
    release_recorded = (
        db.query(LedgerEntry)
        .filter_by(campaign_id=campaign.id, type="PAYOUT_FAILED", source_id=str(payout.id))
        .first()
    )
    if hold is not None and release_recorded is None:
        add_entry(
            db,
            campaign_id=campaign.id,
            type="PAYOUT_FAILED",
            amount=Decimal(str(payout.amount)),
            currency=payout.currency,
            reference=payout.kora_reference,
            source_type="payout",
            source_id=str(payout.id),
            description=f"Payout failed — hold released ({reason})",
        )
    sync_campaign_totals(db, campaign)

    log_audit(
        db,
        campaign_id=campaign.id,
        action="payout.failed",
        description=f"Payout of ₦{payout.amount:,.2f} failed: {reason}",
        reference=payout.kora_reference,
    )
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Payout failed",
        body=f"₦{payout.amount:,.2f} — {reason}",
    )
    db.commit()



