"""
Pydantic models + plan catalogue for billing / credits.

The catalogue is intentionally tiny — a free starter plus three prepaid
credit packs in INR. Each generation costs exactly one credit.
"""

from typing import Literal, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Plan catalogue
# ---------------------------------------------------------------------------

class Plan(BaseModel):
    id:          str
    name:        str
    price_inr:   int           # whole rupees, used by the UI
    amount_paise: int          # what we send to Razorpay (price_inr * 100)
    credits:     int
    tagline:     str
    features:    list[str]
    purchasable: bool
    popular:     bool = False
    currency:    Literal["INR"] = "INR"


def _plan(id_: str, name: str, price_inr: int, credits: int, tagline: str,
          features: list[str], purchasable: bool, popular: bool = False) -> Plan:
    return Plan(
        id=id_,
        name=name,
        price_inr=price_inr,
        amount_paise=price_inr * 100,
        credits=credits,
        tagline=tagline,
        features=features,
        purchasable=purchasable,
        popular=popular,
    )


PLANS: list[Plan] = [
    _plan(
        "free", "Free", 0, 3,
        "Try it on us",
        ["3 AI generations", "All mustache styles", "HD downloads"],
        purchasable=False,
    ),
    _plan(
        "starter", "Starter", 99, 25,
        "For the curious",
        ["25 AI generations", "All styles", "HD downloads"],
        purchasable=True,
    ),
    _plan(
        "pro", "Pro", 299, 100,
        "Best value",
        ["100 AI generations", "All styles", "Priority queue"],
        purchasable=True,
        popular=True,
    ),
    _plan(
        "studio", "Studio", 799, 300,
        "For creators",
        ["300 AI generations", "All styles", "Priority queue"],
        purchasable=True,
    ),
]

PLANS_BY_ID: dict[str, Plan] = {p.id: p for p in PLANS}


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------

class PlansResponse(BaseModel):
    plans:    list[Plan]
    currency: Literal["INR"] = "INR"


class CreditsResponse(BaseModel):
    balance: int


class CreateOrderRequest(BaseModel):
    plan_id: str


class CreateOrderResponse(BaseModel):
    order_id:     str          # Razorpay order_id (rzp uses this in checkout)
    amount_paise: int
    currency:     Literal["INR"] = "INR"
    key_id:       str          # publishable Razorpay key for the client
    plan:         Plan


class VerifyOrderRequest(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str


class VerifyOrderResponse(BaseModel):
    success:        bool
    credits_added:  int
    credits_total:  int
