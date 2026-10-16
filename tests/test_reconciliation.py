"""End-to-end reconciliation + transparency smoke tests."""


def test_reconciliation_is_clean_after_full_flow(client, user, campaign, beneficiary):
    from tests.test_milestones_payouts import contribute, create_milestone

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]
    client.post(f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"])

    report = client.post(
        f"/api/v1/campaigns/{campaign['id']}/reconcile", headers=user["headers"]
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["clean"] is True
    assert body["mismatches"] == []
    assert body["checked"] >= 2


def test_reconciliation_flags_pending_contribution(client, user, campaign, monkeypatch):
    """PENDING rows are checked too: provider SUCCESS vs our PENDING = mismatch."""
    from app.integrations.kora import get_kora_client

    # Create a PENDING contribution directly (skip mock auto-settle).
    monkeypatch.setattr(get_kora_client(), "is_mock", False)
    try:
        created = client.post(
            f"/api/v1/campaigns/{campaign['id']}/contribute",
            json={"amount": 5_000},
            headers=user["headers"],
        )
        assert created.status_code == 201, created.text
        pending_ref = created.json()["kora_reference"]
        # Provider knows the charge as success (live would credit on webhook).
        get_kora_client().register_charge(pending_ref, 5_000.0, "NGN")
    finally:
        monkeypatch.setattr(get_kora_client(), "is_mock", True)

    report = client.post(
        f"/api/v1/campaigns/{campaign['id']}/reconcile", headers=user["headers"]
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["clean"] is False
    assert any(m["reference"] == pending_ref for m in body["mismatches"])


def test_verify_payout_rejects_settled(client, user, campaign, beneficiary):
    from tests.test_milestones_payouts import contribute, create_milestone

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]
    client.post(f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"])

    denied = client.post(
        f"/api/v1/payouts/{payout['id']}/verify", headers=user["headers"]
    )
    assert denied.status_code == 409


def test_live_mode_requires_webhook_secret():
    from pydantic import ValidationError

    from app.core.config import Settings

    try:
        Settings(kora_mode="live", kora_webhook_secret="", kora_secret_key="")
    except ValidationError:
        return
    raise AssertionError("live mode without secrets should fail validation")


def test_public_activity_feed_masks_anonymous(client, user, campaign):
    from tests.test_milestones_payouts import contribute

    contribute(client, user, campaign, amount=50_000)
    client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 25_000, "anonymous": True},
        headers=user["headers"],
    )

    feed = client.get(f"/api/v1/campaigns/{campaign['id']}/activity").json()
    assert len(feed["contributions"]) == 2
    names = {c["contributor"]["name"] for c in feed["contributions"]}
    assert "Anonymous" in names
    assert len(feed["ledger"]) >= 2
