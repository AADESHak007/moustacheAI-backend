"""
Thin wrapper around the Razorpay Python SDK.

Responsibilities
----------------
- Build a Razorpay client lazily (so the server still boots without keys).
- Create orders for a given plan.
- Verify the HMAC signature returned by the client checkout.
- Verify webhook signatures.

Razorpay docs: https://razorpay.com/docs/api/orders/
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from functools import lru_cache
from typing import Any

import razorpay

from app.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


class RazorpayConfigError(RuntimeError):
    """Raised when Razorpay credentials are missing."""


@lru_cache(maxsize=1)
def _client() -> razorpay.Client:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise RazorpayConfigError(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET in your .env file."
        )
    client = razorpay.Client(
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
    )
    # Optional: set a UA so we can spot our traffic in Razorpay logs
    client.set_app_details({"title": settings.app_name, "version": settings.app_version})
    return client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_order(
    *,
    amount_paise: int,
    receipt:      str,
    notes:        dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Create a Razorpay order. Returns the SDK payload — the only fields the
    client really cares about are `id` and `amount`.
    """
    payload = {
        "amount":          amount_paise,
        "currency":        "INR",
        "receipt":         receipt,
        "payment_capture": 1,             # auto-capture on successful auth
        "notes":           notes or {},
    }
    order = _client().order.create(data=payload)
    logger.info(f"[Razorpay] Order created: {order.get('id')} ({amount_paise} paise)")
    return order


def verify_checkout_signature(
    *,
    razorpay_order_id:   str,
    razorpay_payment_id: str,
    razorpay_signature:  str,
) -> bool:
    """
    Verify the HMAC signature returned by the Razorpay JS / native checkout.

    Per the docs, the expected signature is:
        HMAC_SHA256(key_secret, order_id + "|" + payment_id)
    """
    if not settings.razorpay_key_secret:
        logger.error("[Razorpay] verify_checkout_signature called without key_secret")
        return False

    expected = hmac.new(
        key=settings.razorpay_key_secret.encode("utf-8"),
        msg=f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, razorpay_signature)


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    """
    Verify the X-Razorpay-Signature header on inbound webhooks.

        HMAC_SHA256(webhook_secret, raw_body)
    """
    if not settings.razorpay_webhook_secret:
        logger.error("[Razorpay] verify_webhook_signature called without webhook_secret")
        return False

    expected = hmac.new(
        key=settings.razorpay_webhook_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
