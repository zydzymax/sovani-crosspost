"""
Email service for sending authentication codes and notifications.

Uses aiosmtplib for async email delivery via SMTP.
"""

import asyncio
import logging
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import aiosmtplib
from jinja2 import Environment, BaseLoader

logger = logging.getLogger(__name__)


# Email templates as strings (no file dependencies)
AUTH_CODE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .logo { font-size: 24px; font-weight: bold; color: #6366f1; }
        .code-box { background: #f3f4f6; border-radius: 8px; padding: 24px; text-align: center; margin: 24px 0; }
        .code { font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #111827; font-family: monospace; }
        .info { color: #6b7280; font-size: 14px; }
        .footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">SalesWhisper</div>
        </div>

        <h2>Код для входа</h2>
        <p>Используйте этот код для входа в ваш аккаунт:</p>

        <div class="code-box">
            <div class="code">{{ code }}</div>
        </div>

        <p class="info">Код действителен <strong>{{ expires_minutes }} минут</strong>.</p>
        <p class="info">Если вы не запрашивали код, просто проигнорируйте это письмо.</p>

        <div class="footer">
            <p>Это автоматическое сообщение от SalesWhisper.</p>
            <p>Не отвечайте на это письмо.</p>
        </div>
    </div>
</body>
</html>
"""

ORDER_CONFIRMATION_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .logo { font-size: 24px; font-weight: bold; color: #6366f1; }
        .order-info { background: #f3f4f6; border-radius: 8px; padding: 20px; margin: 20px 0; }
        .items { margin: 20px 0; }
        .item { padding: 12px 0; border-bottom: 1px solid #e5e7eb; }
        .item:last-child { border-bottom: none; }
        .total { font-size: 18px; font-weight: bold; text-align: right; margin-top: 20px; }
        .footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">SalesWhisper</div>
        </div>

        <h2>Спасибо за заказ!</h2>
        <p>Ваш заказ <strong>#{{ order_number }}</strong> успешно оплачен.</p>

        <div class="order-info">
            <div class="items">
                {% for item in items %}
                <div class="item">
                    <strong>{{ item.product_name }}</strong> — {{ item.plan_name }}<br>
                    <span style="color: #6b7280;">{{ item.price_rub }} ₽/мес</span>
                </div>
                {% endfor %}
            </div>
            <div class="total">Итого: {{ total_rub }} ₽</div>
        </div>

        <p>Доступ к сервисам активирован. Войдите в личный кабинет:</p>
        <p><a href="https://saleswhisper.pro/account">saleswhisper.pro/account</a></p>

        <div class="footer">
            <p>SalesWhisper — автоматизация продаж и маркетинга</p>
        </div>
    </div>
</body>
</html>
"""


@dataclass
class EmailConfig:
    """Email service configuration."""
    smtp_host: str = "smtp.yandex.ru"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = "SalesWhisper <auth@saleswhisper.pro>"
    use_ssl: bool = True
    timeout: int = 30


class EmailService:
    """Async email service for sending notifications."""

    def __init__(self, config: EmailConfig):
        self.config = config
        self.jinja_env = Environment(loader=BaseLoader())

        # Pre-compile templates
        self._auth_code_template = self.jinja_env.from_string(AUTH_CODE_TEMPLATE)
        self._order_confirmation_template = self.jinja_env.from_string(ORDER_CONFIRMATION_TEMPLATE)

    async def send_auth_code(
        self,
        to_email: str,
        code: str,
        expires_minutes: int = 5
    ) -> bool:
        """Send authentication code email."""
        subject = f"Код входа в SalesWhisper: {code}"

        html = self._auth_code_template.render(
            code=code,
            expires_minutes=expires_minutes
        )

        return await self._send_email(
            to_email=to_email,
            subject=subject,
            html=html
        )

    async def send_order_confirmation(
        self,
        to_email: str,
        order_number: str,
        items: list,
        total_rub: float
    ) -> bool:
        """Send order confirmation email."""
        subject = f"Заказ #{order_number} оплачен — SalesWhisper"

        html = self._order_confirmation_template.render(
            order_number=order_number,
            items=items,
            total_rub=total_rub
        )

        return await self._send_email(
            to_email=to_email,
            subject=subject,
            html=html
        )

    async def send_subscription_activated(
        self,
        to_email: str,
        product_name: str,
        plan_name: str,
        expires_at: Optional[str] = None
    ) -> bool:
        """Send subscription activation email."""
        subject = f"Подписка {product_name} активирована — SalesWhisper"

        html = f"""
        <h2>Подписка активирована!</h2>
        <p>Ваша подписка <strong>{product_name} — {plan_name}</strong> успешно активирована.</p>
        {"<p>Действует до: " + expires_at + "</p>" if expires_at else ""}
        <p><a href="https://saleswhisper.pro/account">Войти в личный кабинет</a></p>
        """

        return await self._send_email(
            to_email=to_email,
            subject=subject,
            html=html
        )

    async def _send_email(
        self,
        to_email: str,
        subject: str,
        html: str,
        text: Optional[str] = None
    ) -> bool:
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.from_email
            msg["To"] = to_email

            # Plain text fallback
            if text:
                msg.attach(MIMEText(text, "plain", "utf-8"))

            # HTML version
            msg.attach(MIMEText(html, "html", "utf-8"))

            # Send via SMTP
            if self.config.use_ssl:
                await aiosmtplib.send(
                    msg,
                    hostname=self.config.smtp_host,
                    port=self.config.smtp_port,
                    username=self.config.smtp_user,
                    password=self.config.smtp_password,
                    use_tls=True,
                    timeout=self.config.timeout
                )
            else:
                await aiosmtplib.send(
                    msg,
                    hostname=self.config.smtp_host,
                    port=self.config.smtp_port,
                    username=self.config.smtp_user,
                    password=self.config.smtp_password,
                    start_tls=True,
                    timeout=self.config.timeout
                )

            logger.info(f"Email sent successfully to {to_email}: {subject}")
            return True

        except aiosmtplib.SMTPException as e:
            logger.error(f"SMTP error sending email to {to_email}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending email to {to_email}: {e}")
            return False


# Singleton instance (will be initialized with config)
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get the email service instance."""
    global _email_service
    if _email_service is None:
        raise RuntimeError("Email service not initialized. Call init_email_service first.")
    return _email_service


def init_email_service(config: EmailConfig) -> EmailService:
    """Initialize the email service with config."""
    global _email_service
    _email_service = EmailService(config)
    return _email_service
