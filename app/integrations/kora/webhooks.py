"""Kora webhook signatures and mock webhook delivery (PRODUCT.md §21).

Kora signs the webhook's ``data`` object with HMAC-SHA256 using the secret
key and sends it in the ``x-korapay-signature`` header
(https://developers.korapay.com/docs/webhooks).

Events handled by FundFlow:
* charge.success / charge.failed   → contributions (pay-ins)
* transfer.success / transfer.failed → payouts
* refund.success / refund.failed   → refunds
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings

if TYPE_CHECKING:
    pass

SIGNATURE_HEADER = "x-korapay-signature"


def _data_body(data: dict) -> bytes:
    """Serialize the ``data`` object the way Kora signs it (compact JSON)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_signature(data: dict, secret: str | None = None) -> str:
    secret = secret or get_settings().webhook_secret
    return hmac.new(secret.encode("utf-8"), _data_body(data), hashlib.sha256).hexdigest()


def verify_signature(data: dict, signature: str | None, secret: str | None = None) -> bool:
    if not signature:
        return False
    expected = compute_signature(data, secret)
    return hmac.compare_digest(expected, signature)


# ------------------------------------------------------------- payload build
def build_charge_event(
    event: str,
    *,
    reference: str,
    amount: float,
    currency: str = "NGN",
    fee: float = 0.0,
    vba: dict | None = None,
    payer: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "event": event,
        "data": {
            "reference": reference,
            "payment_reference": reference,
            "amount": round(float(amount), 2),
            "fee": round(float(fee), 2),
            "currency": currency,
            "status": "success" if event.endswith("success") else "failed",
            "transaction_date": _now_iso(),
        },
    }
    if vba or payer:
        payload["data"]["virtual_bank_account_details"] = {
            "virtual_bank_account": vba or {},
            "payer_bank_account": payer or {},
        }
    return payload


def build_transfer_event(
    event: str,
    *,
    reference: str,
    amount: float,
    currency: str = "NGN",
    fee: float = 0.0,
) -> dict:
    return {
        "event": event,
        "data": {
            "reference": reference,
            "amount": round(float(amount), 2),
            "fee": round(float(fee), 2),
            "currency": currency,
            "status": "success" if event.endswith("success") else "failed",
        },
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------- mock delivery
def deliver_mock_webhook(background_tasks, payload: dict) -> None:
    """Schedule an in-process webhook delivery OR deliver synchronously.

    The pipeline dedupes replays (unique webhook constraint + status
    guards), so delivering twice is always safe — this is also how the
    pipeline survives Kora's own 72h retry window (§21).
    """
    _deliver_now(payload)
    if background_tasks is not None:
        background_tasks.add_task(_deliver_now, payload)


def _deliver_now(payload: dict) -> None:
    from app.services.webhook_service import handle_kora_webhook  # deferred

    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = compute_signature(payload["data"])
    handle_kora_webhook(raw, signature)
