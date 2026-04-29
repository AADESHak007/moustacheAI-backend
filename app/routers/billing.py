"""
Billing Router — pricing catalogue, credit balance, and Razorpay flow.

Endpoints
---------
GET  /api/billing/plans               (public)         — list plans
GET  /api/billing/credits             (auth)           — current user balance
POST /api/billing/orders              (auth)           — create Razorpay order
POST /api/billing/orders/verify       (auth)           — confirm payment, add credits
POST /api/billing/webhook             (Razorpay → us)  — server-to-server confirmation

Flow
----
1.  Client picks a plan and calls `POST /orders` with `plan_id`.
2.  Server creates a Razorpay order, returns `order_id` + `key_id`.
3.  Client opens Razorpay Checkout (web JS / native SDK), which collects
    payment and returns `payment_id` + `signature`.
4.  Client calls `POST /orders/verify` to credit the account immediately.
5.  Razorpay also POSTs a webhook to `/billing/webhook`. Both paths are
    idempotent — whichever lands first credits the user.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import get_settings
from app.dependencies.auth import get_current_user
from app.models.auth import UserProfile
from app.models.billing import (
    CreateOrderRequest,
    CreateOrderResponse,
    CreditsResponse,
    PLANS,
    PLANS_BY_ID,
    PlansResponse,
    VerifyOrderRequest,
    VerifyOrderResponse,
)
from app.services.credits_service import CreditsService
from app.services.razorpay_service import (
    RazorpayConfigError,
    create_order,
    verify_checkout_signature,
    verify_webhook_signature,
)

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/billing", tags=["Billing"])


def _get_credits_service() -> CreditsService:
    return CreditsService()


# ---------------------------------------------------------------------------
# GET /billing/plans  (public)
# ---------------------------------------------------------------------------

@router.get(
    "/plans",
    response_model=PlansResponse,
    summary="List available plans",
    description="Returns the pricing catalogue. Public — safe to call before auth.",
)
def list_plans():
    return PlansResponse(plans=PLANS)


# ---------------------------------------------------------------------------
# GET /billing/credits  (auth)
# ---------------------------------------------------------------------------

@router.get(
    "/credits",
    response_model=CreditsResponse,
    summary="Get current credit balance",
)
def get_credits(
    current_user: UserProfile = Depends(get_current_user),
    svc:          CreditsService = Depends(_get_credits_service),
):
    return CreditsResponse(balance=svc.get_balance(current_user.id))


# ---------------------------------------------------------------------------
# POST /billing/orders  (auth)
# ---------------------------------------------------------------------------

@router.post(
    "/orders",
    response_model=CreateOrderResponse,
    summary="Create a Razorpay order for a plan",
)
def create_payment_order(
    body:         CreateOrderRequest,
    current_user: UserProfile = Depends(get_current_user),
):
    plan = PLANS_BY_ID.get(body.plan_id)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown plan.")
    if not plan.purchasable:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This plan cannot be purchased.")

    # Razorpay receipt must be ≤ 40 chars — uuid4 hex is 32, prefix takes 4
    receipt = f"mst_{uuid.uuid4().hex[:32]}"
    notes   = {
        "user_id": current_user.id,
        "email":   current_user.email,
        "plan_id": plan.id,
    }

    try:
        order = create_order(
            amount_paise=plan.amount_paise,
            receipt=receipt,
            notes=notes,
        )
    except RazorpayConfigError as exc:
        logger.error(f"[Billing] {exc}")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:
        logger.error(f"[Billing] Razorpay order create failed: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Payment gateway unavailable.")

    return CreateOrderResponse(
        order_id=order["id"],
        amount_paise=plan.amount_paise,
        currency="INR",
        key_id=settings.razorpay_key_id,
        plan=plan,
    )


# ---------------------------------------------------------------------------
# POST /billing/orders/verify  (auth)
# ---------------------------------------------------------------------------

@router.post(
    "/orders/verify",
    response_model=VerifyOrderResponse,
    summary="Confirm a Razorpay payment and credit the account",
)
def verify_payment(
    body:         VerifyOrderRequest,
    current_user: UserProfile = Depends(get_current_user),
    svc:          CreditsService = Depends(_get_credits_service),
):
    if not verify_checkout_signature(
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_signature=body.razorpay_signature,
    ):
        logger.warning(
            f"[Billing] Invalid signature for order {body.razorpay_order_id} "
            f"(user {current_user.id})"
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid payment signature.")

    plan = _resolve_plan_for_order(body.razorpay_order_id)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found at gateway.")

    credits_added, new_balance = svc.record_payment_and_credit(
        user_id=current_user.id,
        plan_id=plan.id,
        credits_to_add=plan.credits,
        amount_paise=plan.amount_paise,
        razorpay_order_id=body.razorpay_order_id,
        razorpay_payment_id=body.razorpay_payment_id,
    )

    return VerifyOrderResponse(
        success=True,
        credits_added=credits_added,
        credits_total=new_balance,
    )


# ---------------------------------------------------------------------------
# POST /billing/webhook  (Razorpay → us)
# ---------------------------------------------------------------------------

@router.post(
    "/webhook",
    summary="Razorpay webhook (payment.captured)",
    description=(
        "Server-to-server confirmation channel. Configure this URL in the "
        "Razorpay dashboard. Idempotent against /orders/verify."
    ),
)
async def razorpay_webhook(
    request: Request,
    svc:     CreditsService = Depends(_get_credits_service),
):
    raw = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")

    if not verify_webhook_signature(raw_body=raw, signature=sig):
        logger.warning("[Billing] Webhook signature rejected.")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Bad signature.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Malformed JSON.")

    event = payload.get("event")
    if event != "payment.captured":
        # We only act on captured payments; everything else is a no-op 200.
        logger.info(f"[Billing] Webhook ignored event={event}")
        return {"received": True, "handled": False}

    try:
        payment   = payload["payload"]["payment"]["entity"]
        order_id  = payment["order_id"]
        notes     = payment.get("notes") or {}
        user_id   = notes.get("user_id")
        plan_id   = notes.get("plan_id")
        plan      = PLANS_BY_ID.get(plan_id) if plan_id else None
    except KeyError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unexpected webhook shape.")

    if not user_id or not plan:
        logger.warning(f"[Billing] Webhook missing user/plan in notes: {notes}")
        return {"received": True, "handled": False}

    svc.record_payment_and_credit(
        user_id=user_id,
        plan_id=plan.id,
        credits_to_add=plan.credits,
        amount_paise=plan.amount_paise,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment.get("id", ""),
    )
    return {"received": True, "handled": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_plan_for_order(order_id: str):
    """
    Re-fetch the order from Razorpay to recover the plan_id from `notes`.
    This guards against a malicious client passing a tampered plan locally.
    """
    try:
        from app.services.razorpay_service import _client  # local import, lazy
        order = _client().order.fetch(order_id)
    except Exception as exc:
        logger.error(f"[Billing] Could not fetch order {order_id}: {exc}")
        return None

    plan_id = (order.get("notes") or {}).get("plan_id")
    return PLANS_BY_ID.get(plan_id) if plan_id else None
