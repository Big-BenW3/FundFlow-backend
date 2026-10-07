"""Dashboard, notifications and admin endpoints (§31-32, §50-51)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

from app.core.security import CurrentUser, DBSession, require_admin
from app.models.campaign import Campaign
from app.models.notification import Notification
from app.models.payout import Payout
from app.models.user import User
from app.models.webhook import WebhookEvent
from app.schemas.outputs import campaign_out, notification_out
from app.services.campaign_service import dashboard_stats

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/overview")
def overview(db: DBSession, user: CurrentUser):
    """Aggregate stats across every campaign the user owns (§31)."""
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.owner_id == user.id, Campaign.status != "CANCELLED")
        .order_by(Campaign.created_at.desc())
        .all()
    )
    cards = []
    total_raised = 0.0
    total_disbursed = 0.0
    for campaign in campaigns:
        stats = dashboard_stats(db, campaign)
        total_raised += stats["raised"]
        total_disbursed += stats["disbursed"]
        cards.append({"campaign": campaign_out(campaign), "stats": stats})

    unread = (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == user.id, Notification.read == False)  # noqa: E712
        .scalar()
        or 0
    )
    return {
        "campaigns": cards,
        "totals": {
            "campaigns": len(cards),
            "raised": round(total_raised, 2),
            "disbursed": round(total_disbursed, 2),
            "contributors": sum(c["stats"]["contributors"] for c in cards),
        },
        "unread_notifications": int(unread),
    }


# ------------------------------------------------------------ notifications
@router.get("/notifications")
def list_notifications(db: DBSession, user: CurrentUser, unread_only: bool = False):
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        query = query.filter(Notification.read == False)  # noqa: E712
    rows = query.order_by(Notification.created_at.desc()).limit(50).all()
    return [notification_out(n) for n in rows]


@router.post("/notifications/read-all")
def mark_all_read(db: DBSession, user: CurrentUser):
    db.query(Notification).filter(
        Notification.user_id == user.id, Notification.read == False  # noqa: E712
    ).update({"read": True})
    db.commit()
    return {"status": "ok"}


# ------------------------------------------------------------------- admin
@router.get("/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview(db: DBSession):
    """Platform admin view (PRODUCT.md §51)."""
    from app.models.contribution import Contribution

    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).limit(100).all()
    failed_contributions = (
        db.query(Contribution).filter(Contribution.status == "FAILED").count()
    )
    unknown_payouts = db.query(Payout).filter(Payout.status == "UNKNOWN").count()
    webhook_count = db.query(func.count(WebhookEvent.id)).scalar() or 0
    return {
        "counts": {
            "users": db.query(func.count(User.id)).scalar() or 0,
            "campaigns": db.query(func.count(Campaign.id)).scalar() or 0,
            "contributions": db.query(func.count(Contribution.id)).scalar() or 0,
            "payouts": db.query(func.count(Payout.id)).scalar() or 0,
            "webhook_events": int(webhook_count),
            "failed_contributions": failed_contributions,
            "unknown_payouts": unknown_payouts,
        },
        "campaigns": [campaign_out(c) for c in campaigns],
        "recent_webhooks": [
            {
                "id": w.id,
                "event_type": w.event_type,
                "provider_reference": w.provider_reference,
                "processed": w.processed,
                "created_at": w.created_at,
            }
            for w in db.query(WebhookEvent)
            .order_by(WebhookEvent.created_at.desc())
            .limit(25)
            .all()
        ],
    }


@router.post("/admin/campaigns/{campaign_id}/suspend", dependencies=[Depends(require_admin)])
def suspend_campaign(campaign_id: int, db: DBSession):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    from app.services.activity import log_audit

    campaign.status = "PAUSED"
    log_audit(
        db,
        campaign_id=campaign.id,
        action="campaign.suspended",
        description="Suspended by platform admin",
        commit=True,
    )
    return {"status": "suspended", "id": campaign.id}

