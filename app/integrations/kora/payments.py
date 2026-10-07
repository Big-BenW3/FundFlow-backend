"""Live Kora pay-in verification (Charge Query API).

Docs: https://developers.korapay.com/docs/virtual-bank-accounts-ngn
* GET /charges/:reference — verify a payment after receiving its webhook.

Kora explicitly recommends verifying a payment after receiving a
virtual-account webhook (PRODUCT.md §20).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.kora.client import KoraClient


def live_verify_transaction(client: "KoraClient", reference: str) -> dict:
    """Return the canonical transaction state straight from Kora.

    Shape (docs):
    {
      "status": true,
      "data": {
        "reference": "...", "status": "success", "amount": "100.00",
        "amount_paid": "100.00", "fee": 1.5, "currency": "NGN", ...
      }
    }
    """
    body = client._request("GET", f"/charges/{reference}")
    return body.get("data") or {}
