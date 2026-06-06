"""
Blog auto-generation task for SalesWhisper.

Pipeline:
  1. Fetch top relevant tech news via news_aggregator
  2. Write full article via article_writer (Anton Merkulov persona)
  3. Send Telegram preview with Approve / Reject buttons
  4. On approval: publish HTML to /var/www/saleswhisper.pro/blog/,
     update sitemap.xml, ping Google/Yandex, enqueue social posts

Beat schedule: every 3 days at 09:00 Moscow time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ...core.config import settings
from ...core.logging import get_logger
from ..celery_app import celery

logger = get_logger("tasks.blog_autogen")

BLOG_DIR = Path("/var/www/saleswhisper.pro/blog")
SITEMAP_PATH = Path("/var/www/saleswhisper.pro/sitemap.xml")
SITE_BASE_URL = "https://saleswhisper.pro"
TG_BOT_TOKEN = settings.TG_BOT_TOKEN
TG_ADMIN_CHANNEL_ID = settings.TG_ADMIN_CHANNEL_ID

# Pending approvals stored in Redis (key → article JSON)
PENDING_KEY_PREFIX = "blog:pending:"


# ---------------------------------------------------------------------------
# HTML template builder
# ---------------------------------------------------------------------------

def _build_article_html(article_data: dict, source_url: str, source_name: str) -> str:
    """Build full HTML page from article data using existing blog page structure."""
    title = article_data["title"]
    meta_desc = article_data.get("meta_description", "")
    body_html = article_data.get("body_html", "")
    tags = article_data.get("tags", [])
    date_str = datetime.now().strftime("%d.%m.%Y")

    tags_html = " ".join(f'<span class="tag">{t}</span>' for t in tags)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title} — SalesWhisper</title>
  <meta name="description" content="{meta_desc}"/>
  <link rel="canonical" href="{SITE_BASE_URL}/blog/{article_data['slug']}.html"/>
  <meta property="og:title" content="{title}"/>
  <meta property="og:description" content="{meta_desc}"/>
  <meta property="og:type" content="article"/>
  <meta property="og:url" content="{SITE_BASE_URL}/blog/{article_data['slug']}.html"/>
  <link rel="stylesheet" href="/_next/static/chunks/e7da8b3b73161038.css" data-precedence="next"/>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px; margin: 0 auto; padding: 24px 16px; color: #1a1a1a; line-height: 1.7; }}
    h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem; }}
    .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 2rem; }}
    .tag {{ background: #f0f0f0; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; margin-right: 4px; }}
    .source {{ border-top: 1px solid #eee; margin-top: 2rem; padding-top: 1rem;
               font-size: 0.85rem; color: #888; }}
    article h2 {{ font-size: 1.4rem; margin-top: 2rem; }}
    article p {{ margin-bottom: 1.2rem; }}
    article a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <a href="/blog/" style="color:#888;font-size:0.9rem;">← Блог</a>
  <h1>{title}</h1>
  <div class="meta">
    <span>{date_str}</span> · <span>Антон Меркулов</span> · {tags_html}
  </div>
  {body_html}
  <div class="source">
    Материал подготовлен на основе публикации <a href="{source_url}" target="_blank">{source_name}</a>
  </div>
  <script async src="/_next/static/chunks/aee6c7720838f8a2.js"></script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Sitemap updater
# ---------------------------------------------------------------------------

def _update_sitemap(slug: str) -> None:
    """Add new blog post URL to sitemap.xml."""
    import xml.etree.ElementTree as ET

    url = f"{SITE_BASE_URL}/blog/{slug}.html"
    today = datetime.now().strftime("%Y-%m-%d")

    if SITEMAP_PATH.exists():
        try:
            tree = ET.parse(SITEMAP_PATH)
            root = tree.getroot()
            ns = "http://www.sitemaps.org/schemas/sitemap/0.9"

            # Check if URL already present
            for existing in root.findall(f"{{{ns}}}url"):
                loc = existing.findtext(f"{{{ns}}}loc")
                if loc == url:
                    return

            new_url = ET.SubElement(root, f"{{{ns}}}url")
            ET.SubElement(new_url, f"{{{ns}}}loc").text = url
            ET.SubElement(new_url, f"{{{ns}}}lastmod").text = today
            ET.SubElement(new_url, f"{{{ns}}}changefreq").text = "weekly"
            ET.SubElement(new_url, f"{{{ns}}}priority").text = "0.7"

            ET.indent(tree, space="  ")
            tree.write(SITEMAP_PATH, xml_declaration=True, encoding="utf-8")
            logger.info("sitemap updated with %s", url)
        except Exception as e:
            logger.warning("Failed to update sitemap: %s", e)
    else:
        logger.warning("sitemap.xml not found at %s", SITEMAP_PATH)


def _ping_search_engines() -> None:
    """Notify Google and Yandex about updated sitemap."""
    sitemap_url = f"{SITE_BASE_URL}/sitemap.xml"
    endpoints = [
        f"https://www.google.com/ping?sitemap={sitemap_url}",
        f"https://webmaster.yandex.ru/ping?sitemap={sitemap_url}",
    ]
    with httpx.Client(timeout=10) as client:
        for ep in endpoints:
            try:
                client.get(ep)
                logger.info("Pinged: %s", ep)
            except Exception as e:
                logger.warning("Ping failed for %s: %s", ep, e)


# ---------------------------------------------------------------------------
# Telegram approval gate
# ---------------------------------------------------------------------------

def _send_telegram_approval_request(article: dict, pending_key: str) -> bool:
    """Send article preview to admin Telegram channel with approve/reject buttons."""
    if not TG_BOT_TOKEN or not TG_ADMIN_CHANNEL_ID or TG_ADMIN_CHANNEL_ID == "-1001234567890":
        logger.warning("Telegram admin channel not configured — auto-approving")
        return True  # Auto-approve if Telegram not set up

    preview = (
        f"📝 *Новая статья для блога*\n\n"
        f"*{article['title']}*\n\n"
        f"{article.get('meta_description', '')}\n\n"
        f"🏷 Теги: {', '.join(article.get('tags', []))}\n"
        f"📊 Источник: {article.get('source_name', '')}\n\n"
        f"*Превью для TG:*\n_{article.get('summary_tg', '')}_"
    )

    inline_keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": f"blog_approve:{pending_key}"},
            {"text": "❌ Отклонить", "callback_data": f"blog_reject:{pending_key}"},
        ]]
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_ADMIN_CHANNEL_ID,
                    "text": preview,
                    "parse_mode": "Markdown",
                    "reply_markup": inline_keyboard,
                },
            )
            resp.raise_for_status()
            logger.info("Telegram approval request sent for article: %s", article["title"])
            return False  # Waiting for approval
    except Exception as e:
        logger.warning("Failed to send Telegram approval: %s — auto-approving", e)
        return True  # Auto-approve on failure


# ---------------------------------------------------------------------------
# Blog publisher
# ---------------------------------------------------------------------------

def _publish_to_blog(article: dict, source_url: str, source_name: str) -> str:
    """Write article HTML to blog directory. Returns published URL."""
    BLOG_DIR.mkdir(parents=True, exist_ok=True)

    slug = article["slug"]
    # Sanitize slug
    slug = re.sub(r"[^a-z0-9\-]", "", slug.lower().replace(" ", "-"))
    if not slug:
        slug = f"article-{int(time.time())}"

    html = _build_article_html(article, source_url, source_name)
    file_path = BLOG_DIR / f"{slug}.html"

    file_path.write_text(html, encoding="utf-8")
    logger.info("Published blog article: %s", file_path)

    _update_sitemap(slug)
    _ping_search_engines()

    return f"{SITE_BASE_URL}/blog/{slug}.html"


def _enqueue_social_posts(article: dict, article_url: str) -> None:
    """Trigger crosspost pipeline for social media distribution."""
    from .ingest import process_telegram_update

    post_id = str(uuid.uuid4())
    social_text = (
        f"{article['title']}\n\n"
        f"{article.get('summary_tg', article.get('meta_description', ''))}\n\n"
        f"Читать полностью: {article_url}"
    )

    fake_update = {
        "update_id": int(time.time()),
        "message": {
            "message_id": int(time.time()),
            "text": social_text,
            "chat": {"id": 0, "type": "channel"},
            "date": int(time.time()),
        },
    }

    process_telegram_update.apply_async(
        args=[fake_update, post_id],
        queue="ingest",
    )
    logger.info("Enqueued social crosspost for article: %s (post_id=%s)", article["title"], post_id)


# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------

@celery.task(bind=True, name="app.workers.tasks.blog_autogen.generate_blog_article", max_retries=2)
def generate_blog_article(self, force: bool = False) -> dict[str, Any]:
    """
    Fetch top news, write full article as Anton Merkulov, request Telegram approval.
    Beat schedule: every 3 days at 09:00 MSK.
    """
    from ...services.news_aggregator import fetch_top_news
    from ...services.article_writer import translate_and_write

    t0 = time.time()
    logger.info("blog_autogen: starting article generation")

    try:
        # 1. Fetch news
        news_items = fetch_top_news(limit=5)
        if not news_items:
            logger.warning("blog_autogen: no new relevant news found")
            return {"success": False, "reason": "no_new_news"}

        best = news_items[0]
        logger.info("blog_autogen: selected news '%s' from %s", best.title[:60], best.source)

        # 2. Write article
        result = translate_and_write(
            title=best.title,
            summary=best.summary,
            url=best.url,
            source_name=best.source,
        )

        if not result.success:
            logger.error("blog_autogen: article writing failed: %s", result.error)
            return {"success": False, "error": result.error}

        article_dict = {
            "title": result.title,
            "meta_description": result.meta_description,
            "slug": result.slug,
            "body_html": result.body_html,
            "tags": result.tags,
            "summary_tg": result.summary_tg,
            "summary_vk": result.summary_vk,
            "hook_instagram": result.hook_instagram,
            "source_url": result.source_url,
            "source_name": result.source_name,
        }

        # 3. Store in Redis for approval callback
        pending_key = hashlib.md5(result.slug.encode()).hexdigest()[:12]
        redis_key = f"{PENDING_KEY_PREFIX}{pending_key}"

        from ..celery_app import celery as celery_app
        redis_client = celery_app.backend.client if hasattr(celery_app.backend, "client") else None

        # Save pending article to Redis (72h TTL for approval window)
        if redis_client:
            redis_client.setex(redis_key, 259200, json.dumps(article_dict))

        # 4. Send Telegram approval or auto-publish
        auto_approved = _send_telegram_approval_request(article_dict, pending_key)

        if auto_approved:
            # Publish immediately (Telegram not configured or sent failed)
            article_url = _publish_to_blog(article_dict, result.source_url, result.source_name)
            _enqueue_social_posts(article_dict, article_url)
            return {
                "success": True,
                "auto_published": True,
                "article_url": article_url,
                "title": result.title,
                "processing_time": time.time() - t0,
            }

        return {
            "success": True,
            "pending_approval": True,
            "pending_key": pending_key,
            "title": result.title,
            "processing_time": time.time() - t0,
        }

    except Exception as e:
        logger.exception("blog_autogen: task failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300)
        return {"success": False, "error": str(e)}


@celery.task(bind=True, name="app.workers.tasks.blog_autogen.publish_approved_article")
def publish_approved_article(self, pending_key: str) -> dict[str, Any]:
    """
    Called by Telegram webhook callback when user clicks '✅ Опубликовать'.
    Reads article from Redis and publishes.
    """
    redis_key = f"{PENDING_KEY_PREFIX}{pending_key}"

    from ..celery_app import celery as celery_app
    redis_client = celery_app.backend.client if hasattr(celery_app.backend, "client") else None

    if not redis_client:
        return {"success": False, "error": "Redis unavailable"}

    raw = redis_client.get(redis_key)
    if not raw:
        return {"success": False, "error": "Article expired or not found"}

    article_dict = json.loads(raw)
    article_url = _publish_to_blog(article_dict, article_dict["source_url"], article_dict["source_name"])
    _enqueue_social_posts(article_dict, article_url)

    redis_client.delete(redis_key)

    logger.info("publish_approved_article: published '%s' → %s", article_dict["title"], article_url)
    return {"success": True, "article_url": article_url}
