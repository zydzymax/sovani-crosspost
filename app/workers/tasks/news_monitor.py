"""
News monitor task — daily short commentary reposts to all social platforms.

Beat schedule: daily at 08:00, 14:00, 20:00 Moscow time.
Fetches 1-2 top news items, generates 200-300 word expert commentary,
pushes through the crosspost pipeline.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from ...core.logging import get_logger
from ..celery_app import celery

logger = get_logger("tasks.news_monitor")


@celery.task(bind=True, name="app.workers.tasks.news_monitor.post_news_commentary", max_retries=2)
def post_news_commentary(self, limit: int = 2) -> dict[str, Any]:
    """
    Fetch top news, write short commentary, push to crosspost pipeline.
    """
    from ...services.news_aggregator import fetch_top_news
    from ...services.article_writer import write_short_commentary
    from .ingest import process_telegram_update

    t0 = time.time()
    logger.info("news_monitor: starting")

    try:
        news_items = fetch_top_news(limit=limit)
        if not news_items:
            logger.info("news_monitor: no new relevant news")
            return {"success": True, "posted": 0, "reason": "no_new_news"}

        posted = 0
        for item in news_items[:limit]:
            try:
                commentary = write_short_commentary(
                    title=item.title,
                    summary=item.summary,
                    url=item.url,
                    source_name=item.source,
                )

                tg_text = commentary.get("telegram", f"{item.title}\n{item.url}")

                post_id = str(uuid.uuid4())
                fake_update = {
                    "update_id": int(time.time()),
                    "message": {
                        "message_id": int(time.time()),
                        "text": tg_text,
                        "chat": {"id": 0, "type": "channel"},
                        "date": int(time.time()),
                    },
                }

                process_telegram_update.apply_async(
                    args=[fake_update, post_id],
                    queue="ingest",
                )
                logger.info("news_monitor: queued commentary for '%s'", item.title[:60])
                posted += 1

            except Exception as e:
                logger.warning("news_monitor: failed for '%s': %s", item.title[:60], e)

        return {"success": True, "posted": posted, "processing_time": time.time() - t0}

    except Exception as e:
        logger.exception("news_monitor: task failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=120)
        return {"success": False, "error": str(e)}
