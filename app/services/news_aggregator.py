"""
News aggregator service for SalesWhisper Crosspost.

Fetches tech news from foreign RSS sources, scores by relevance,
and deduplicates using the dedupe table.
"""

from __future__ import annotations

import hashlib
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ..core.logging import get_logger
from ..models.db import db_manager

logger = get_logger("services.news_aggregator")

# ---------------------------------------------------------------------------
# RSS sources — English-language tech/AI/automation/SaaS
# ---------------------------------------------------------------------------

RSS_SOURCES = [
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "tags": ["startup", "ai", "saas", "automation", "crm"],
    },
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "tags": ["ai", "research", "automation", "enterprise"],
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
        "tags": ["ai", "ml", "llm", "automation"],
    },
    {
        "name": "The Verge Tech",
        "url": "https://www.theverge.com/rss/index.xml",
        "tags": ["tech", "ai", "product", "software"],
    },
    {
        "name": "Hacker News Best",
        "url": "https://hnrss.org/best",
        "tags": ["engineering", "ai", "automation", "saas", "productivity"],
    },
    {
        "name": "InfoQ AI",
        "url": "https://feed.infoq.com/articles/",
        "tags": ["ai", "ml", "automation", "enterprise", "architecture"],
    },
]

# Keywords that signal relevance for saleswhisper.pro audience
RELEVANCE_KEYWORDS = [
    # AI / ML
    "artificial intelligence", "machine learning", "llm", "gpt", "chatgpt", "openai",
    "claude", "gemini", "neural network", "generative ai", "large language model",
    # Automation / Sales
    "automation", "crm", "sales automation", "lead generation", "pipeline",
    "workflow", "no-code", "low-code", "agentic", "ai agent",
    # SaaS / Business
    "saas", "b2b", "enterprise software", "productivity", "revenue", "conversion",
    "customer success", "growth hacking", "marketing automation",
    # Tech trends
    "rag", "vector database", "embedding", "fine-tuning", "prompt engineering",
]

MINIMUM_RELEVANCE_SCORE = 2  # Must match at least 2 keywords


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    title: str
    url: str
    summary: str
    source: str
    published_at: str
    relevance_score: int
    dedupe_key: str


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _dedupe_key(url: str) -> str:
    return "news:" + hashlib.md5(url.encode()).hexdigest()


def _score_relevance(title: str, summary: str) -> int:
    text = (title + " " + summary).lower()
    return sum(1 for kw in RELEVANCE_KEYWORDS if kw in text)


def _strip_html(raw: str) -> str:
    """Minimal HTML stripper using stdlib only (no bs4)."""
    import re
    clean = re.sub(r"<[^>]+>", " ", raw or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:800]


def _parse_rss_xml(xml_text: str, source_name: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed, return raw items."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            pub_date = item.findtext("pubDate") or ""
            if title and link:
                items.append({"title": title, "url": link, "summary": description, "published": pub_date})

        # Atom
        if not items:
            for entry in root.findall("atom:entry", ns) or root.findall("{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                updated_el = entry.find("{http://www.w3.org/2005/Atom}updated")
                title = (title_el.text or "").strip() if title_el is not None else ""
                link = (link_el.get("href") or "").strip() if link_el is not None else ""
                summary = _strip_html((summary_el.text or "") if summary_el is not None else "")
                pub = (updated_el.text or "") if updated_el is not None else ""
                if title and link:
                    items.append({"title": title, "url": link, "summary": summary, "published": pub})

    except ET.ParseError as e:
        logger.warning("RSS parse error for %s: %s", source_name, e)

    return items


def _is_already_seen(dedupe_key: str) -> bool:
    """Check if URL was already processed (dedupe table)."""
    try:
        from sqlalchemy import text as sql_text
        with db_manager.get_sync_session() as db:
            row = db.execute(
                sql_text("SELECT id FROM dedupe WHERE dedupe_key = :k AND expires_at > NOW()"),
                {"k": dedupe_key},
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.warning("Dedupe check failed: %s", e)
        return False


def _mark_as_seen(dedupe_key: str, url: str) -> None:
    """Insert dedupe record so the same URL is skipped next time."""
    try:
        from sqlalchemy import text as sql_text
        with db_manager.get_sync_session() as db:
            db.execute(
                sql_text(
                    """
                    INSERT INTO dedupe (id, dedupe_key, dedupe_type, entity_type, entity_id, metadata)
                    VALUES (:id, :key, 'news_url', 'news', :eid, :meta)
                    ON CONFLICT (dedupe_key) DO NOTHING
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "key": dedupe_key,
                    "eid": uuid.uuid4(),
                    "meta": '{"source": "news_aggregator"}',
                },
            )
            db.commit()
    except Exception as e:
        logger.warning("Dedupe insert failed: %s", e)


def fetch_top_news(limit: int = 5) -> list[NewsItem]:
    """
    Fetch and rank top relevant tech news from all RSS sources.
    Returns up to `limit` items, deduplicated and scored.
    """
    candidates: list[NewsItem] = []

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for source in RSS_SOURCES:
            try:
                resp = client.get(source["url"], headers={"User-Agent": "SalesWhisper-NewsBot/1.0"})
                resp.raise_for_status()
                raw_items = _parse_rss_xml(resp.text, source["name"])

                for item in raw_items[:20]:  # Check first 20 from each feed
                    score = _score_relevance(item["title"], item["summary"])
                    if score < MINIMUM_RELEVANCE_SCORE:
                        continue

                    dk = _dedupe_key(item["url"])
                    if _is_already_seen(dk):
                        continue

                    candidates.append(NewsItem(
                        title=item["title"],
                        url=item["url"],
                        summary=item["summary"],
                        source=source["name"],
                        published_at=item["published"],
                        relevance_score=score,
                        dedupe_key=dk,
                    ))

            except httpx.HTTPError as e:
                logger.warning("Failed to fetch RSS from %s: %s", source["name"], e)
            except Exception as e:
                logger.warning("Unexpected error fetching %s: %s", source["name"], e)

    # Sort by relevance score descending, then take top N
    candidates.sort(key=lambda x: x.relevance_score, reverse=True)
    top = candidates[:limit]

    # Mark selected items as seen
    for item in top:
        _mark_as_seen(item.dedupe_key, item.url)

    logger.info("news_aggregator: fetched %d candidates, returning %d", len(candidates), len(top))
    return top
