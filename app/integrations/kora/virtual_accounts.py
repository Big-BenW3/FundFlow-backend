"""Live Kora Virtual Bank Account endpoints.

Docs: https://developers.korapay.com/docs/virtual-bank-accounts-ngn
* POST   /virtual-bank-account                 — create fixed VBA
* GET    /virtual-bank-account/:accountReference — query VBA
* GET    /virtual-bank-account/transactions      — VBA transaction history
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.kora.client import KoraClient


def live_create_virtual_account(
    client: "KoraClient",
    *,
    account_name: str,
    account_reference: str,
    customer_name: str,
    customer_email: str | None = None,
    bank_code: str = "000",
    bvn: str | None = None,
) -> dict:
    """Create an NGN fixed virtual account.

    ``bank_code="000"`` creates a sandbox account (Kora docs). NGN accounts
    require a ``kyc.bvn`` in live mode — pass one via settings when live keys
    are configured; KYC/compliance requirements are documented in PRODUCT.md §19.
    """
    payload: dict = {
        "account_name": account_name,
        "account_reference": account_reference,
        "permanent": True,
        "bank_code": bank_code,
        "customer": {"name": customer_name},
    }
    if customer_email:
        payload["customer"]["email"] = customer_email
    if bvn:
        payload["kyc"] = {"bvn": bvn}

    body = client._request("POST", "/virtual-bank-account", json=payload)
    return body.get("data") or {}


def live_get_virtual_account(client: "KoraClient", account_reference: str) -> dict:
    body = client._request("GET", f"/virtual-bank-account/{account_reference}")
    return body.get("data") or {}


def live_get_virtual_account_transactions(
    client: "KoraClient", account_number: str, page: int = 1, limit: int = 100
) -> dict:
    body = client._request(
        "GET",
        "/virtual-bank-account/transactions",
        params={
            "account_number": account_number,
            "page": page,
            "limit": limit,
        },
    )
    return body.get("data") or {}
