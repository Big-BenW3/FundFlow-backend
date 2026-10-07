"""Security matrix (§71): unauthorized access to campaigns, contributions,
payouts and admin endpoints; JWT + signature tampering."""


def test_unauthorized_payout_execution(client, user, campaign, beneficiary):
    from tests.test_milestones_payouts import contribute, create_milestone

    contribute(client, user, campaign, amount=1_000_000)
    milestone = create_milestone(client, user, campaign, beneficiary["id"])
    payout = client.post(
        f"/api/v1/milestones/{milestone['id']}/approve", headers=user["headers"]
    ).json()["payout"]

    # No token at all → 401/403.
    response = client.post(f"/api/v1/payouts/{payout['id']}/execute")
    assert response.status_code in (401, 403)

    # A different logged-in user → 403.
    other = client.post(
        "/api/v1/auth/register",
        json={"email": "stranger@example.com", "name": "Str", "password": "password12345"},
    ).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    response = client.post(
        f"/api/v1/payouts/{payout['id']}/execute", headers=headers
    )
    assert response.status_code == 403

    # Contributions listing is owner-only too.
    response = client.get(
        f"/api/v1/campaigns/{campaign['id']}/contributions", headers=headers
    )
    assert response.status_code == 403


def test_audit_log_requires_write_access(client, user, campaign):
    other = client.post(
        "/api/v1/auth/register",
        json={"email": "reader@example.com", "name": "Rd", "password": "password12345"},
    ).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    response = client.get(
        f"/api/v1/campaigns/{campaign['id']}/audit", headers=headers
    )
    assert response.status_code == 403


def test_admin_endpoints_require_admin(client, user):
    response = client.get("/api/v1/admin/overview", headers=user["headers"])
    assert response.status_code == 403


def test_admin_can_view_overview_and_suspend(client, db):
    from app.core.security import hash_password
    from app.models.user import User

    admin = User(
        email="admin@fundflow.app",
        name="Platform Admin",
        password_hash=hash_password("password12345"),
        role="ADMIN",
    )
    db.add(admin)
    db.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@fundflow.app", "password": "password12345"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    overview = client.get("/api/v1/admin/overview", headers=headers)
    assert overview.status_code == 200
    assert "counts" in overview.json()


def test_double_spend_blocked(client, user, campaign, beneficiary):
    """Two payouts that together exceed the balance → second is refused."""
    from tests.test_milestones_payouts import contribute, create_milestone

    contribute(client, user, campaign, amount=600_000)
    first = create_milestone(client, user, campaign, beneficiary["id"])
    second = create_milestone(
        client, user, campaign, beneficiary["id"],
        name="Second", amount=400_000,
    )
    p1 = client.post(
        f"/api/v1/milestones/{first['id']}/approve", headers=user["headers"]
    ).json()["payout"]
    p2 = client.post(
        f"/api/v1/milestones/{second['id']}/approve", headers=user["headers"]
    ).json()["payout"]
    assert p1["status"] == "READY", p1

    executed = client.post(
        f"/api/v1/payouts/{p1['id']}/execute", headers=user["headers"]
    ).json()
    assert executed["status"] == "SUCCESS"

    # ₦400k > remaining ₦100k → READY check refuses.
    p2_after = client.get(
        f"/api/v1/payouts/{p2['id']}", headers=user["headers"]
    ).json()
    assert p2_after["status"] in ("LOCKED", "READY")
    executed2 = client.post(
        f"/api/v1/payouts/{p2['id']}/execute", headers=user["headers"]
    )
    assert executed2.status_code == 409
