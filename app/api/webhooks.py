"""Webhook receiver (PRODUCT.md §21-22).

POST /api/v1/webhooks/kora — public endpoint; authenticity comes from the
HMAC-SHA256 signature, never from transport auth.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.database import get_session_factory
from app.services.webhook_service import WebhookSignatureError, handle_kora_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/kora")
async def kora_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("x-korapay-signature")

    session = get_session_factory()()
    try:
        try:
            result = handle_kora_webhook(raw, signature, db=session)
        except WebhookSignatureError as exc:
            # Rule 2: unsigned/invalid webhooks are rejected outright.
            raise HTTPException(status_code=401, detail=str(exc))
        # Kora expects a fast 200; duplicates ack the same way (§21).
        return Response(
            content='{"status": "%s"}' % result.get("status", "ok"),
            media_type="application/json",
            status_code=200,
        )
    finally:
        session.close()
