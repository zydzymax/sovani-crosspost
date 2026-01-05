"""
Checkout API for SalesWhisper unified payments.
Handles order creation and payment processing via Tochka Bank.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from ..models.entities import (
    Cart,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PromoCode,
    SaaSProduct,
    SaaSProductPlan,
    User,
    UserSubscription,
)
from ..services.email_service import get_email_service
from ..services.payment_service import get_payment_service
from .deps import get_current_user, get_db_async_session

logger = get_logger("api.checkout")

router = APIRouter(prefix="/checkout", tags=["Checkout"])


# ==================== SCHEMAS ====================


class CreateOrderRequest(BaseModel):
    """Request to create order from cart."""

    payment_method: str = Field(default="card", description="Payment method: card or sbp")
    return_url: str | None = Field(None, description="Custom return URL after payment")


class OrderItemResponse(BaseModel):
    """Order item in response."""

    product_code: str
    product_name: str
    plan_code: str
    plan_name: str
    price_rub: float
    billing_period: str


class OrderResponse(BaseModel):
    """Order response."""

    id: str
    order_number: str
    status: str
    items: list[OrderItemResponse]
    subtotal_rub: float
    discount_rub: float
    promo_code: str | None
    total_rub: float
    payment_url: str | None
    created_at: datetime


class CreateOrderResponse(BaseModel):
    """Response after creating order."""

    success: bool
    order: OrderResponse
    payment_url: str | None = None
    message: str


class WebhookResponse(BaseModel):
    """Webhook processing response."""

    success: bool
    message: str


# ==================== HELPERS ====================


def generate_order_number() -> str:
    """Generate unique order number."""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_suffix = uuid.uuid4().hex[:6].upper()
    return f"SW-{timestamp}-{random_suffix}"


async def activate_subscription(db: AsyncSession, user: User, order: Order):
    """
    Activate subscriptions from paid order.

    Args:
        db: Database session
        user: User who made the order
        order: Paid order
    """
    from dateutil.relativedelta import relativedelta

    for item in order.items:
        product_code = item.get("product_code")
        plan_code = item.get("plan_code")
        billing_period = item.get("billing_period", "monthly")

        # Find product and plan
        result = await db.execute(select(SaaSProduct).where(SaaSProduct.code == product_code))
        product = result.scalar_one_or_none()

        if not product:
            logger.error(f"Product not found for activation: {product_code}")
            continue

        result = await db.execute(
            select(SaaSProductPlan).where(SaaSProductPlan.product_id == product.id, SaaSProductPlan.code == plan_code)
        )
        plan = result.scalar_one_or_none()

        if not plan:
            logger.error(f"Plan not found for activation: {product_code}/{plan_code}")
            continue

        # Calculate expiration
        now = datetime.utcnow()
        if billing_period == "yearly":
            expires_at = now + relativedelta(years=1)
        else:
            expires_at = now + relativedelta(months=1)

        # Check if subscription exists
        result = await db.execute(
            select(UserSubscription).where(
                UserSubscription.user_id == user.id, UserSubscription.product_id == product.id
            )
        )
        subscription = result.scalar_one_or_none()

        if subscription:
            # Update existing subscription
            subscription.plan_id = plan.id
            subscription.status = "active"
            # Extend if already active
            if subscription.expires_at and subscription.expires_at > now:
                if billing_period == "yearly":
                    subscription.expires_at = subscription.expires_at + relativedelta(years=1)
                else:
                    subscription.expires_at = subscription.expires_at + relativedelta(months=1)
            else:
                subscription.expires_at = expires_at
            subscription.updated_at = now
            logger.info(f"Updated subscription for user {user.id}: {product_code}/{plan_code}")
        else:
            # Create new subscription
            subscription = UserSubscription(
                user_id=user.id,
                product_id=product.id,
                plan_id=plan.id,
                status="active",
                started_at=now,
                expires_at=expires_at,
            )
            db.add(subscription)
            logger.info(f"Created subscription for user {user.id}: {product_code}/{plan_code}")

    await db.commit()


async def send_order_confirmation_email(user: User, order: Order):
    """Send order confirmation email."""
    try:
        email_service = get_email_service()
        await email_service.send_order_confirmation(
            to_email=user.email or order.customer_email,
            order_number=order.order_number,
            items=[
                {"name": f"{item.get('product_name')} - {item.get('plan_name')}", "price": item.get("price_rub")}
                for item in order.items
            ],
            total_rub=float(order.total_rub),
        )
    except Exception as e:
        logger.error(f"Failed to send order confirmation email: {e}")


# ==================== ROUTES ====================


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_order(
    request: CreateOrderRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_async_session),
    user: User = Depends(get_current_user),
):
    """
    Create order from cart and initiate payment.

    Returns order info and payment URL for redirect.
    """
    # Get user's cart
    result = await db.execute(select(Cart).where(Cart.user_id == user.id))
    cart = result.scalar_one_or_none()

    if not cart or not cart.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Корзина пуста")

    # Calculate totals
    subtotal = Decimal("0")
    for item in cart.items:
        subtotal += Decimal(str(item.get("price_rub", 0)))

    discount = Decimal("0")
    promo_code = cart.promo_code

    if promo_code:
        result = await db.execute(select(PromoCode).where(PromoCode.code == promo_code, PromoCode.is_active))
        promo = result.scalar_one_or_none()

        if promo and promo.is_valid:
            if promo.discount_percent:
                discount = subtotal * (Decimal(str(promo.discount_percent)) / 100)
            elif promo.discount_amount:
                discount = min(promo.discount_amount, subtotal)

    total = subtotal - discount

    # Generate order number
    order_number = generate_order_number()

    # Create order
    order = Order(
        user_id=user.id,
        order_number=order_number,
        status=OrderStatus.PENDING,
        items=cart.items,
        subtotal_rub=subtotal,
        discount_rub=discount,
        promo_code=promo_code,
        total_rub=total,
        customer_email=user.email or "",
        customer_phone=user.phone,
        payment_method=request.payment_method,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    # Create payment
    payment_service = get_payment_service()
    payment_result = await payment_service.create_payment(
        order_id=str(order.id),
        amount_rub=total,
        description=f"SalesWhisper - Заказ {order_number}",
        customer_email=user.email or order.customer_email,
        customer_phone=user.phone,
        return_url=request.return_url,
    )

    if not payment_result.success:
        # Update order status to failed
        order.status = OrderStatus.FAILED
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка создания платежа: {payment_result.error}"
        )

    # Create payment record
    payment = Payment(
        order_id=order.id,
        amount_rub=total,
        status=PaymentStatus.PENDING,
        provider="tochka",
        provider_payment_id=payment_result.payment_id,
    )
    db.add(payment)

    # Update order with payment info
    order.status = OrderStatus.AWAITING_PAYMENT

    # Increment promo code usage if applied
    if promo_code:
        result = await db.execute(select(PromoCode).where(PromoCode.code == promo_code))
        promo = result.scalar_one_or_none()
        if promo:
            promo.current_uses += 1

    # Clear cart after order created
    cart.items = []
    cart.promo_code = None
    cart.total_rub = Decimal("0")

    await db.commit()

    logger.info(f"Order created: {order_number} for user {user.id}, total: {total} RUB")

    # Build response
    response_items = [
        OrderItemResponse(
            product_code=item.get("product_code", ""),
            product_name=item.get("product_name", ""),
            plan_code=item.get("plan_code", ""),
            plan_name=item.get("plan_name", ""),
            price_rub=float(item.get("price_rub", 0)),
            billing_period=item.get("billing_period", "monthly"),
        )
        for item in order.items
    ]

    return CreateOrderResponse(
        success=True,
        order=OrderResponse(
            id=str(order.id),
            order_number=order.order_number,
            status=order.status.value,
            items=response_items,
            subtotal_rub=float(order.subtotal_rub),
            discount_rub=float(order.discount_rub),
            promo_code=order.promo_code,
            total_rub=float(order.total_rub),
            payment_url=payment_result.payment_url,
            created_at=order.created_at,
        ),
        payment_url=payment_result.payment_url,
        message="Заказ создан. Перенаправляем на страницу оплаты...",
    )


@router.get("/order/{order_id}")
async def get_order(
    order_id: str, db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)
):
    """
    Get order details.
    """
    result = await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id))
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заказ не найден")

    response_items = [
        OrderItemResponse(
            product_code=item.get("product_code", ""),
            product_name=item.get("product_name", ""),
            plan_code=item.get("plan_code", ""),
            plan_name=item.get("plan_name", ""),
            price_rub=float(item.get("price_rub", 0)),
            billing_period=item.get("billing_period", "monthly"),
        )
        for item in order.items
    ]

    return OrderResponse(
        id=str(order.id),
        order_number=order.order_number,
        status=order.status.value,
        items=response_items,
        subtotal_rub=float(order.subtotal_rub),
        discount_rub=float(order.discount_rub),
        promo_code=order.promo_code,
        total_rub=float(order.total_rub),
        payment_url=None,
        created_at=order.created_at,
    )


@router.get("/orders")
async def list_orders(db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)):
    """
    List user's orders.
    """
    result = await db.execute(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(50))
    orders = result.scalars().all()

    return {
        "orders": [
            {
                "id": str(order.id),
                "order_number": order.order_number,
                "status": order.status.value,
                "total_rub": float(order.total_rub),
                "created_at": order.created_at,
                "paid_at": order.paid_at,
            }
            for order in orders
        ]
    }


@router.post("/webhook/tochka", response_model=WebhookResponse)
async def tochka_webhook(
    request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db_async_session)
):
    """
    Handle payment webhook from Tochka Bank.

    This endpoint is called by Tochka when payment status changes.
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    logger.info(f"Received Tochka webhook: {data}")

    # Process webhook
    payment_service = get_payment_service()
    webhook_info = payment_service.process_webhook(data)

    if not webhook_info.get("verified"):
        logger.warning(f"Webhook verification failed: {webhook_info}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook verification failed")

    # Find order
    order_id = webhook_info.get("order_id")
    if not order_id:
        logger.error("Webhook missing order_id")
        return WebhookResponse(success=False, message="Missing order_id")

    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if not order:
        logger.error(f"Order not found for webhook: {order_id}")
        return WebhookResponse(success=False, message="Order not found")

    # Find payment
    result = await db.execute(select(Payment).where(Payment.order_id == order.id))
    payment = result.scalar_one_or_none()

    # Process based on status
    payment_status = webhook_info.get("status")

    if payment_status == "paid":
        # Payment successful
        order.status = OrderStatus.PAID
        order.paid_at = datetime.utcnow()

        if payment:
            payment.status = PaymentStatus.COMPLETED
            payment.paid_at = datetime.utcnow()

        await db.commit()

        # Get user for subscription activation
        result = await db.execute(select(User).where(User.id == order.user_id))
        user = result.scalar_one_or_none()

        if user:
            # Activate subscriptions
            await activate_subscription(db, user, order)

            # Send confirmation email
            background_tasks.add_task(send_order_confirmation_email, user, order)

        logger.info(f"Payment completed for order {order.order_number}")

    elif payment_status == "failed" or payment_status == "cancelled":
        # Payment failed or cancelled
        order.status = OrderStatus.FAILED

        if payment:
            payment.status = PaymentStatus.FAILED

        await db.commit()
        logger.info(f"Payment failed/cancelled for order {order.order_number}")

    elif payment_status == "refunded":
        # Payment refunded
        order.status = OrderStatus.REFUNDED

        if payment:
            payment.status = PaymentStatus.REFUNDED

        await db.commit()
        logger.info(f"Payment refunded for order {order.order_number}")

    return WebhookResponse(success=True, message="Webhook processed")


@router.post("/simulate-payment/{order_id}")
async def simulate_payment(
    order_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_async_session),
    user: User = Depends(get_current_user),
):
    """
    Simulate successful payment (for testing/development).

    Only works when Tochka credentials are not configured.
    """
    if settings.payment.merchant_id and settings.payment.secret_key.get_secret_value():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment simulation disabled in production")

    result = await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id))
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заказ не найден")

    if order.status != OrderStatus.AWAITING_PAYMENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Неверный статус заказа: {order.status.value}"
        )

    # Simulate payment completion
    order.status = OrderStatus.PAID
    order.paid_at = datetime.utcnow()

    result = await db.execute(select(Payment).where(Payment.order_id == order.id))
    payment = result.scalar_one_or_none()

    if payment:
        payment.status = PaymentStatus.COMPLETED
        payment.paid_at = datetime.utcnow()

    await db.commit()

    # Activate subscriptions
    await activate_subscription(db, user, order)

    # Send confirmation email
    background_tasks.add_task(send_order_confirmation_email, user, order)

    logger.info(f"Simulated payment for order {order.order_number}")

    return {"success": True, "message": "Платёж успешно симулирован", "order_number": order.order_number}
