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
