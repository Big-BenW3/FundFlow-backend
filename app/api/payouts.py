"""Payout endpoints (PRODUCT.md §26-27, §47).

GET /campaigns/{id}/payouts · GET /payouts/{id}
POST /payouts/{id}/approve · POST /payouts/{id}/execute
POST /payouts/{id}/verify — resolve PROCESSING/UNKNOWN via Kora query
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.security import CurrentUser, DBSession, OptionalUser
from app.models.payout import Payout
from app.schemas.outputs import payout_out
from app.services import campaign_service, payout_service

router = APIRouter(tags=["payouts"])


@router.get("/campaigns/{campaign_id}/payouts")
def list_payouts(campaign_id: int, db: DBSession, user: OptionalUser, status: str | None = None):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    query = db.query(Payout).filter(Payout.campaign_id == campaign_id)
    if status:
        query = query.filter(Payout.status == status.upper())
    rows = query.order_by(Payout.created_at.desc()).all()
    return [payout_out(p) for p in rows]


@router.get("/payouts/{payout_id}")
def get_payout(payout_id: int, db: DBSession, user: OptionalUser):
    payout = db.get(Payout, payout_id)
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")
    campaign = campaign_service.get_campaign(db, payout.campaign_id)
    campaign_service.require_read(db, campaign, user)
    return payout_out(payout)


@router.post("/payouts/{payout_id}/approve")
def approve_payout(payout_id: int, db: DBSession, user: CurrentUser):
    payout = db.get(Payout, payout_id)
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")
    campaign = campaign_service.get_campaign(db, payout.campaign_id)
    campaign_service.require_write(db, campaign, user)
    payout = payout_service.approve_payout(db, payout=payout, campaign=campaign, actor=user)
    return payout_out(payout)


@router.post("/payouts/{payout_id}/execute")
def execute_payout(
    payout_id: int,
    db: DBSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    """Execute a READY payout via Kora (mock delivers transfer webhook)."""
    payout = db.get(Payout, payout_id)
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")
    campaign = campaign_service.get_campaign(db, payout.campaign_id)
    campaign_service.require_write(db, campaign, user)
    payout = payout_service.execute_payout(
        db,
        payout=payout,
        campaign=campaign,
        actor=user,
        background_tasks=background_tasks,
    )
    db.refresh(payout)
    return payout_out(payout)


@router.post("/payouts/{payout_id}/verify")
def verify_payout(payout_id: int, db: DBSession, user: CurrentUser):
    """Resolve a PROCESSING/UNKNOWN payout by querying Kora (§26).

    Gives ops (and the frontend) a manual driver besides the async
    transfer webhook and the retryable-exception path.
    """
    payout = db.get(Payout, payout_id)
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")
    campaign = campaign_service.get_campaign(db, payout.campaign_id)
    campaign_service.require_write(db, campaign, user)
    if payout.status not in ("PROCESSING", "UNKNOWN"):
        raise HTTPException(
            status_code=409, detail=f"Payout is {payout.status}, nothing to verify"
        )
    if not payout.kora_reference:
        raise HTTPException(status_code=409, detail="Payout has no Kora reference yet")
    payout = payout_service.verify_and_settle(db, payout=payout, campaign=campaign)
    db.refresh(payout)
    return payout_out(payout)
