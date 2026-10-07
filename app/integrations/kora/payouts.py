"""Live Kora payout endpoints.

Docs: https://developers.korapay.com/docs/payout-via-api
* POST /transactions/disburse — initiate a payout
* GET  /transactions/:reference — verify/query a payout status
  (Kora's "Transaction Query" step — resolve UNKNOWN outcomes after 5xx)
* GET  /balances — merchant balance
* GET  /payouts — payout history
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.kora.client import KoraClient


def live_create_payout(
    client: "KoraClient",
    *,
    reference: str,
    amount: float,
    currency: str,
    bank_code: str,
    account_number: str,
    narration: str,
    customer_name: str | None = None,
    customer_email: str = "payout@fundflow.app",
) -> dict:
    """Initiate a bank-transfer payout from the merchant balance.

    On 5xx/timeouts KoraClient raises ``KoraAPIError(retryable=True)`` — the
    caller must verify the payout instead of treating it as failed (§26).
    """
    payload = {
        "reference": reference,  # must be >= 5 characters (Kora requirement)
        "destination": {
            "type": "bank_account",
            "amount": round(float(amount), 2),
            "currency": currency,
            "narration": narration,
            "bank_account": {
                "bank": bank_code,
                "account": account_number,
            },
            "customer": {
                "name": customer_name or account_number,
                "email": customer_email,
            },
        },
    }
    body = client._request("POST", "/transactions/disburse", json=payload)
    return body.get("data") or {}


def live_verify_payout(client: "KoraClient", reference: str) -> dict:
    """Query the payout transaction to confirm its final state."""
    body = client._request("GET", f"/transactions/{reference}")
    return body.get("data") or {}


def live_get_balance(client: "KoraClient", currency: str = "NGN") -> dict:
    body = client._request("GET", "/balances")
    data = body.get("data") or {}
    if currency:
        return {currency: data.get(currency, {})}
    return data


def live_get_payout_history(
    client: "KoraClient", currency: str | None = "NGN", limit: int = 20
) -> list:
    params: dict = {"limit": limit}
    if currency:
        params["currency"] = currency
    body = client._request("GET", "/payouts", params=params)
    data = body.get("data")
    if isinstance(data, dict):
        return data.get("transactions") or data.get("data") or []
    return data or []
