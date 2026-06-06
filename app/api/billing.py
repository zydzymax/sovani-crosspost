"""
Billing API for subscription management.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.entities import User
from ..services.payment_service import TochkaPaymentService
from .deps import get_current_user, get_db_async_session

logger = get_logger("api.billing")
router = APIRouter(prefix="/billing", tags=["billing"])


class SubscribeRequest(BaseModel):
    product: str
    plan: str
    price: int


class SubscribeResponse(BaseModel):
    success: bool
    payment_url: str | None = None
    error: str | None = None


PRODUCTS = {
    "saleswhisper": {
        "name": "SalesWhisper",
        "plans": {
            "sw_starter": {"name": "Starter", "price": 3990},
            "sw_business": {"name": "Business", "price": 9990},
            "sw_enterprise": {"name": "Enterprise", "price": 29990},
        },
    },
    "crosspost": {
        "name": "Crosspost",
        "plans": {
            "cp_free": {"name": "Free", "price": 0},
            "cp_pro": {"name": "Pro", "price": 1490},
            "cp_agency": {"name": "Agency", "price": 4990},
        },
    },
    "headofsales": {
        "name": "Head of Sales",
        "plans": {
            "hos_starter": {"name": "Starter", "price": 4990},
            "hos_business": {"name": "Business", "price": 14990},
            "hos_enterprise": {"name": "Enterprise", "price": 49990},
        },
    },
    "sites": {
        "name": "Sites",
        "plans": {
            "sites_basic": {"name": "Basic", "price": 990},
            "sites_pro": {"name": "Pro", "price": 2990},
            "sites_agency": {"name": "Agency", "price": 9990},
        },
    },
}


def _get_product_and_plan(product_code: str, plan_code: str) -> tuple[dict, dict]:
    """Resolve product and plan metadata or raise HTTP 400."""
    product = PRODUCTS.get(product_code)
    if product is None:
        raise HTTPException(status_code=400, detail=f"Unknown product: {product_code}")

    plan = product["plans"].get(plan_code)
    if plan is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_code}")
    return product, plan


def _build_order_id(user_id: object, product_code: str, plan_code: str) -> str:
    """Create stable order id for payment provider."""
    return f"{user_id}_{product_code}_{plan_code}"


@router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(
    request: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    _db: AsyncSession = Depends(get_db_async_session),
):
    """Create subscription payment."""
    product, plan = _get_product_and_plan(request.product, request.plan)

    if request.price != plan["price"]:
        raise HTTPException(status_code=400, detail="Price mismatch")

    if plan["price"] == 0:
        return SubscribeResponse(success=True)

    try:
        payment_service = TochkaPaymentService()
        order_id = _build_order_id(current_user.id, request.product, request.plan)
        description = f"{product['name']} - {plan['name']} (1 месяц)"

        result = await payment_service.create_payment(
            order_id=order_id,
            amount_rub=Decimal(plan["price"]),
            description=description,
            customer_email=current_user.email,
        )

        if result.success and result.payment_url:
            return SubscribeResponse(success=True, payment_url=result.payment_url)
        return SubscribeResponse(success=False, error=result.error or "Payment creation failed")

    except Exception:
        logger.exception("Payment error")
        return SubscribeResponse(success=False, error="Ошибка создания платежа")


@router.get("/subscriptions")
async def get_subscriptions(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_async_session)
):
    """Get user's active subscriptions."""
    return {"subscriptions": [{"product": "crosspost", "plan": "cp_free", "status": "active", "expires_at": None}]}
