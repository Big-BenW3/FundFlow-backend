"""Milestone endpoints (PRODUCT.md §25, §47).

POST/GET /campaigns/{id}/milestones · PATCH /milestones/{id}
POST /milestones/{id}/approve
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app.core.security import CurrentUser, DBSession, OptionalUser
from app.models.beneficiary import Beneficiary
from app.models.milestone import Milestone
from app.schemas.inputs import MilestoneCreateIn, MilestoneUpdateIn
from app.schemas.outputs import milestone_out
from app.services import campaign_service, milestone_service
from app.services.activity import log_audit

router = APIRouter(tags=["milestones"])


@router.get("/campaigns/{campaign_id}/milestones")
def list_milestones(campaign_id: int, db: DBSession, user: OptionalUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    rows = (
        db.query(Milestone)
        .filter(Milestone.campaign_id == campaign_id)
        .order_by(Milestone.order_index.asc(), Milestone.created_at.asc())
        .all()
    )
    return [
        milestone_out(m, state=milestone_service.progress_state(db, campaign, m))
        for m in rows
    ]


@router.post("/campaigns/{campaign_id}/milestones", status_code=201)
def create_milestone(
    campaign_id: int, body: MilestoneCreateIn, db: DBSession, user: CurrentUser
):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)

    if body.beneficiary_id is not None:
        beneficiary = db.get(Beneficiary, body.beneficiary_id)
        if beneficiary is None or beneficiary.campaign_id != campaign_id:
            raise HTTPException(status_code=404, detail="Beneficiary not found")

    if body.order_index is None:
        count = (
            db.query(Milestone).filter(Milestone.campaign_id == campaign_id).count()
        )
        order_index = count
    else:
        order_index = body.order_index

    milestone = Milestone(
        campaign_id=campaign_id,
        order_index=order_index,
        name=body.name,
        description=body.description,
        amount=Decimal(str(body.amount)),
        beneficiary_id=body.beneficiary_id,
        trigger_type=body.trigger_type,
        threshold_amount=Decimal(str(body.threshold_amount))
        if body.threshold_amount
        else None,
        threshold_percentage=body.threshold_percentage,
        due_date=body.due_date,
        requires_approval=body.requires_approval,
    )
    db.add(milestone)
    log_audit(
        db,
        campaign_id=campaign_id,
        actor_id=user.id,
        action="milestone.created",
        description=f"Milestone “{body.name}” created — ₦{body.amount:,.2f}",
    )
    db.commit()
    db.refresh(milestone)
    return milestone_out(milestone)


@router.patch("/milestones/{milestone_id}")
def update_milestone(
    milestone_id: int, body: MilestoneUpdateIn, db: DBSession, user: CurrentUser
):
    milestone = db.get(Milestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    campaign = campaign_service.get_campaign(db, milestone.campaign_id)
    campaign_service.require_write(db, campaign, user)
    if milestone.status != "PENDING":
        raise HTTPException(status_code=409, detail="Approved milestones cannot be edited")

    data = body.model_dump(exclude_unset=True)
    if "amount" in data and data["amount"] is not None:
        data["amount"] = Decimal(str(data["amount"]))
    if "threshold_amount" in data and data["threshold_amount"] is not None:
        data["threshold_amount"] = Decimal(str(data["threshold_amount"]))
    if data.get("beneficiary_id") is not None:
        beneficiary = db.get(Beneficiary, data["beneficiary_id"])
        if beneficiary is None or beneficiary.campaign_id != campaign.id:
            raise HTTPException(status_code=404, detail="Beneficiary not found")
    for key, value in data.items():
        setattr(milestone, key, value)
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=user.id,
        action="milestone.updated",
        description=f"Milestone “{milestone.name}” updated",
    )
    db.commit()
    db.refresh(milestone)
    return milestone_out(milestone)


@router.post("/milestones/{milestone_id}/approve")
def approve_milestone(milestone_id: int, db: DBSession, user: CurrentUser):
    """Approve → creates the milestone's READY payout (§26-27)."""
    milestone = db.get(Milestone, milestone_id)
    if milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    campaign = campaign_service.get_campaign(db, milestone.campaign_id)
    campaign_service.require_write(db, campaign, user)
    payout = milestone_service.approve_milestone(
        db, milestone=milestone, campaign=campaign, actor=user
    )
    from app.schemas.outputs import payout_out

    return {
        "milestone": milestone_out(milestone),
        "payout": payout_out(payout),
    }
