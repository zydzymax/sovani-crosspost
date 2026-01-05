"""
Payment Service for Tochka Bank integration.
Handles payment creation, verification, and webhook processing.
"""

import hashlib
import hmac
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

import httpx
from pydantic import BaseModel

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.payment")


class PaymentError(Exception):
    """Payment processing error."""
    pass


class PaymentResult(BaseModel):
    """Result of payment creation."""
    success: bool
    payment_id: str
    payment_url: Optional[str] = None
    status: str
    error: Optional[str] = None


class TochkaPaymentService:
    """
    Tochka Bank payment integration.

    Implements:
    - Payment link creation
    - Payment status check
    - Webhook signature verification
    """

    def __init__(self):
        self.merchant_id = settings.payment.merchant_id
        self.secret_key = settings.payment.secret_key.get_secret_value() if settings.payment.secret_key else ""
        self.api_url = settings.payment.api_url
        self.callback_url = settings.payment.callback_url
        self.success_url = settings.payment.success_url
        self.fail_url = settings.payment.fail_url

    def _generate_signature(self, data: Dict[str, Any]) -> str:
        """Generate HMAC signature for request."""
        # Sort keys and create string
        sorted_items = sorted(data.items())
        sign_string = "&".join(f"{k}={v}" for k, v in sorted_items if v is not None)

        # Add secret key
        sign_string += f"&{self.secret_key}"

        # Create SHA256 hash
        signature = hashlib.sha256(sign_string.encode()).hexdigest()
        return signature

    def _verify_webhook_signature(self, data: Dict[str, Any], signature: str) -> bool:
        """Verify webhook signature from Tochka."""
        # Remove signature from data for verification
        data_copy = {k: v for k, v in data.items() if k != "signature"}
        expected_signature = self._generate_signature(data_copy)
        return hmac.compare_digest(signature, expected_signature)

    async def create_payment(
        self,
        order_id: str,
        amount_rub: Decimal,
        description: str,
        customer_email: str,
        customer_phone: Optional[str] = None,
        return_url: Optional[str] = None
    ) -> PaymentResult:
        """
        Create payment link in Tochka Bank.

        Args:
            order_id: Internal order ID
            amount_rub: Amount in RUB
            description: Payment description
            customer_email: Customer email
            customer_phone: Customer phone (optional)
            return_url: Custom return URL (optional)

        Returns:
            PaymentResult with payment URL or error
        """
        if not self.merchant_id or not self.secret_key:
            logger.warning("Tochka Bank credentials not configured, using mock payment")
            return PaymentResult(
                success=True,
                payment_id=f"mock_{uuid.uuid4().hex[:16]}",
                payment_url=f"{self.success_url}?order_id={order_id}&mock=true",
                status="mock"
            )

        try:
            # Prepare payment data
            payment_data = {
                "merchant_id": self.merchant_id,
                "order_id": order_id,
                "amount": int(amount_rub * 100),  # Convert to kopeks
                "currency": "RUB",
                "description": description[:256],  # Tochka limit
                "customer_email": customer_email,
                "callback_url": self.callback_url,
                "success_url": return_url or self.success_url,
                "fail_url": self.fail_url,
            }

            if customer_phone:
                payment_data["customer_phone"] = customer_phone

            # Add signature
            payment_data["signature"] = self._generate_signature(payment_data)

            # Create payment via API
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/payments/create",
                    json=payment_data
                )

                if response.status_code != 200:
                    logger.error(f"Tochka API error: {response.status_code} - {response.text}")
                    return PaymentResult(
                        success=False,
                        payment_id="",
                        status="error",
                        error=f"Payment provider error: {response.status_code}"
                    )

                result = response.json()

                if result.get("success"):
                    logger.info(f"Payment created: {result.get('payment_id')} for order {order_id}")
                    return PaymentResult(
                        success=True,
                        payment_id=result.get("payment_id", ""),
                        payment_url=result.get("payment_url", ""),
                        status="created"
                    )
                else:
                    logger.error(f"Payment creation failed: {result}")
                    return PaymentResult(
                        success=False,
                        payment_id="",
                        status="error",
                        error=result.get("error", "Unknown error")
                    )

        except httpx.TimeoutException:
            logger.error("Tochka API timeout")
            return PaymentResult(
                success=False,
                payment_id="",
                status="error",
                error="Payment provider timeout"
            )
        except Exception as e:
            logger.error(f"Payment creation error: {e}")
            return PaymentResult(
                success=False,
                payment_id="",
                status="error",
                error=str(e)
            )

    async def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Check payment status in Tochka Bank.

        Args:
            payment_id: Payment ID from Tochka

        Returns:
            Payment status info
        """
        if not self.merchant_id or not self.secret_key:
            return {"status": "mock", "paid": True}

        try:
            check_data = {
                "merchant_id": self.merchant_id,
                "payment_id": payment_id,
            }
            check_data["signature"] = self._generate_signature(check_data)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/payments/status",
                    json=check_data
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Payment status check failed: {response.status_code}")
                    return {"status": "error", "error": f"Status check failed: {response.status_code}"}

        except Exception as e:
            logger.error(f"Payment status check error: {e}")
            return {"status": "error", "error": str(e)}

    def process_webhook(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process webhook from Tochka Bank.

        Args:
            data: Webhook payload

        Returns:
            Processed webhook data with verification status
        """
        signature = data.get("signature", "")

        # Verify signature (skip for mock payments)
        if self.secret_key and not data.get("mock"):
            if not self._verify_webhook_signature(data, signature):
                logger.warning("Invalid webhook signature")
                return {"verified": False, "error": "Invalid signature"}

        # Extract payment info
        payment_info = {
            "verified": True,
            "payment_id": data.get("payment_id"),
            "order_id": data.get("order_id"),
            "status": data.get("status"),
            "amount": Decimal(str(data.get("amount", 0))) / 100,  # Convert from kopeks
            "paid_at": data.get("paid_at"),
        }

        logger.info(f"Webhook processed: {payment_info}")
        return payment_info


# Singleton instance
_payment_service: Optional[TochkaPaymentService] = None


def get_payment_service() -> TochkaPaymentService:
    """Get payment service singleton."""
    global _payment_service
    if _payment_service is None:
        _payment_service = TochkaPaymentService()
    return _payment_service
