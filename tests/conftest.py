"""Shared pytest fixtures and HTTP helpers for the §71 test matrix."""

from __future__ import annotations

import os
import pathlib
import secrets

TEST_DB = pathlib.Path(__file__).resolve().parent / "test_fundflow.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["JWT_SECRET"] = "test-secret-for-fundflow"
os.environ["KORA_MODE"] = "mock"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["CORS_ORIGINS"] = "http://test"
# Fast hashing in tests only — production defaults to 390,000 iterations.
os.environ["PASSWORD_HASH_ITERATIONS"] = "1200"

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, get_engine
from app.integrations.kora import get_kora_client
from app.integrations.kora.client import reset_kora_client
from app.main import create_app


@pytest.fixture(scope="session")
def client():
    reset_kora_client()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    reset_kora_client()
    Base.metadata.drop_all(get_engine())


@pytest.fixture(scope="session")
def db():
    from app.core.database import get_session_factory

    session = get_session_factory()()
    yield session
    session.close()


@pytest.fixture()
def user(client: TestClient):
    """Registered user with auth headers. Unique email per test run."""
    email = f"user-{secrets.token_hex(4)}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Test User", "password": "password12345"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "user": data["user"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


@pytest.fixture()
def campaign(client: TestClient, user):
    """Active campaign with Kora collection account + resolved beneficiary."""
    payload = {
        "title": f"Test Campaign {secrets.token_hex(3)}",
        "short_description": "A test campaign",
        "description": "Full description for tests.",
        "category": "Charity",
        "visibility": "PUBLIC",
        "target_amount": 5_000_000,
    }
    response = client.post("/api/v1/campaigns", json=payload, headers=user["headers"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["account_error"] is None, body
    campaign = body["campaign"]
    assert campaign["status"] == "ACTIVE"
    return campaign


@pytest.fixture()
def beneficiary(client: TestClient, user, campaign):
    """Verified beneficiary (UBA 1234567890 -> JOHN OKAFOR)."""
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/beneficiaries",
        json={"name": "ABC Construction", "bank_code": "033",
              "account_number": "1234567890"},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def funded_campaign(client: TestClient, user, campaign):
    """Campaign with a ₦1,000,000 verified contribution."""
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/contribute",
        json={"amount": 1_000_000, "anonymous": False},
        headers=user["headers"],
    )
    assert response.status_code == 201, response.text
    return campaign


def kora_mock():
    return get_kora_client()._mock


def signed(payload: dict) -> str:
    from app.integrations.kora.webhooks import compute_signature

    return compute_signature(payload["data"])


def make_charge_payload(contribution: dict, amount: float | None = None) -> dict:
    from app.integrations.kora.webhooks import build_charge_event

    return build_charge_event(
        "charge.success",
        reference=contribution["kora_reference"],
        amount=amount if amount is not None else contribution["amount"],
        currency=contribution["currency"],
    )


def post_webhook(client: TestClient, payload: dict, signature: str | None = None):
    import json

    return client.post(
        "/api/v1/webhooks/kora",
        content=json.dumps(payload),
        headers={"x-korapay-signature": signature or signed(payload)},
    )

