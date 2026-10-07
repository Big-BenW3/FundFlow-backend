"""Live Kora bank verification utilities.

Docs:
* https://developers.korapay.com/docs/payout-via-api (bank resolve)
* https://developers.korapay.com/docs/virtual-bank-accounts-ngn (VBA bank codes)
* POST /misc/banks/resolve — resolve beneficiary accounts (NGN/KES)
* GET  /misc/banks?countryCode=NG — bank list
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.kora.client import KoraClient


def live_resolve_bank_account(
    client: "KoraClient", bank_code: str, account_number: str, currency: str = "NGN"
) -> dict:
    """Resolve a bank account to its account name before payout (§14).

    Response shape (docs):
    {
      "status": true,
      "data": {
        "bank_name": "United Bank for Africa",
        "bank_code": "033",
        "account_number": "2158634852",
        "account_name": "EBUKA CIROMOLA OLADEMJI"
      }
    }
    """
    body = client._request(
        "POST",
        "/misc/banks/resolve",
        json={"bank": bank_code, "account": account_number, "currency": currency},
    )
    return body.get("data") or {}


def live_list_banks(client: "KoraClient", country_code: str = "NG") -> list[dict]:
    body = client._request(
        "GET", "/misc/banks", params={"countryCode": country_code}
    )
    data = body.get("data")
    if isinstance(data, dict):
        return data.get("banks") or []
    return data or []
