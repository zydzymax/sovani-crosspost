#!/usr/bin/env python3
"""
Operational alert sender for production scripts.

Supports optional channels configured via environment variables:
- Telegram Bot API
- Generic webhook
- SMTP email
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable
from urllib import error, parse, request

DEFAULT_TIMEOUT_SECONDS = 10

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def _load_project_env() -> None:
    """Load project .env if present so cron/manual runs have config."""
    if load_dotenv is None:
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    env_path = os.path.join(project_root, ".env")
    if os.path.isfile(env_path):
        load_dotenv(env_path, override=False)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str) -> list[str]:
    separators = [",", ";"]
    for separator in separators:
        value = value.replace(separator, ",")
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class AlertPayload:
    event: str
    status: str
    subject: str
    message: str
    host: str
    timestamp_utc: str
    details: dict[str, str]

    def as_text(self) -> str:
        lines = [
            f"[OPS] {self.status.upper()} :: {self.event}",
            f"Host: {self.host}",
            f"Time (UTC): {self.timestamp_utc}",
            f"Subject: {self.subject}",
            f"Message: {self.message}",
        ]
        if self.details:
            lines.append("Details:")
            for key in sorted(self.details.keys()):
                lines.append(f"- {key}: {self.details[key]}")
        return "\n".join(lines)

    def as_json(self) -> dict[str, object]:
        return {
            "event": self.event,
            "status": self.status,
            "subject": self.subject,
            "message": self.message,
            "host": self.host,
            "timestamp_utc": self.timestamp_utc,
            "details": self.details,
        }


def _channel_telegram(payload: AlertPayload, dry_run: bool) -> tuple[bool, bool, str]:
    token = os.getenv("OPS_ALERT_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("OPS_ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, False, "not configured"

    if dry_run:
        return True, True, "dry-run"

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": payload.as_text(),
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    req = request.Request(api_url, data=body, method="POST")
    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if response.status == 200 and data.get("ok") is True:
                return True, True, "sent"
            return False, True, f"telegram api error: {raw}"
    except error.HTTPError as exc:
        response_body = ""
        try:
            response_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            response_body = ""
        if response_body:
            return False, True, f"telegram send failed: HTTP {exc.code} {exc.reason}; body={response_body}"
        return False, True, f"telegram send failed: HTTP {exc.code} {exc.reason}"
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, True, f"telegram send failed: {exc}"


def _parse_custom_header(value: str) -> tuple[str, str] | None:
    if ":" not in value:
        return None
    name, header_value = value.split(":", 1)
    name = name.strip()
    header_value = header_value.strip()
    if not name or not header_value:
        return None
    return name, header_value


def _channel_webhook(payload: AlertPayload, dry_run: bool) -> tuple[bool, bool, str]:
    webhook_url = os.getenv("OPS_ALERT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return False, False, "not configured"

    if dry_run:
        return True, True, "dry-run"

    headers = {"Content-Type": "application/json"}
    bearer = os.getenv("OPS_ALERT_WEBHOOK_BEARER_TOKEN", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    raw_custom_header = os.getenv("OPS_ALERT_WEBHOOK_AUTH_HEADER", "").strip()
    if raw_custom_header:
        parsed_header = _parse_custom_header(raw_custom_header)
        if parsed_header:
            headers[parsed_header[0]] = parsed_header[1]

    body = json.dumps(payload.as_json()).encode("utf-8")
    req = request.Request(webhook_url, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            if 200 <= response.status < 300:
                return True, True, "sent"
            return False, True, f"webhook status: {response.status}"
    except error.HTTPError as exc:
        response_body = ""
        try:
            response_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            response_body = ""
        if response_body:
            return False, True, f"webhook send failed: HTTP {exc.code} {exc.reason}; body={response_body}"
        return False, True, f"webhook send failed: HTTP {exc.code} {exc.reason}"
    except (error.URLError, TimeoutError) as exc:
        return False, True, f"webhook send failed: {exc}"


def _channel_email(payload: AlertPayload, dry_run: bool) -> tuple[bool, bool, str]:
    smtp_host = os.getenv("OPS_ALERT_SMTP_HOST", "").strip()
    smtp_port_raw = os.getenv("OPS_ALERT_SMTP_PORT", "465").strip()
    smtp_user = os.getenv("OPS_ALERT_SMTP_USER", "").strip()
    smtp_password = os.getenv("OPS_ALERT_SMTP_PASSWORD", "").strip()
    smtp_to_raw = os.getenv("OPS_ALERT_SMTP_TO", "").strip()
    smtp_from = os.getenv("OPS_ALERT_SMTP_FROM", "").strip() or smtp_user

    if not smtp_host or not smtp_to_raw:
        return False, False, "not configured"

    recipients = _split_csv(smtp_to_raw)
    if not recipients:
        return False, False, "not configured"

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        return False, True, "invalid OPS_ALERT_SMTP_PORT"

    if dry_run:
        return True, True, "dry-run"

    msg = EmailMessage()
    msg["Subject"] = payload.subject
    msg["From"] = smtp_from or "ops-alert@saleswhisper.local"
    msg["To"] = ", ".join(recipients)
    msg.set_content(payload.as_text())

    use_ssl = _env_flag("OPS_ALERT_SMTP_USE_SSL", True)
    starttls = _env_flag("OPS_ALERT_SMTP_STARTTLS", not use_ssl)

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=DEFAULT_TIMEOUT_SECONDS, context=context) as server:
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
            return True, True, "sent"

        with smtplib.SMTP(smtp_host, smtp_port, timeout=DEFAULT_TIMEOUT_SECONDS) as server:
            server.ehlo()
            if starttls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True, True, "sent"
    except Exception as exc:
        return False, True, f"smtp send failed: {exc}"


def _parse_details(raw_details: list[str]) -> dict[str, str]:
    details: dict[str, str] = {}
    for item in raw_details:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            details[key] = value
    return details


def _build_payload(args: argparse.Namespace) -> AlertPayload:
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hostname = args.host or os.getenv("OPS_ALERT_HOSTNAME") or os.uname().nodename
    details = _parse_details(args.detail)

    subject = args.subject or f"[OPS][{args.status.upper()}] {args.event} @ {hostname}"
    return AlertPayload(
        event=args.event,
        status=args.status,
        subject=subject,
        message=args.message,
        host=hostname,
        timestamp_utc=timestamp_utc,
        details=details,
    )


def main() -> int:
    _load_project_env()

    parser = argparse.ArgumentParser(description="Send operational alerts to Telegram/webhook/SMTP")
    parser.add_argument("--event", required=True, help="Alert event name, e.g. backup|smoke|post-deploy")
    parser.add_argument("--status", default="info", choices=["success", "failure", "info"], help="Alert status")
    parser.add_argument("--subject", default="", help="Optional custom subject/title")
    parser.add_argument("--message", required=True, help="Alert message")
    parser.add_argument("--host", default="", help="Override host in payload")
    parser.add_argument("--detail", action="append", default=[], help="Additional detail key=value (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send, only print channel decisions")
    args = parser.parse_args()

    payload = _build_payload(args)
    channels: list[tuple[str, Callable[[AlertPayload, bool], tuple[bool, bool, str]]]] = [
        ("telegram", _channel_telegram),
        ("webhook", _channel_webhook),
        ("email", _channel_email),
    ]

    configured_count = 0
    sent_count = 0

    print(f"[ops-alert] event={payload.event} status={payload.status} dry_run={args.dry_run}")

    for channel_name, sender in channels:
        ok, configured, info = sender(payload, args.dry_run)
        if configured:
            configured_count += 1
        if ok and configured:
            sent_count += 1
            print(f"[ops-alert] {channel_name}: OK ({info})")
        elif configured:
            print(f"[ops-alert] {channel_name}: FAIL ({info})", file=sys.stderr)
        else:
            print(f"[ops-alert] {channel_name}: SKIP ({info})")

    if configured_count == 0:
        print("[ops-alert] no channels configured")
        return 0

    if sent_count > 0:
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
