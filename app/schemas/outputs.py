"""Response builders.

Amounts are exposed as floats (NGN, 2dp) — the DB keeps Decimal precision.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignMember, CampaignUpdate
from app.models.contribution import Contribution
from app.models.milestone import Milestone
from app.models.payout import Payout, PayoutRule
from app.models.user import User
from app.services.campaign_service import dashboard_stats


def _f(value) -> float:
    return float(value or 0)


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,
        "created_at": user.created_at,
    }


def account_out(campaign: Campaign) -> dict | None:
    account = campaign.account
    if account is None:
        return None
    return {
        "provider": account.provider,
        "account_number": account.kora_account_number,
        "account_reference": account.kora_account_reference,
        "bank_name": account.bank_name,
        "bank_code": account.bank_code,
        "currency": account.currency,
        "status": account.status,
    }


def campaign_out(campaign: Campaign, *, detail: bool = False, stats: dict | None = None) -> dict:
    owner_name = None
    if campaign.owner is not None:  # relationship set lazily via joined load or access
        owner_name = campaign.owner.name
    progress = 0.0
    target = _f(campaign.target_amount)
    if target > 0:
        progress = round(_f(campaign.raised_amount) / target * 100, 2)
    data = {
        "id": campaign.id,
        "title": campaign.title,
        "slug": campaign.slug,
        "short_description": campaign.short_description,
        "category": campaign.category,
        "visibility": campaign.visibility,
        "currency": campaign.currency,
        "target_amount": target,
        "raised_amount": _f(campaign.raised_amount),
        "disbursed_amount": _f(campaign.disbursed_amount),
        "progress": progress,
        "deadline": campaign.deadline,
        "status": campaign.status,
        "cover_image": campaign.cover_image,
        "is_diaspora": campaign.is_diaspora,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
        "owner": {"id": campaign.owner_id, "name": owner_name},
    }
    if detail:
        data["description"] = campaign.description
        data["account"] = account_out(campaign)
        data["stats"] = stats or {}
    return data


def contribution_out(contribution: Contribution, *, viewer_is_owner: bool = False) -> dict:
    display_name = contribution.contributor_name
    if contribution.anonymous:
        display_name = "Anonymous"
    return {
        "id": contribution.id,
        "campaign_id": contribution.campaign_id,
        "kora_reference": contribution.kora_reference,
        "amount": _f(contribution.amount),
        "currency": contribution.currency,
        "fee": _f(contribution.fee),
        "contributor": {"id": contribution.contributor_id, "name": display_name},
        "anonymous": contribution.anonymous,
        "status": contribution.status,
        "payment_method": contribution.payment_method,
        "transaction_date": contribution.transaction_date,
        "created_at": contribution.created_at,
        **(
            {"payer_bank_name": contribution.payer_bank_name}
            if viewer_is_owner
            else {}
        ),
    }


def beneficiary_out(b) -> dict:
    return {
        "id": b.id,
        "campaign_id": b.campaign_id,
        "name": b.name,
        "beneficiary_type": b.beneficiary_type,
        "bank_code": b.bank_code,
        "bank_name": b.bank_name,
        "account_number": b.account_number,
        "resolved_account_name": b.resolved_account_name,
        "currency": b.currency,
        "verification_status": b.verification_status,
        "created_at": b.created_at,
    }


def milestone_out(m: Milestone, *, state: str | None = None) -> dict:
    beneficiary = None
    if m.beneficiary is not None:
        beneficiary = {
            "id": m.beneficiary.id,
            "name": m.beneficiary.name,
            "bank_name": m.beneficiary.bank_name,
            "account_number": m.beneficiary.account_number,
            "verification_status": m.beneficiary.verification_status,
        }
    return {
        "id": m.id,
        "campaign_id": m.campaign_id,
        "order_index": m.order_index,
        "name": m.name,
        "description": m.description,
        "amount": _f(m.amount),
        "beneficiary": beneficiary,
        "trigger_type": m.trigger_type,
        "threshold_amount": _f(m.threshold_amount) if m.threshold_amount else None,
        "threshold_percentage": m.threshold_percentage,
        "due_date": m.due_date,
        "requires_approval": m.requires_approval,
        "status": m.status,
        "state": state or m.status,
        "approved_at": m.approved_at,
        "paid_at": m.paid_at,
        "created_at": m.created_at,
    }


def payout_out(p: Payout) -> dict:
    beneficiary = None
    if p.beneficiary is not None:
        beneficiary = {
            "id": p.beneficiary.id,
            "name": p.beneficiary.name,
            "bank_name": p.beneficiary.bank_name,
            "account_number": p.beneficiary.account_number,
            "verification_status": p.beneficiary.verification_status,
        }
    return {
        "id": p.id,
        "campaign_id": p.campaign_id,
        "kora_reference": p.kora_reference,
        "amount": _f(p.amount),
        "currency": p.currency,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "beneficiary": beneficiary,
        "milestone_id": p.milestone_id,
        "milestone_name": p.milestone.name if p.milestone else None,
        "rule_id": p.rule_id,
        "approved_at": p.approved_at,
        "created_at": p.created_at,
        "completed_at": p.completed_at,
    }


def rule_out(rule: PayoutRule) -> dict:
    beneficiary = None
    if rule.beneficiary is not None:
        beneficiary = {
            "id": rule.beneficiary.id,
            "name": rule.beneficiary.name,
            "verification_status": rule.beneficiary.verification_status,
        }
    return {
        "id": rule.id,
        "campaign_id": rule.campaign_id,
        "label": rule.label,
        "amount": _f(rule.amount) if rule.amount else None,
        "percentage": rule.percentage,
        "trigger_type": rule.trigger_type,
        "threshold_amount": _f(rule.threshold_amount) if rule.threshold_amount else None,
        "threshold_percentage": rule.threshold_percentage,
        "due_date": rule.due_date,
        "requires_approval": rule.requires_approval,
        "status": rule.status,
        "milestone_id": rule.milestone_id,
        "beneficiary": beneficiary,
        "created_at": rule.created_at,
    }


def update_out(u: CampaignUpdate) -> dict:
    return {
        "id": u.id,
        "campaign_id": u.campaign_id,
        "milestone_id": u.milestone_id,
        "title": u.title,
        "body": u.body,
        "image_url": u.image_url,
        "created_at": u.created_at,
    }


def member_out(member: CampaignMember, user: User | None) -> dict:
    return {
        "user_id": member.user_id,
        "role": member.role,
        "name": user.name if user else None,
        "email": user.email if user else None,
        "created_at": member.created_at,
    }


def notification_out(n) -> dict:
    return {
        "id": n.id,
        "campaign_id": n.campaign_id,
        "title": n.title,
        "body": n.body,
        "read": n.read,
        "created_at": n.created_at,
    }


def ledger_entry_out(entry) -> dict:
    return {
        "id": entry.id,
        "type": entry.type,
        "amount": _f(entry.amount),
        "currency": entry.currency,
        "reference": entry.reference,
        "description": entry.description,
        "created_at": entry.created_at,
    }


def audit_out(entry) -> dict:
    return {
        "id": entry.id,
        "actor_id": entry.actor_id,
        "action": entry.action,
        "description": entry.description,
        "reference": entry.reference,
        "created_at": entry.created_at,
    }


def stats_for(db: Session, campaign: Campaign) -> dict:
    return dashboard_stats(db, campaign)

