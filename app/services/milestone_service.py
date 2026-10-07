"""Milestone engine (PRODUCT.md §25).

Evaluates whether a milestone's conditions are satisfied, and — on approval —
creates the milestone's READY payout to the verified beneficiary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.beneficiary import Beneficiary
from app.models.campaign import Campaign
from app.models.milestone import Milestone
from app.models.payout import Payout, PayoutRule
from app.models.user import User
from app.services.activity import log_audit, notify
from app.services.ledger_service import balance


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def conditions_met(db: Session, campaign: Campaign, milestone: Milestone) -> bool:
    """True when threshold/date conditions for this milestone are satisfied."""
    raised = Decimal(str(campaign.raised_amount or 0))
    if milestone.threshold_amount is not None and raised < Decimal(str(milestone.threshold_amount)):
        return False
    if milestone.threshold_percentage is not None:
        target = Decimal(str(campaign.target_amount or 0))
        if target <= 0:
            return False
        pct = float(raised / target * 100)
        if pct < float(milestone.threshold_percentage):
            return False
    due = _aware(milestone.due_date)
    if due is not None and datetime.now(timezone.utc) < due:
        return False
    return True


def progress_state(db: Session, campaign: Campaign, milestone: Milestone) -> str:
    """UI-facing state: NOT_STARTED | IN_PROGRESS | READY | PAID."""
    if milestone.status == "PAID":
        return "PAID"
    if milestone.status == "APPROVED":
        return "APPROVED"
    return "READY" if conditions_met(db, campaign, milestone) else "PENDING"


def evaluate_milestones(db: Session, campaign: Campaign) -> list[Milestone]:
    """Auto-approve conditions-only milestones (requires_approval=False).

    Approval-gated milestones stay PENDING until an admin approves them (§25).
    """
    milestones = (
        db.query(Milestone)
        .filter(Milestone.campaign_id == campaign.id, Milestone.status == "PENDING")
        .order_by(Milestone.order_index.asc())
        .all()
    )
    for milestone in milestones:
        if milestone.requires_approval or milestone.beneficiary_id is None:
            continue
        if not conditions_met(db, campaign, milestone):
            continue
        try:
            approve_milestone(db, milestone=milestone, campaign=campaign, actor=None)
        except HTTPException:
            continue
    return milestones


def approve_milestone(
    db: Session, *, milestone: Milestone, campaign: Campaign, actor: User | None
) -> Payout:
    """Approve a milestone → create its READY payout (§26 payout flow).

    Guard rails:
    * conditions must be met (threshold/date)
    * beneficiary must exist and be VERIFIED (Rule 8)
    """
    if milestone.status != "PENDING":
        raise HTTPException(status_code=409, detail="Milestone has already been approved")
    if not conditions_met(db, campaign, milestone):
        raise HTTPException(status_code=409, detail="Milestone conditions not met yet")

    beneficiary = (
        db.get(Beneficiary, milestone.beneficiary_id) if milestone.beneficiary_id else None
    )
    if beneficiary is None:
        raise HTTPException(status_code=400, detail="Milestone needs a beneficiary")
    if beneficiary.verification_status != "VERIFIED":
        raise HTTPException(
            status_code=400, detail="Beneficiary must be verified before payout (Rule 8)"
        )

    milestone.status = "APPROVED"
    milestone.approved_by = actor.id if actor else None
    milestone.approved_at = datetime.now(timezone.utc)

    rule = PayoutRule(
        campaign_id=campaign.id,
        milestone_id=milestone.id,
        beneficiary_id=beneficiary.id,
        label=milestone.name,
        amount=Decimal(str(milestone.amount)),
        trigger_type="MILESTONE",
        requires_approval=False,
        status="TRIGGERED",
    )
    db.add(rule)
    db.flush()

    available = balance(db, campaign.id)
    payout = Payout(
        campaign_id=campaign.id,
        beneficiary_id=beneficiary.id,
        rule_id=rule.id,
        milestone_id=milestone.id,
        amount=Decimal(str(milestone.amount)),
        currency=campaign.currency,
        status="READY" if available >= Decimal(str(milestone.amount)) else "LOCKED",
        approved_by=actor.id if actor else None,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(payout)
    db.flush()

    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=actor.id if actor else None,
        action="milestone.approved",
        description=(
            f"Milestone “{milestone.name}” approved — ₦{milestone.amount:,.2f} "
            f"payout ready for {beneficiary.name}"
        ),
        reference=f"MILESTONE-{milestone.id}",
    )
    notify(
        db,
        user_id=campaign.owner_id,
        campaign_id=campaign.id,
        title="Milestone approved",
        body=f"“{milestone.name}” approved — payout is ready to execute",
    )
    db.commit()
    db.refresh(payout)
    return payout
