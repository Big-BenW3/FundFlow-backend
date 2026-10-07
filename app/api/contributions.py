"""Contribution endpoints (PRODUCT.md §18, §47).

GET /campaigns/{id}/contributions · POST /campaigns/{id}/contribute
GET /contributions/{id}
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.rate_limiter import contribute_limit
from app.core.security import CurrentUser, DBSession, OptionalUser
from app.models.contribution import Contribution
from app.schemas.inputs import ContributeIn
from app.schemas.outputs import contribution_out
from app.services import campaign_service, contribution_service

router = APIRouter(tags=["contributions"])


@router.get("/campaigns/{campaign_id}/contributions")
def list_contributions(
    campaign_id: int,
    db: DBSession,
    user: CurrentUser,
    status: str | None = None,
    anonymous: bool | None = None,
    limit: int = Query(default=100, le=500),
):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    query = db.query(Contribution).filter(Contribution.campaign_id == campaign_id)
    if status:
        query = query.filter(Contribution.status == status.upper())
    if anonymous is not None:
        query = query.filter(Contribution.anonymous == anonymous)
    rows = query.order_by(Contribution.created_at.desc()).limit(limit).all()
    return [contribution_out(c, viewer_is_owner=True) for c in rows]


@router.post(
    "/campaigns/{campaign_id}/contribute",
    status_code=201,
    dependencies=[Depends(contribute_limit)],
)
def contribute(
    campaign_id: int,
    body: ContributeIn,
    db: DBSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    """Start a contribution. In mock mode Kora's charge.success webhook is
    delivered immediately through the full verified pipeline."""
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    contribution = contribution_service.initiate_contribution(
        db,
        campaign=campaign,
        user=user,
        amount=body.amount,
        anonymous=body.anonymous,
        payment_method=body.payment_method,
        background_tasks=background_tasks,
    )
    db.refresh(contribution)
    return contribution_out(contribution, viewer_is_owner=True)


@router.get("/contributions/{contribution_id}")
def get_contribution(contribution_id: int, db: DBSession, user: OptionalUser):
    contribution = db.get(Contribution, contribution_id)
    if contribution is None:
        raise HTTPException(status_code=404, detail="Contribution not found")
    campaign = campaign_service.get_campaign(db, contribution.campaign_id)
    campaign_service.require_read(db, campaign, user)
    is_owner = campaign_service.member_role(db, campaign, user) in ("OWNER", "ADMIN")
    is_contributor = user is not None and contribution.contributor_id == user.id
    if not (is_owner or is_contributor or campaign.visibility != "PRIVATE"):
        raise HTTPException(status_code=404, detail="Contribution not found")
    return contribution_out(contribution, viewer_is_owner=is_owner)
