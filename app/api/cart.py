"""
Shopping Cart API for SalesWhisper unified cart.
Allows users to add SaaS products/plans to cart before checkout.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.entities import Cart, PromoCode, SaaSProduct, SaaSProductPlan, User
from .deps import get_current_user, get_db_async_session

logger = get_logger("api.cart")

router = APIRouter(prefix="/cart", tags=["Cart"])


# ==================== SCHEMAS ====================


class CartItemInput(BaseModel):
    """Request to add item to cart."""

    product_code: str = Field(..., description="Product code (crosspost, headofsales)")
    plan_code: str = Field(..., description="Plan code (demo, starter, pro, business)")


class CartItemResponse(BaseModel):
    """Cart item in response."""

    id: str
    product_code: str
    product_name: str
    plan_code: str
    plan_name: str
    price_rub: float
    billing_period: str


class CartResponse(BaseModel):
    """Full cart response."""

    items: list[CartItemResponse]
    subtotal_rub: float
    discount_rub: float = 0
    promo_code: str | None = None
    promo_description: str | None = None
    total_rub: float


class ApplyPromoRequest(BaseModel):
    """Request to apply promo code."""

    promo_code: str = Field(..., min_length=1, max_length=50)


class ApplyPromoResponse(BaseModel):
    """Response after applying promo."""

    success: bool
    message: str
    discount_rub: float = 0
    discount_percent: float = 0


# ==================== HELPERS ====================


async def get_or_create_cart(db: AsyncSession, user: User) -> Cart:
    """Get existing cart or create new one for user."""
    result = await db.execute(select(Cart).where(Cart.user_id == user.id))
    cart = result.scalar_one_or_none()

    if cart is None:
        cart = Cart(user_id=user.id, items=[], total_rub=Decimal("0"))
        db.add(cart)
        await db.commit()
        await db.refresh(cart)

    return cart


async def calculate_cart_total(db: AsyncSession, cart: Cart) -> tuple[Decimal, Decimal]:
    """Calculate cart subtotal and discount."""
    subtotal = Decimal("0")
    discount = Decimal("0")

    for item in cart.items:
        subtotal += Decimal(str(item.get("price_rub", 0)))

    # Apply promo code discount if exists
    if cart.promo_code:
        result = await db.execute(select(PromoCode).where(PromoCode.code == cart.promo_code, PromoCode.is_active))
        promo = result.scalar_one_or_none()

        if promo and promo.is_valid:
            if promo.discount_percent:
                discount = subtotal * (Decimal(str(promo.discount_percent)) / 100)
            elif promo.discount_amount:
                discount = min(promo.discount_amount, subtotal)

    return subtotal, discount


# ==================== ROUTES ====================


@router.get("", response_model=CartResponse)
async def get_cart(db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)):
    """
    Get current user's shopping cart.
    """
    cart = await get_or_create_cart(db, user)
    subtotal, discount = await calculate_cart_total(db, cart)

    # Build response items with product/plan names
    response_items = []
    for item in cart.items:
        response_items.append(
            CartItemResponse(
                id=item.get("id", ""),
                product_code=item.get("product_code", ""),
                product_name=item.get("product_name", ""),
                plan_code=item.get("plan_code", ""),
                plan_name=item.get("plan_name", ""),
                price_rub=float(item.get("price_rub", 0)),
                billing_period=item.get("billing_period", "monthly"),
            )
        )

    # Get promo description if applied
    promo_description = None
    if cart.promo_code:
        result = await db.execute(select(PromoCode).where(PromoCode.code == cart.promo_code))
        promo = result.scalar_one_or_none()
        if promo:
            promo_description = promo.description

    return CartResponse(
        items=response_items,
        subtotal_rub=float(subtotal),
        discount_rub=float(discount),
        promo_code=cart.promo_code,
        promo_description=promo_description,
        total_rub=float(subtotal - discount),
    )


@router.post("/add", response_model=CartResponse)
async def add_to_cart(
    item: CartItemInput, db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)
):
    """
    Add a product plan to the shopping cart.
    """
    # Find product
    result = await db.execute(select(SaaSProduct).where(SaaSProduct.code == item.product_code, SaaSProduct.is_active))
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{item.product_code}' not found")

    # Find plan
    result = await db.execute(
        select(SaaSProductPlan).where(
            SaaSProductPlan.product_id == product.id, SaaSProductPlan.code == item.plan_code, SaaSProductPlan.is_active
        )
    )
    plan = result.scalar_one_or_none()

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{item.plan_code}' not found for product '{item.product_code}'",
        )

    # Don't allow demo plans to be purchased
    if plan.code == "demo":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Demo plan is free and cannot be added to cart"
        )

    # Get or create cart
    cart = await get_or_create_cart(db, user)

    # Check if same product already in cart (replace plan)
    cart_items = list(cart.items) if cart.items else []
    existing_idx = None
    for idx, cart_item in enumerate(cart_items):
        if cart_item.get("product_code") == item.product_code:
            existing_idx = idx
            break

    # Create new cart item
    import uuid

    new_item = {
        "id": str(uuid.uuid4()),
        "product_id": str(product.id),
        "product_code": product.code,
        "product_name": product.name,
        "plan_id": str(plan.id),
        "plan_code": plan.code,
        "plan_name": plan.name,
        "price_rub": float(plan.price_rub),
        "billing_period": plan.billing_period,
        "added_at": datetime.utcnow().isoformat(),
    }

    if existing_idx is not None:
        cart_items[existing_idx] = new_item
        logger.info(f"Updated cart item for user {user.id}: {item.product_code}/{item.plan_code}")
    else:
        cart_items.append(new_item)
        logger.info(f"Added to cart for user {user.id}: {item.product_code}/{item.plan_code}")

    # Update cart
    cart.items = cart_items
    cart.updated_at = datetime.utcnow()

    # Recalculate total
    subtotal, discount = await calculate_cart_total(db, cart)
    cart.total_rub = subtotal - discount

    await db.commit()
    await db.refresh(cart)

    # Return updated cart
    return await get_cart(db, user)


@router.delete("/remove/{item_id}")
async def remove_from_cart(
    item_id: str, db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)
):
    """
    Remove an item from the shopping cart.
    """
    cart = await get_or_create_cart(db, user)

    cart_items = list(cart.items) if cart.items else []
    original_count = len(cart_items)

    # Remove item by id
    cart_items = [item for item in cart_items if item.get("id") != item_id]

    if len(cart_items) == original_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found in cart")

    # Update cart
    cart.items = cart_items
    cart.updated_at = datetime.utcnow()

    # Recalculate total
    subtotal, discount = await calculate_cart_total(db, cart)
    cart.total_rub = subtotal - discount

    await db.commit()

    logger.info(f"Removed item {item_id} from cart for user {user.id}")

    return {"success": True, "message": "Item removed from cart"}


@router.post("/clear")
async def clear_cart(db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)):
    """
    Clear all items from the shopping cart.
    """
    cart = await get_or_create_cart(db, user)

    cart.items = []
    cart.promo_code = None
    cart.total_rub = Decimal("0")
    cart.updated_at = datetime.utcnow()

    await db.commit()

    logger.info(f"Cleared cart for user {user.id}")

    return {"success": True, "message": "Cart cleared"}


@router.post("/apply-promo", response_model=ApplyPromoResponse)
async def apply_promo_code(
    request: ApplyPromoRequest, db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)
):
    """
    Apply a promo code to the cart.
    """
    code = request.promo_code.upper().strip()

    # Find promo code
    result = await db.execute(select(PromoCode).where(PromoCode.code == code, PromoCode.is_active))
    promo = result.scalar_one_or_none()

    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Промокод не найден")

    # Check if promo is valid
    if not promo.is_valid:
        if promo.valid_until and promo.valid_until < datetime.utcnow():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок действия промокода истёк")
        if promo.max_uses and promo.current_uses >= promo.max_uses:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Промокод больше не действителен")

    # Get cart
    cart = await get_or_create_cart(db, user)

    if not cart.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Корзина пуста")

    # Apply promo to cart
    cart.promo_code = code
    cart.updated_at = datetime.utcnow()

    # Calculate new total
    subtotal, discount = await calculate_cart_total(db, cart)
    cart.total_rub = subtotal - discount

    await db.commit()

    logger.info(f"Applied promo {code} to cart for user {user.id}, discount: {discount}")

    return ApplyPromoResponse(
        success=True,
        message=f"Промокод применён: {promo.description or code}",
        discount_rub=float(discount),
        discount_percent=float(promo.discount_percent or 0),
    )


@router.delete("/remove-promo")
async def remove_promo_code(db: AsyncSession = Depends(get_db_async_session), user: User = Depends(get_current_user)):
    """
    Remove promo code from cart.
    """
    cart = await get_or_create_cart(db, user)

    if not cart.promo_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Промокод не применён")

    cart.promo_code = None
    cart.updated_at = datetime.utcnow()

    # Recalculate total
    subtotal, _ = await calculate_cart_total(db, cart)
    cart.total_rub = subtotal

    await db.commit()

    logger.info(f"Removed promo from cart for user {user.id}")

    return {"success": True, "message": "Промокод удалён"}
