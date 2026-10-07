"""Audit log + in-app notification helpers (PRODUCT.md §37, §50)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.campaign import Campaign, CampaignMember
from app.models.notification import Notification


def log_audit(
    db: Session,
    *,
    campaign_id: int | None,
    action: str,
    description: str,
    actor_id: int | None = None,
    reference: str | None = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        campaign_id=campaign_id,
        actor_id=actor_id,
        action=action,
        description=description,
        reference=reference,
    )
    db.add(entry)
    if commit:
        db.commit()
    else:
        db.flush()
    return entry


def notify(
    db: Session,
    *,
    user_id: int,
    title: str,
    body: str = "",
    campaign_id: int | None = None,
    commit: bool = False,
) -> Notification | None:
    if user_id is None:
        return None
    note = Notification(user_id=user_id, campaign_id=campaign_id, title=title, body=body)
    db.add(note)
    if commit:
        db.commit()
    else:
        db.flush()
    return note


def notify_campaign_team(
    db: Session,
    campaign: Campaign,
    *,
    title: str,
    body: str = "",
    exclude_user_id: int | None = None,
) -> None:
    """Notify the campaign owner and all members (except the actor)."""
    user_ids = {campaign.owner_id}
    members = (
        db.query(CampaignMember).filter(CampaignMember.campaign_id == campaign.id).all()
    )
    user_ids.update(m.user_id for m in members)
    if exclude_user_id is not None:
        user_ids.discard(exclude_user_id)
    for uid in user_ids:
        notify(db, user_id=uid, campaign_id=campaign.id, title=title, body=body)
