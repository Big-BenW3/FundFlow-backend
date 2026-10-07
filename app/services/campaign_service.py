"""Campaign lifecycle: creation, Kora collection account, access control."""

from __future__ import annotations

import re
import secrets
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.integrations.kora import get_kora_client
from app.models.campaign import Campaign, CampaignAccount, CampaignMember
from app.models.user import User
from app.services.activity import log_audit

CATEGORIES = [
    "Medical", "Education", "Community", "NGO", "Charity", "Emergency",
    "Family", "Business", "Religious", "Event", "Other",
]
WRITE_ROLES = {"OWNER", "ADMIN"}


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "campaign"
    return f"{slug}-{secrets.token_hex(3)}"


def create_campaign(db: Session, owner: User, data: dict) -> Campaign:
    campaign = Campaign(
        owner_id=owner.id,
        title=data["title"],
        slug=slugify(data["title"]),
        short_description=data.get("short_description"),
        description=data.get("description") or "",
        category=data.get("category") or "Other",
        visibility=data.get("visibility") or "PUBLIC",
        currency=data.get("currency") or "NGN",
        target_amount=Decimal(str(data["target_amount"])),
        deadline=data.get("deadline"),
        is_diaspora=bool(data.get("is_diaspora")),
        cover_image=data.get("cover_image"),
        status="DRAFT",
    )
    db.add(campaign)
    db.flush()
    # Owner membership carries OWNER rights on the campaign.
    db.add(CampaignMember(campaign_id=campaign.id, user_id=owner.id, role="OWNER"))
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=owner.id,
        action="campaign.created",
        description=f"Campaign “{campaign.title}” created",
    )
    db.commit()
    db.refresh(campaign)
    return campaign


def ensure_collection_account(db: Session, campaign: Campaign, owner: User) -> CampaignAccount:
    """Create the campaign's Kora virtual collection account (PRODUCT.md §19).

    Flow: generate account reference → Kora VBA → save → campaign ACTIVE.
    """
    if campaign.account:
        return campaign.account

    kora = get_kora_client()
    reference = f"FF-{campaign.slug.upper()[:24]}-{secrets.token_hex(3).upper()}"
    account_name = f"FundFlow {campaign.title}"[:64]

    result = kora.create_virtual_account(
        account_name=account_name,
        account_reference=reference,
        customer_name=owner.name,
        customer_email=owner.email,
        bank_code="000",  # sandbox bank code per Kora docs; live uses a listed bank
    )

    account = CampaignAccount(
        campaign_id=campaign.id,
        provider="kora",
        kora_account_reference=result.get("account_reference") or reference,
        kora_account_number=result["account_number"],
        bank_name=result.get("bank_name") or "Kora Sandbox Bank",
        bank_code=result.get("bank_code"),
        currency=campaign.currency,
        status=result.get("account_status") or "active",
    )
    db.add(account)
    campaign.status = "ACTIVE"
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=owner.id,
        action="collection_account.created",
        description=f"Kora collection account {account.kora_account_number} created",
        reference=account.kora_account_reference,
    )
    db.commit()
    db.refresh(campaign)
    return account


# ---------------------------------------------------------------- access
def get_campaign(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or campaign.status == "CANCELLED":
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def member_role(db: Session, campaign: Campaign, user: User | None) -> str | None:
    if user is None:
        return None
    if campaign.owner_id == user.id:
        return "OWNER"
    if user.role == "ADMIN":
        return "ADMIN"
    row = (
        db.query(CampaignMember)
        .filter(CampaignMember.campaign_id == campaign.id, CampaignMember.user_id == user.id)
        .first()
    )
    return row.role if row else None


def require_read(db: Session, campaign: Campaign, user: User | None) -> Campaign:
    """Private campaigns must not expose anything to non-members (§29).

    Outsiders receive 404 (existence itself is not leaked).
    """
    if campaign.visibility == "PRIVATE":
        if member_role(db, campaign, user) is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def require_write(db: Session, campaign: Campaign, user: User) -> Campaign:
    """Server-side permission check — never trust the frontend (§48)."""
    role = member_role(db, campaign, user)
    if role not in WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this campaign",
        )
    return campaign


def require_owner(db: Session, campaign: Campaign, user: User) -> Campaign:
    if campaign.owner_id != user.id and user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Only the campaign owner can do this")
    return campaign


# --------------------------------------------------------------- queries
def list_campaigns(
    db: Session,
    *,
    q: str | None = None,
    category: str | None = None,
    owner_id: int | None = None,
    limit: int = 60,
    offset: int = 0,
) -> list[Campaign]:
    query = db.query(Campaign).filter(Campaign.status != "CANCELLED")
    if owner_id is not None:
        query = query.filter(Campaign.owner_id == owner_id)
    else:
        # Public discovery: PUBLIC only (UNLISTED is link-only, PRIVATE hidden).
        query = query.filter(Campaign.visibility == "PUBLIC", Campaign.status != "DRAFT")
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Campaign.title.ilike(like), Campaign.short_description.ilike(like))
        )
    if category:
        query = query.filter(Campaign.category == category)
    return (
        query.order_by(Campaign.created_at.desc()).offset(offset).limit(limit).all()
    )


def dashboard_stats(db: Session, campaign: Campaign) -> dict:
    from datetime import datetime, timezone

    from sqlalchemy import func

    from app.models.contribution import Contribution
    from app.models.payout import Payout
    from app.services.ledger_service import balance

    raised = Decimal(str(campaign.raised_amount or 0))
    target = Decimal(str(campaign.target_amount or 0))
    progress = float(raised / target * 100) if target > 0 else 0.0

    contributors = (
        db.query(func.count(func.distinct(Contribution.contributor_id)))
        .filter(
            Contribution.campaign_id == campaign.id,
            Contribution.status == "SUCCESS",
        )
        .scalar()
        or 0
    )
    successful = (
        db.query(func.count(Contribution.id))
        .filter(Contribution.campaign_id == campaign.id, Contribution.status == "SUCCESS")
        .scalar()
        or 0
    )
    pending_payouts = (
        db.query(func.coalesce(func.sum(Payout.amount), 0))
        .filter(
            Payout.campaign_id == campaign.id,
            Payout.status.in_(("DRAFT", "LOCKED", "READY", "PROCESSING", "UNKNOWN")),
        )
        .scalar()
        or 0
    )
    days_remaining = None
    if campaign.deadline:
        deadline = campaign.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        days_remaining = max((deadline - datetime.now(timezone.utc)).days, 0)

    return {
        "raised": float(raised),
        "target": float(target),
        "progress": round(progress, 2),
        "contributors": int(contributors),
        "contributions_count": int(successful),
        "disbursed": float(campaign.disbursed_amount or 0),
        "available": float(balance(db, campaign.id)),
        "pending_payouts": float(pending_payouts),
        "days_remaining": days_remaining,
    }

