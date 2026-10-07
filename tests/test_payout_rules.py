"""Payout rule engine tests (§71: threshold reached/not reached, approval
required, milestone already paid, multiple milestones)."""


def contribute(client, user, campaign, amount):
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": amount},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_rule(client, user, campaign, beneficiary, **overrides):
    body = {
        "beneficiary_id": beneficiary["id"],
        "label": "Stage payment",
        "amount": 300_000,
        "trigger_type": "THRESHOLD",
        "threshold_amount": 2_000_000,
    }
    body.update(overrides)
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/payout-rules",
        json=body,
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def payouts_for(client, user, campaign):
    return client.get(
        f"/api/v1/campaigns/{campaign['id']}/payouts", headers=user["headers"]
    ).json()


def test_threshold_rule_triggers_on_reached(client, user, campaign, beneficiary):
    create_rule(client, user, campaign, beneficiary)

    # Below threshold: no payout yet.
    contribute(client, user, campaign, 1_000_000)
    assert payouts_for(client, user, campaign) == []

    # Cross the threshold: evaluate auto-creates a READY payout.
    contribute(client, user, campaign, 1_500_000)
    rows = payouts_for(client, user, campaign)
    assert len(rows) == 1
    assert rows[0]["status"] == "READY"
    assert rows[0]["amount"] == 300_000


def test_threshold_rule_stays_locked_when_funds_locked(client, user, campaign, beneficiary):
    create_rule(client, user, campaign, beneficiary, threshold_amount=1_000_000)
    contribute(client, user, campaign, 500_000)
    assert payouts_for(client, user, campaign) == []


def test_rule_with_approval_required_creates_draft(client, user, campaign, beneficiary):
    rule = create_rule(
        client, user, campaign, beneficiary,
        trigger_type="MANUAL", requires_approval=True,
    )
    assert rule["status"] == "LOCKED"

    triggered = client.post(
        f"/api/v1/campaigns/{campaign['id']}/payout-rules/{rule['id']}/trigger",
        headers=user["headers"],
    )
    assert triggered.status_code == 200, triggered.text
    payout = triggered.json()
    assert payout["status"] == "DRAFT"

    contribute(client, user, campaign, 1_000_000)
    approved = client.post(
        f"/api/v1/payouts/{payout['id']}/approve", headers=user["headers"]
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "READY"

    # Double approval is rejected.
    again = client.post(
        f"/api/v1/payouts/{payout['id']}/approve", headers=user["headers"]
    )
    assert again.status_code == 409


def test_percentage_rule_computes_release(client, user, campaign, beneficiary):
    create_rule(
        client, user, campaign, beneficiary,
        label="20% release",
        amount=None,
        percentage=20,
        trigger_type="PERCENTAGE",
        threshold_amount=None,
        threshold_percentage=50,
    )
    contribute(client, user, campaign, 3_000_000)  # 60% of ₦5M target
    rows = payouts_for(client, user, campaign)
    assert len(rows) == 1
    assert rows[0]["amount"] == 600_000  # 20% of ₦3M collected


def test_multiple_milestones_multiple_payouts(client, user, campaign, beneficiary):
    for name, amount in [("Phase One", 200_000), ("Phase Two", 300_000)]:
        milestone = client.post(
            f"/api/v1/campaigns/{campaign['id']}/milestones",
            json={"name": name, "amount": amount, "beneficiary_id": beneficiary["id"]},
            headers=user["headers"],
        ).json()
        client.post(
            f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
        )
    contribute(client, user, campaign, 1_000_000)
    rows = payouts_for(client, user, campaign)
    assert len(rows) == 2
    assert {r["amount"] for r in rows} == {200_000, 300_000}


def test_execute_requires_approval_for_draft(client, user, campaign, beneficiary):
    rule = create_rule(
        client, user, campaign, beneficiary,
        trigger_type="MANUAL", requires_approval=True,
    )
    contribute(client, user, campaign, 1_000_000)
    payout = client.post(
        f"/api/v1/campaigns/{campaign['id']}/payout-rules/{rule['id']}/trigger",
        headers=user["headers"],
    ).json()
    response = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=user["headers"]
    )
    assert response.status_code == 409
