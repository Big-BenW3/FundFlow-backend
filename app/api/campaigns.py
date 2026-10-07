"""Campaign endpoints (PRODUCT.md §47, §8-17, §31-37).

Includes: CRUD, Kora collection account, dashboard stats, activity,
audit log, updates, members, payout rules, evaluation and reconciliation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.security import CurrentUser, DBSession, OptionalUser
from app.integrations.kora import KoraAPIError, get_kora_client
from app.models.campaign import Campaign, CampaignMember, CampaignUpdate
from app.models.user import User
from app.schemas.inputs import (
    CampaignCreateIn,
    CampaignUpdateIn,
    MemberAddIn,
    PayoutRuleCreateIn,
    UpdateCreateIn,
)
from app.schemas.outputs import (
    campaign_out,
    member_out,
    stats_for,
    update_out,
)
from app.services import campaign_service
from app.services.activity import log_audit

router = APIRouter(prefix="/campaigns", tags=["campaigns"])



@router.get("")
def list_campaigns(
    db: DBSession,
    user: OptionalUser,
    q: str | None = None,
    category: str | None = None,
    mine: bool = False,
    limit: int = Query(default=60, le=100),
    offset: int = 0,
):
    owner_id = None
    if mine:
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to view your campaigns")
        owner_id = user.id
    campaigns = campaign_service.list_campaigns(
        db, q=q, category=category, owner_id=owner_id, limit=limit, offset=offset
    )
    return [campaign_out(c) for c in campaigns]


@router.post("", status_code=201)
def create_campaign(body: CampaignCreateIn, db: DBSession, user: CurrentUser):
    """Create campaign → generate Kora collection account → ACTIVE (§19)."""
    campaign = campaign_service.create_campaign(db, user, body.model_dump())
    try:
        campaign_service.ensure_collection_account(db, campaign, user)
    except KoraAPIError as exc:
        db.refresh(campaign)
        return {
            "campaign": campaign_out(campaign, detail=True, stats=stats_for(db, campaign)),
            "account_error": exc.message,
        }
    db.refresh(campaign)
    return {
        "campaign": campaign_out(campaign, detail=True, stats=stats_for(db, campaign)),
        "account_error": None,
    }


# NOTE: /slug/{slug} is declared before /{campaign_id} so slug lookups win.
@router.get("/slug/{slug}")
def get_campaign_by_slug(slug: str, db: DBSession, user: OptionalUser):
    campaign = db.query(Campaign).filter(Campaign.slug == slug).first()
    if campaign is None or campaign.status == "CANCELLED":
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign_service.require_read(db, campaign, user)
    return campaign_out(campaign, detail=True, stats=stats_for(db, campaign))


@router.get("/{campaign_id}")
def get_campaign(campaign_id: int, db: DBSession, user: OptionalUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    return campaign_out(campaign, detail=True, stats=stats_for(db, campaign))


@router.patch("/{campaign_id}")
def update_campaign(campaign_id: int, body: CampaignUpdateIn, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(campaign, key, value)
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=user.id,
        action="campaign.updated",
        description=f"Campaign updated ({', '.join(data) or 'no fields'})",
    )
    db.commit()
    db.refresh(campaign)
    return campaign_out(campaign, detail=True, stats=stats_for(db, campaign))


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: int, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_owner(db, campaign, user)
    campaign.status = "CANCELLED"
    log_audit(
        db,
        campaign_id=campaign.id,
        actor_id=user.id,
        action="campaign.cancelled",
        description="Campaign cancelled",
        commit=True,
    )
    return {"status": "cancelled", "id": campaign.id}


# ------------------------------------------------------- collection account
@router.post("/{campaign_id}/account", status_code=201)
def create_collection_account(campaign_id: int, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    try:
        account = campaign_service.ensure_collection_account(db, campaign, user)
    except KoraAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Kora error: {exc.message}")
    return {
        "provider": account.provider,
        "account_number": account.kora_account_number,
        "account_reference": account.kora_account_reference,
        "bank_name": account.bank_name,
        "currency": account.currency,
        "status": account.status,
    }


@router.get("/{campaign_id}/account")
def get_collection_account(campaign_id: int, db: DBSession, user: OptionalUser):
    from app.schemas.outputs import account_out

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    data = account_out(campaign)
    if data is None:
        raise HTTPException(status_code=404, detail="No collection account yet")
    return data


@router.get("/{campaign_id}/dashboard")
def campaign_dashboard(campaign_id: int, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    stats = stats_for(db, campaign)
    kora = get_kora_client()
    return {
        "campaign": campaign_out(campaign, detail=True, stats=stats),
        "stats": stats,
        "mode": "mock" if kora.is_mock else "live",
    }


# --------------------------------------------------- transparency (public)
@router.get("/{campaign_id}/activity")
def campaign_activity(campaign_id: int, db: DBSession, user: OptionalUser):
    """Public transparency feed (§16-17, §28): contributions (anonymised),
    payout history and the ledger trace."""
    from app.models.contribution import Contribution
    from app.models.ledger import LedgerEntry
    from app.models.payout import Payout
    from app.schemas.outputs import contribution_out, ledger_entry_out, payout_out

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    is_writer = campaign_service.member_role(db, campaign, user) in ("OWNER", "ADMIN")

    contributions = (
        db.query(Contribution)
        .filter(Contribution.campaign_id == campaign_id)
        .order_by(Contribution.created_at.desc())
        .limit(50)
        .all()
    )
    payouts = (
        db.query(Payout)
        .filter(Payout.campaign_id == campaign_id)
        .order_by(Payout.created_at.desc())
        .all()
    )
    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.campaign_id == campaign_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(80)
        .all()
    )
    return {
        "contributions": [
            contribution_out(c, viewer_is_owner=is_writer)
            for c in contributions
            if is_writer or c.status == "SUCCESS"
        ],
        "payouts": [payout_out(p) for p in payouts],
        "ledger": [
            ledger_entry_out(e) for e in entries
            if is_writer or e.type != "ADJUSTMENT"
        ],
    }


@router.get("/{campaign_id}/audit")
def campaign_audit(campaign_id: int, db: DBSession, user: CurrentUser):
    from app.models.audit import AuditLog
    from app.schemas.outputs import audit_out

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.campaign_id == campaign_id)
        .order_by(AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    return [audit_out(r) for r in rows]


# ------------------------------------------------------------------ updates
@router.get("/{campaign_id}/updates")
def list_updates(campaign_id: int, db: DBSession, user: OptionalUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    rows = (
        db.query(CampaignUpdate)
        .filter(CampaignUpdate.campaign_id == campaign_id)
        .order_by(CampaignUpdate.created_at.desc())
        .all()
    )
    return [update_out(u) for u in rows]


@router.post("/{campaign_id}/updates", status_code=201)
def create_update(campaign_id: int, body: UpdateCreateIn, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    update = CampaignUpdate(campaign_id=campaign_id, **body.model_dump())
    db.add(update)
    log_audit(
        db,
        campaign_id=campaign_id,
        actor_id=user.id,
        action="update.posted",
        description=f"Update posted: “{update.title}”",
    )
    db.commit()
    db.refresh(update)
    return update_out(update)


# ------------------------------------------------------------------ members
@router.get("/{campaign_id}/members")
def list_members(campaign_id: int, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    members = (
        db.query(CampaignMember).filter(CampaignMember.campaign_id == campaign_id).all()
    )
    ids = [m.user_id for m in members]
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    )
    return [member_out(m, users.get(m.user_id)) for m in members]


@router.post("/{campaign_id}/members", status_code=201)
def add_member(campaign_id: int, body: MemberAddIn, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_owner(db, campaign, user)
    target = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if target is None:
        raise HTTPException(
            status_code=404, detail="No user with that email — ask them to sign up"
        )
    existing = (
        db.query(CampaignMember)
        .filter(
            CampaignMember.campaign_id == campaign_id,
            CampaignMember.user_id == target.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already a member")
    member = CampaignMember(campaign_id=campaign_id, user_id=target.id, role=body.role)
    db.add(member)
    log_audit(
        db,
        campaign_id=campaign_id,
        actor_id=user.id,
        action="member.added",
        description=f"{target.email} added as {body.role}",
    )
    db.commit()
    db.refresh(member)
    return member_out(member, target)


@router.delete("/{campaign_id}/members/{user_id}")
def remove_member(campaign_id: int, user_id: int, db: DBSession, user: CurrentUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_owner(db, campaign, user)
    if user_id == campaign.owner_id:
        raise HTTPException(status_code=400, detail="The owner cannot be removed")
    member = (
        db.query(CampaignMember)
        .filter(
            CampaignMember.campaign_id == campaign_id,
            CampaignMember.user_id == user_id,
        )
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(member)
    db.commit()
    return {"status": "removed"}


# ------------------------------------------------------------- payout rules
@router.get("/{campaign_id}/payout-rules")
def list_payout_rules(campaign_id: int, db: DBSession, user: OptionalUser):
    from app.models.payout import PayoutRule
    from app.schemas.outputs import rule_out

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    rules = (
        db.query(PayoutRule)
        .filter(PayoutRule.campaign_id == campaign_id)
        .order_by(PayoutRule.created_at.asc())
        .all()
    )
    return [rule_out(r) for r in rules]


@router.post("/{campaign_id}/payout-rules", status_code=201)
def create_payout_rule(
    campaign_id: int, body: PayoutRuleCreateIn, db: DBSession, user: CurrentUser
):
    from decimal import Decimal

    from app.models.beneficiary import Beneficiary
    from app.models.payout import PayoutRule
    from app.schemas.outputs import rule_out

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    beneficiary = db.get(Beneficiary, body.beneficiary_id)
    if beneficiary is None or beneficiary.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Beneficiary not found")
    if body.trigger_type in ("THRESHOLD", "DATE", "MANUAL", "HYBRID") and not body.amount:
        raise HTTPException(status_code=400, detail="amount is required for this trigger")
    if body.trigger_type == "PERCENTAGE":
        if not body.percentage:
            raise HTTPException(status_code=400, detail="percentage is required")
        if not body.threshold_percentage:
            raise HTTPException(status_code=400, detail="threshold_percentage is required")
    if body.trigger_type in ("THRESHOLD", "HYBRID") and not body.threshold_amount:
        raise HTTPException(status_code=400, detail="threshold_amount is required")
    if body.trigger_type == "DATE" and not body.due_date:
        raise HTTPException(status_code=400, detail="due_date is required")

    rule = PayoutRule(
        campaign_id=campaign_id,
        beneficiary_id=beneficiary.id,
        label=body.label,
        amount=Decimal(str(body.amount)) if body.amount else None,
        percentage=body.percentage,
        trigger_type=body.trigger_type,
        threshold_amount=Decimal(str(body.threshold_amount)) if body.threshold_amount else None,
        threshold_percentage=body.threshold_percentage,
        due_date=body.due_date,
        requires_approval=body.requires_approval,
        status="LOCKED",
    )
    db.add(rule)
    log_audit(
        db,
        campaign_id=campaign_id,
        actor_id=user.id,
        action="payout_rule.created",
        description=f"Rule “{body.label or body.trigger_type}” created",
    )
    db.commit()
    db.refresh(rule)
    return rule_out(rule)


@router.post("/{campaign_id}/payout-rules/{rule_id}/trigger")
def trigger_payout_rule(campaign_id: int, rule_id: int, db: DBSession, user: CurrentUser):
    from app.models.payout import PayoutRule
    from app.schemas.outputs import payout_out
    from app.services import payout_service

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    rule = db.get(PayoutRule, rule_id)
    if rule is None or rule.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    payout = payout_service.trigger_rule(db, rule=rule, campaign=campaign, actor=user)
    return payout_out(payout)


# ------------------------------------------------- evaluate / reconcile
@router.post("/{campaign_id}/evaluate")
def evaluate_campaign_rules(campaign_id: int, db: DBSession, user: CurrentUser):
    from app.schemas.outputs import payout_out
    from app.services import milestone_service, payout_service

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    created = payout_service.evaluate_rules(db, campaign)
    milestone_service.evaluate_milestones(db, campaign)
    db.commit()
    return {"created": [payout_out(p) for p in created]}


@router.post("/{campaign_id}/reconcile")
def reconcile_campaign(campaign_id: int, db: DBSession, user: CurrentUser):
    from app.services import reconciliation_service

    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)
    return reconciliation_service.reconcile_campaign(db, campaign, actor_id=user.id)






