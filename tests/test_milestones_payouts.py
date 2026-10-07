"""Milestone + payout tests (§71: thresholds, approvals, valid/invalid payouts,
timeout → verify, duplicate payout webhook, security)."""

from tests.conftest import post_webhook


def create_milestone(client, user, campaign, beneficiary_id, **overrides):
    body = {
        "name": "Foundation",
        "description": "Foundation works",
        "amount": 500_000,
        "beneficiary_id": beneficiary_id,
        "requires_approval": True,
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/milestones",
        json=body,
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def contribute(client, user, campaign, amount=1_000_000):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": amount},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_beneficiary_verification_success(client, user, campaign):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/beneficiaries",
        json={"name": "ABC Construction", "bank_code": "033",
              "account_number": "1234567890"},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["verification_status"] == "VERIFIED"
    assert response.json()["resolved_account_name"] == "JOHN OKAFOR"


def test_beneficiary_verification_failure(client, user, campaign):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/beneficiaries",
        json={"name": "Ghost", "bank_code": "011", "account_number": "9999999999"},
        headers=user["headers"],
    )
    assert response.status_code == 400


def test_milestone_approval_requires_beneficiary(client, user, campaign):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/milestones",
        json={"name": "No Payee", "amount": 100_000},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    milestone = response.json()

    response = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert response.status_code == 400
    assert "beneficiary" in response.json()["detail"].lower()


def test_milestone_approval_rejects_unverified_beneficiary(client, user, campaign):
    from app.core.database import get_session_factory
    from app.models.beneficiary import Beneficiary

    session = get_session_factory()()
    beneficiary = Beneficiary(
        campaign_id=campaign["id"],
        name="Unverified Vendor",
        bank_code="033",
        bank_name="UBA",
        account_number="7777777777",
        verification_status="PENDING",
    )
    session.add(beneficiary)
    session.commit()
    beneficiary_id = beneficiary.id
    session.close()

    milestone = create_milestone(client, user, campaign, beneficiary_id)

    response = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert response.status_code == 400  # Rule 8


def test_milestone_threshold_not_reached_blocks_approval(client, user, campaign, beneficiary):
    milestone = create_milestone(
        client, user, campaign, beneficiary["id"],
        trigger_type="THRESHOLD", threshold_amount=5_000_000,
    )
    response = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert response.status_code == 409  # conditions not met

    contribute(client, user, campaign, amount=6_000_000)
    response = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert response.status_code == 200  # threshold reached


def test_full_milestone_payout_flow(client, user, campaign, beneficiary):
    """Approve -> READY -> execute -> SUCCESS -> milestone PAID (the §66 loop)."""
    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])

    approved = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert approved.status_code == 200, approved.text
    payout = approved.json()["payout"]
    assert payout["status"] == "READY"

    executed = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCESS"

    milestones = client.get(
        f"/api/v1/campaigns/{campaign['id']}/milestones", headers=user["headers"]
    ).json()
    assert milestones[0]["status"] == "PAID"
    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["disbursed_amount"] == 500_000
    assert detail["stats"]["available"] == 500_000


def test_milestone_already_paid_rejected(client, user, campaign, beneficiary):
    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    client.post(f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"])
    second = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert second.status_code == 409


def test_payout_insufficient_funds_locked(client, user, campaign, beneficiary):
    """A 500k milestone with only 300k raised -> LOCKED, cannot execute."""
    contribute(client, user, campaign, amount=300_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    approved = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    )
    assert approved.status_code == 200
    payout = approved.json()["payout"]
    assert payout["status"] == "LOCKED"

    executed = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    )
    assert executed.status_code == 409
    assert "funds" in executed.json()["detail"].lower()


def test_payout_failure_releases_hold(client, user, campaign):
    """Destination 035/0000000000 is Kora's failure scenario: FAILED + funds back."""
    failing = client.post(
        f"/api/v1/campaigns/{campaign['id']}/beneficiaries",
        json={"name": "Fail Bank", "bank_code": "035", "account_number": "0000000000"},
        headers=user["headers"],
    ).json()
    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, failing["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]

    executed = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    )
    body = executed.json()
    assert body["status"] == "FAILED"
    assert body["failure_reason"]

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["stats"]["available"] == 1_000_000  # hold released
    assert detail["disbursed_amount"] == 0


def test_payout_rejected_upfront_for_invalid_account(client, user, campaign):
    """The Kora-documented invalid account (9999999999) → execute fails cleanly."""
    from app.core.database import get_session_factory
    from app.models.beneficiary import Beneficiary

    session = get_session_factory()()
    phantom = Beneficiary(
        campaign_id=campaign["id"],
        name="Phantom Payee",
        bank_code="011",
        bank_name="First Bank of Nigeria",
        account_number="9999999999",
        verification_status="VERIFIED",  # force past Rule 8 to reach Kora
    )
    session.add(phantom)
    session.commit()
    phantom_id = phantom.id
    session.close()

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, phantom_id)
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]

    body = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    ).json()
    assert body["status"] == "FAILED"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["stats"]["available"] == 1_000_000  # nothing was held/lost


def test_payout_timeout_verified_not_failed(client, user, campaign, beneficiary, monkeypatch):
    """504 during create_payout → UNKNOWN → verify → SUCCESS (Rule 4 / §26)."""
    from app.integrations.kora import get_kora_client
    from app.integrations.kora.client import KoraAPIError

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]

    kora = get_kora_client()
    real_create = kora._mock.create_payout

    def flaky_create(**kwargs):
        real_create(**kwargs)  # Kora accepted it...
        raise KoraAPIError("504 Gateway Timeout", status_code=504, retryable=True)

    monkeypatch.setattr(kora._mock, "create_payout", flaky_create)

    response = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    )
    assert response.status_code == 200, response.text
    # The verify call found the accepted transfer → SUCCESS, never FAILED.
    payout_after = client.get(
        f"/api/v1/payouts/{payout['id']}", headers=user["headers"]
    ).json()
    assert payout_after["status"] == "SUCCESS"


def test_duplicate_payout_webhook_ignored(client, user, campaign, beneficiary):
    """Re-delivering transfer.success must not move money twice."""
    from app.integrations.kora.webhooks import build_transfer_event

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]
    client.post(f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"])

    reference = client.get(
        f"/api/v1/payouts/{payout['id']}", headers=user["headers"]
    ).json()["kora_reference"]
    assert reference
    payload = build_transfer_event(
        "transfer.success", reference=reference, amount=500_000, currency="NGN"
    )
    first = post_webhook(client, payload).json()
    second = post_webhook(client, payload).json()
    assert first["status"] == "duplicate"
    assert second["status"] == "duplicate"

    detail = client.get(f"/api/v1/campaigns/{campaign['id']}", headers=user["headers"]).json()
    assert detail["disbursed_amount"] == 500_000





