"""
Credits service — tracks per-user credit balances and payment ledger.

Persistence model
-----------------
Two Supabase tables (recommended SQL in `migrations/billing.sql`):

    user_credits ( user_id uuid pk, balance int, updated_at timestamptz )
    payments     ( id uuid pk, user_id uuid, plan_id text,
                   amount_paise int, currency text,
                   razorpay_order_id text unique, razorpay_payment_id text,
                   status text, credits_added int, created_at timestamptz )

If the Supabase client is unavailable or the tables do not exist, the
service silently degrades to an in-process dict so local dev still works.
The in-memory store is shared across threads in a single worker but does
not survive a restart — exactly the same trade-off as `_jobs_db` in
`app/routers/jobs.py`.

Idempotency
-----------
`record_payment_and_credit` is keyed on `razorpay_order_id`. Repeated
calls (e.g. webhook + client-verify firing for the same order) are safe:
the second call is a no-op and returns the existing balance.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from supabase import Client, create_client

from app.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


def _service_client() -> Client | None:
    try:
        return create_client(settings.supabase_url, settings.supabase_service_key)
    except Exception as exc:
        logger.warning(f"[Credits] Supabase service client unavailable: {exc}")
        return None


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

_memory_lock              = threading.Lock()
_memory_balances:         dict[str, int]  = {}
_memory_processed_orders: dict[str, dict] = {}   # order_id -> {credits, balance}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CreditsService:
    """
    Read/write the user_credits table with an in-memory fallback.

    All methods are sync — Supabase's Python SDK is sync — but they are safe
    to call from FastAPI async handlers (the SDK uses httpx under the hood).
    """

    def __init__(self) -> None:
        self.db: Client | None = _service_client()

    # ------------------------------------------------------------------
    # Balance read / write
    # ------------------------------------------------------------------

    def get_balance(self, user_id: str) -> int:
        """
        Return the user's current balance. Seeds `free_signup_credits` for
        first-time callers so new accounts can try the product immediately.
        """
        if self.db:
            try:
                resp = (
                    self.db.table("user_credits")
                    .select("balance")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    return int(resp.data[0]["balance"])

                # Seed free credits for this user
                seeded = settings.free_signup_credits
                self.db.table("user_credits").insert({
                    "user_id":    user_id,
                    "balance":    seeded,
                    "updated_at": _now_iso(),
                }).execute()
                return seeded
            except Exception as exc:
                logger.warning(f"[Credits] DB read failed, falling back to memory: {exc}")

        with _memory_lock:
            if user_id not in _memory_balances:
                _memory_balances[user_id] = settings.free_signup_credits
            return _memory_balances[user_id]

    def _set_balance(self, user_id: str, new_balance: int) -> int:
        if self.db:
            try:
                self.db.table("user_credits").upsert({
                    "user_id":    user_id,
                    "balance":    new_balance,
                    "updated_at": _now_iso(),
                }, on_conflict="user_id").execute()
                return new_balance
            except Exception as exc:
                logger.warning(f"[Credits] DB write failed, falling back to memory: {exc}")

        with _memory_lock:
            _memory_balances[user_id] = new_balance
            return new_balance

    # ------------------------------------------------------------------
    # Spend / refund
    # ------------------------------------------------------------------

    def try_spend(self, user_id: str, amount: int = 1) -> bool:
        """
        Atomically deduct `amount` credits. Returns False if balance is too low.
        """
        current = self.get_balance(user_id)
        if current < amount:
            return False
        self._set_balance(user_id, current - amount)
        logger.info(f"[Credits] {user_id} spent {amount} → balance {current - amount}")
        return True

    def refund(self, user_id: str, amount: int = 1) -> int:
        """Return `amount` credits to the user (e.g. AI pipeline failed)."""
        current = self.get_balance(user_id)
        new_balance = current + amount
        self._set_balance(user_id, new_balance)
        logger.info(f"[Credits] {user_id} refunded {amount} → balance {new_balance}")
        return new_balance

    # ------------------------------------------------------------------
    # Add credits + record payment (idempotent on order_id)
    # ------------------------------------------------------------------

    def record_payment_and_credit(
        self,
        *,
        user_id:             str,
        plan_id:             str,
        credits_to_add:      int,
        amount_paise:        int,
        razorpay_order_id:   str,
        razorpay_payment_id: str,
    ) -> tuple[int, int]:
        """
        Idempotently credit a user for a successful Razorpay payment.

        Returns (credits_added_this_call, new_balance). If the order has
        already been processed, returns (0, current_balance).
        """
        # --- Idempotency check ------------------------------------------------
        if self.db:
            try:
                existing = (
                    self.db.table("payments")
                    .select("id")
                    .eq("razorpay_order_id", razorpay_order_id)
                    .eq("status", "paid")
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    logger.info(f"[Credits] Order {razorpay_order_id} already processed.")
                    return 0, self.get_balance(user_id)
            except Exception as exc:
                logger.warning(f"[Credits] Idempotency lookup failed (will continue): {exc}")
        else:
            with _memory_lock:
                if razorpay_order_id in _memory_processed_orders:
                    return 0, self.get_balance(user_id)

        # --- Add credits ------------------------------------------------------
        new_balance = self.get_balance(user_id) + credits_to_add
        self._set_balance(user_id, new_balance)

        # --- Persist payment row ---------------------------------------------
        payment_row = {
            "id":                  str(uuid.uuid4()),
            "user_id":             user_id,
            "plan_id":             plan_id,
            "amount_paise":        amount_paise,
            "currency":            "INR",
            "razorpay_order_id":   razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "status":              "paid",
            "credits_added":       credits_to_add,
            "created_at":          _now_iso(),
        }
        if self.db:
            try:
                self.db.table("payments").insert(payment_row).execute()
            except Exception as exc:
                logger.warning(f"[Credits] payments insert failed (credits already applied): {exc}")
        else:
            with _memory_lock:
                _memory_processed_orders[razorpay_order_id] = {
                    "credits": credits_to_add, "balance": new_balance,
                }

        logger.info(
            f"[Credits] {user_id} +{credits_to_add} (plan={plan_id}, "
            f"order={razorpay_order_id}) → balance {new_balance}"
        )
        return credits_to_add, new_balance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
