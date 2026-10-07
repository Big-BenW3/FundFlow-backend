"""Beneficiary endpoints (PRODUCT.md §13-14, §47).

POST/GET /campaigns/{id}/beneficiaries · POST /beneficiaries/{id}/verify
GET /banks — bank list for the payout form
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.security import CurrentUser, DBSession, OptionalUser
from app.integrations.kora import KoraAPIError, get_kora_client
from app.models.beneficiary import Beneficiary
from app.schemas.inputs import BeneficiaryCreateIn
from app.schemas.outputs import beneficiary_out
from app.services import campaign_service
from app.services.activity import log_audit

router = APIRouter(tags=["beneficiaries"])


@router.get("/banks")
def list_banks():
    kora = get_kora_client()
    return {"banks": kora.list_banks("NG"), "mode": "mock" if kora.is_mock else "live"}


@router.get("/campaigns/{campaign_id}/beneficiaries")
def list_beneficiaries(campaign_id: int, db: DBSession, user: OptionalUser):
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_read(db, campaign, user)
    rows = (
        db.query(Beneficiary)
        .filter(Beneficiary.campaign_id == campaign_id)
        .order_by(Beneficiary.created_at.asc())
        .all()
    )
    return [beneficiary_out(b) for b in rows]


@router.post("/campaigns/{campaign_id}/beneficiaries", status_code=201)
def create_beneficiary(
    campaign_id: int, body: BeneficiaryCreateIn, db: DBSession, user: CurrentUser
):
    """Create + resolve in one step (§14: bank + account → Kora resolve →
    user confirms → saved). The response carries the resolved account name."""
    campaign = campaign_service.get_campaign(db, campaign_id)
    campaign_service.require_write(db, campaign, user)

    kora = get_kora_client()
    resolved_name = None
    bank_name = None
    verification_status = "PENDING"
    try:
        resolved = kora.resolve_bank_account(body.bank_code, body.account_number, "NGN")
        resolved_name = resolved.get("account_name")
        bank_name = resolved.get("bank_name")
        verification_status = "VERIFIED"
    except KoraAPIError:
        verification_status = "FAILED"

    if verification_status == "FAILED":
        raise HTTPException(
            status_code=400,
            detail="We could not verify that account — check the bank and account number",
        )

    existing = (
        db.query(Beneficiary)
        .filter(
            Beneficiary.campaign_id == campaign_id,
            Beneficiary.bank_code == body.bank_code,
            Beneficiary.account_number == body.account_number,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Beneficiary already added")

    beneficiary = Beneficiary(
        campaign_id=campaign_id,
        name=body.name,
        beneficiary_type=body.beneficiary_type,
        bank_code=body.bank_code,
        bank_name=bank_name or body.bank_code,
        account_number=body.account_number,
        resolved_account_name=resolved_name,
        currency="NGN",
        verification_status=verification_status,
    )
    db.add(beneficiary)
    log_audit(
        db,
        campaign_id=campaign_id,
        actor_id=user.id,
        action="beneficiary.verified",
        description=(
            f"Beneficiary {beneficiary.name} verified — "
            f"{bank_name} {body.account_number} → {resolved_name}"
        ),
    )
    db.commit()
    db.refresh(beneficiary)
    return beneficiary_out(beneficiary)


@router.post("/beneficiaries/{beneficiary_id}/verify")
def verify_beneficiary(beneficiary_id: int, db: DBSession, user: CurrentUser):
    """Re-run Kora bank resolve for an existing beneficiary (§14)."""
    beneficiary = db.get(Beneficiary, beneficiary_id)
    if beneficiary is None:
        raise HTTPException(status_code=404, detail="Beneficiary not found")
    campaign = campaign_service.get_campaign(db, beneficiary.campaign_id)
    campaign_service.require_write(db, campaign, user)

    kora = get_kora_client()
    try:
        resolved = kora.resolve_bank_account(
            beneficiary.bank_code, beneficiary.account_number, beneficiary.currency
        )
    except KoraAPIError:
        beneficiary.verification_status = "FAILED"
        db.commit()
        raise HTTPException(status_code=400, detail="Account could not be resolved")

    beneficiary.resolved_account_name = resolved.get("account_name")
    beneficiary.bank_name = resolved.get("bank_name") or beneficiary.bank_name
    beneficiary.verification_status = "VERIFIED"
    db.commit()
    db.refresh(beneficiary)
    return beneficiary_out(beneficiary)
