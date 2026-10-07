"""Auth (§48) + campaign tests (§71: create, update, private/public, deadline)."""


def test_register_login_me(client):
    email = "ada@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Ada", "password": "password12345", "phone": "08000000000"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "password12345"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == email


def test_register_duplicate_email(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "name": "A", "password": "password12345"},
    )
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "name": "B", "password": "password12345"},
    )
    assert response.status_code == 409


def test_login_wrong_password_rejected(client, user):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": user["user"]["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_invalid_jwt_rejected(client):
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_missing_jwt_rejected(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_create_campaign_provisions_collection_account(client, user):
    response = client.post(
        "/api/v1/campaigns",
        json={
            "title": "Water Project",
            "short_description": "Water for all",
            "category": "Charity",
            "visibility": "PUBLIC",
            "target_amount": 2_000_000,
        },
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["account_error"] is None
    campaign = body["campaign"]
    assert campaign["status"] == "ACTIVE"
    assert campaign["account"]["account_number"]
    assert campaign["account"]["account_reference"]


def test_update_campaign_by_owner(client, user, campaign):
    response = client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        json={"title": "Renamed Campaign"},
        headers=user["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed Campaign"


def test_update_campaign_by_outsider_forbidden(client, user, campaign):
    other = client.post(
        "/api/v1/auth/register",
        json={"email": "outsider@example.com", "name": "Out", "password": "password12345"},
    ).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    response = client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        json={"title": "Hijacked"},
        headers=headers,
    )
    assert response.status_code == 403


def test_private_campaign_hidden_from_outsiders(client, user):
    created = client.post(
        "/api/v1/campaigns",
        json={
            "title": "Private Family Fund",
            "short_description": "Private",
            "visibility": "PRIVATE",
            "target_amount": 500_000,
        },
        headers=user["headers"],
    ).json()["campaign"]

    # Public list must not expose it.
    listing = client.get("/api/v1/campaigns").json()
    assert all(c["id"] != created["id"] for c in listing)

    # Outsider direct access: 404 (existence not leaked, §29).
    assert client.get(f"/api/v1/campaigns/{created['id']}").status_code == 404

    # Owner can still see it.
    own = client.get(
        f"/api/v1/campaigns/{created['id']}", headers=user["headers"]
    )
    assert own.status_code == 200


def test_unlisted_campaign_link_only(client, user):
    created = client.post(
        "/api/v1/campaigns",
        json={
            "title": "Unlisted Fund",
            "short_description": "Unlisted",
            "visibility": "UNLISTED",
            "target_amount": 500_000,
        },
        headers=user["headers"],
    ).json()["campaign"]
    listing = client.get("/api/v1/campaigns").json()
    assert all(c["id"] != created["id"] for c in listing)
    assert client.get(f"/api/v1/campaigns/{created['id']}").status_code == 200


def test_deadline_blocks_contributions(client, user, campaign):
    client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        json={"deadline": "2020-01-01T00:00:00"},
        headers=user["headers"],
    )
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 5_000},
        headers=user["headers"],
    )
    assert response.status_code == 409
