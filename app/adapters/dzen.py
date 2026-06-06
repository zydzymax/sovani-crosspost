"""
Dzen (dzen.ru) publishing adapter for SalesWhisper Crosspost.

Dzen's API: https://dzen.ru/api/publisher-api
Supports publishing articles via OAuth2 access token.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from ..core.logging import get_logger

logger = get_logger("adapters.dzen")

DZEN_API_BASE = "https://api.dzen.ru"
DZEN_ARTICLE_URL = f"{DZEN_API_BASE}/v1/publisher/articles"


class DzenPostStatus(Enum):
    PUBLISHED = "published"
    PENDING = "pending"
    FAILED = "failed"
    DRAFT = "draft"


@dataclass
class DzenPublishResult:
    success: bool
    status: DzenPostStatus
    post_id: str | None = None
    post_url: str | None = None
    message: str | None = None
    raw: dict | None = None


async def publish_dzen_article(
    title: str,
    body: str,
    access_token: str,
    image_url: str | None = None,
    tags: list[str] | None = None,
) -> DzenPublishResult:
    """
    Publish an article to Dzen.

    Args:
        title: Article title (up to 200 chars)
        body: Article body (plain text or basic HTML)
        access_token: OAuth2 access token for Dzen publisher
        image_url: Optional cover image URL
        tags: Optional list of tags

    Returns:
        DzenPublishResult with status and post info
    """
    if not access_token or access_token.startswith("your-"):
        return DzenPublishResult(
            success=False,
            status=DzenPostStatus.FAILED,
            message="Dzen access token not configured",
        )

    # Build article payload
    # Dzen uses its own JSON format for articles
    content_blocks = []

    if image_url:
        content_blocks.append({
            "type": "image",
            "data": {"url": image_url, "caption": ""},
        })

    # Split body into paragraphs
    for paragraph in body.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            content_blocks.append({
                "type": "paragraph",
                "data": {"text": paragraph},
            })

    payload = {
        "title": title[:200],
        "content": content_blocks,
        "tags": (tags or [])[:10],
        "publishMode": "publish",  # or "draft"
    }

    headers = {
        "Authorization": f"OAuth {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                DZEN_ARTICLE_URL,
                headers=headers,
                json=payload,
            )

            if response.status_code == 200:
                data = response.json()
                post_id = data.get("id") or data.get("articleId")
                post_url = data.get("url") or (f"https://dzen.ru/a/{post_id}" if post_id else None)
                return DzenPublishResult(
                    success=True,
                    status=DzenPostStatus.PUBLISHED,
                    post_id=post_id,
                    post_url=post_url,
                    message="Published successfully",
                    raw=data,
                )
            elif response.status_code == 401:
                return DzenPublishResult(
                    success=False,
                    status=DzenPostStatus.FAILED,
                    message=f"Dzen auth failed: token invalid or expired",
                )
            else:
                body_text = response.text[:300]
                logger.warning("Dzen API error", status=response.status_code, body=body_text)
                return DzenPublishResult(
                    success=False,
                    status=DzenPostStatus.FAILED,
                    message=f"Dzen API {response.status_code}: {body_text}",
                )
    except httpx.TimeoutException:
        return DzenPublishResult(
            success=False,
            status=DzenPostStatus.FAILED,
            message="Dzen API request timeout",
        )
    except Exception as e:
        logger.exception("Dzen publish error")
        return DzenPublishResult(
            success=False,
            status=DzenPostStatus.FAILED,
            message=str(e),
        )
