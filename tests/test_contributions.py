"""Contribution tests (§71: success, failed, duplicate webhook, bad signature,
verification failure)."""

from tests.conftest import kora_mock, make_charge_payload, post_webhook


def test_successful_contribution_flow(client, user, campaign):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 250_000, "anonymous": False},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    contribution = response.json()
    assert contribution["status"] == "SUCCESS"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["raised_amount"] == 250_000
    assert detail["stats"]["raised"] == 250_000
    assert detail["stats"]["contributions_count"] == 1


def test_anonymous_contribution_masks_name(client, user, campaign):
    contribution = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 50_000, "anonymous": True},
        headers=user["headers"],
    ).json()
    assert contribution["anonymous"] is True
    assert contribution["contributor"]["name"] == "Anonymous"


def test_minimum_amount_rejected(client, user, campaign):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 50},
        headers=user["headers"],
    )
    assert response.status_code == 400


def test_duplicate_webhook_ignored(client, user, campaign):
    contribution = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 100_000},
        headers=user["headers"],
    ).json()
    payload = make_charge_payload(contribution)

    first = post_webhook(client, payload)
    second = post_webhook(client, payload)
    assert first.json()["status"] == "duplicate"
    assert second.json()["status"] == "duplicate"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["raised_amount"] == 100_000


def test_invalid_webhook_signature_rejected(client, user, campaign):
    contribution = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 100_000},
        headers=user["headers"],
    ).json()
    payload = make_charge_payload(contribution)
    response = post_webhook(client, payload, signature="tampered-signature")
    assert response.status_code == 401

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["raised_amount"] == 100_000


def test_webhook_without_signature_rejected(client, user, campaign):
    contribution = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 100_000},
        headers=user["headers"],
    ).json()
    payload = make_charge_payload(contribution)
    response = client.post("/api/v1/webhooks/kora", json=payload)
    assert response.status_code == 401


def test_forged_reference_never_credited(client, user, campaign):
    """A correctly-signed webhook for a transaction Kora never saw → unmatched."""
    payload = {
        "event": "charge.success",
        "data": {
            "reference": "KPY-PAY-FORGED9999",
            "amount": 999_000,
            "fee": 0,
            "currency": "NGN",
            "status": "success",
        },
    }
    result = post_webhook(client, payload).json()
    assert result["status"] == "unmatched"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["raised_amount"] == 0


def test_tampered_webhook_amount_rejected(client, user, campaign):
    """Webhook lies about the amount → verification mismatch → FAILED."""
    from app.core.database import get_session_factory
    from app.models.campaign import Campaign
    from app.models.user import User

    session = get_session_factory()()
    real_campaign = session.get(Campaign, campaign["id"])
    real_user = session.query(User).filter(
        User.email == user["user"]["email"]
    ).first()
    from decimal import Decimal

    from app.models.contribution import Contribution

    pending = Contribution(
        campaign_id=real_campaign.id,
        kora_reference="KPY-PAY-TAMPER001",
        amount=Decimal("200000"),
        currency="NGN",
        contributor_id=real_user.id,
        contributor_name=real_user.name,
        anonymous=False,
        status="PENDING",
    )
    session.add(pending)
    session.commit()
    # Register a DIFFERENT amount at mock Kora → verification mismatch.
    kora_mock().register_charge("KPY-PAY-TAMPER001", 100_000, "NGN")
    session.close()

    payload = make_charge_payload(
        {"kora_reference": "KPY-PAY-TAMPER001", "amount": 999_999, "currency": "NGN"}
    )
    result = post_webhook(client, payload).json()
    assert result["status"] == "verification_failed"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["raised_amount"] == 0, result

