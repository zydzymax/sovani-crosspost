"""AI-powered content planner service for Crosspost.

Uses GPT o1 (reasoning model) for initial generation and Claude for review/refinement.
Two-stage pipeline ensures high quality content plans tailored to specific niches.

Pricing (per plan):
- o1-mini generation: ~$0.18 (2K input + 15K output)
- Claude Sonnet review: ~$0.09 (5K input + 5K output)
- Total cost: ~$0.27
- With 3x markup: ~$0.81 ($1.00 rounded)
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import httpx
import openai
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.content_planner")


class Tone(str, Enum):
    """Content tone options."""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    HUMOROUS = "humorous"
    EDUCATIONAL = "educational"
    INSPIRATIONAL = "inspirational"


class MediaType(str, Enum):
    """Media type for posts."""
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    TEXT_ONLY = "text_only"


@dataclass
class PlannedPost:
    """A single planned post."""
    date: str  # ISO format YYYY-MM-DD
    day_of_week: str
    time: str  # HH:MM
    topic: str
    caption_draft: str
    hashtags: list[str]
    platforms: list[str]
    media_type: MediaType
    image_prompt: str | None = None
    video_prompt: str | None = None
    call_to_action: str | None = None
    content_pillar: str | None = None  # Educational, Entertaining, Promotional, etc.
    engagement_hook: str | None = None  # First line hook for attention


@dataclass
class ContentPlanResult:
    """Result of content plan generation."""
    success: bool
    plan_id: str
    niche: str
    duration_days: int
    posts_per_day: int
    tone: str
    platforms: list[str]
    posts: list[PlannedPost]
    total_posts: int
    generation_cost_usd: float = 0.0
    error: str | None = None


# Pricing constants
CONTENT_PLAN_COSTS = {
    "base_cost_usd": 0.27,  # Cost of generation (o1-mini + Claude)
    "markup_multiplier": 3.0,  # 3x markup
    "price_per_plan_usd": 1.00,  # Final price (~92 RUB)
    "price_per_post_usd": 0.03,  # Additional posts beyond base
}


class ContentPlannerService:
    """
    AI-powered content planner using two-stage generation:

    Stage 1 (GPT o1-mini): Deep reasoning about niche, audience psychology,
                          content strategy, and post generation
    Stage 2 (Claude):     Review, improve, and ensure quality/consistency
    """

    # System prompt for o1-mini (GPT-5 reasoning model)
    # o1 models don't use system prompts, reasoning is done via user prompt
    O1_GENERATION_PROMPT = """Ты создаёшь контент-план для социальных сетей. Действуй как опытный контент-стратег.

## ТВОЯ ЗАДАЧА
Создать контент-план для ниши "{niche}" на {duration_days} дней ({posts_per_day} пост(ов) в день).

## АНАЛИЗ НИШИ
Перед созданием плана проанализируй:

1. **Целевая аудитория ниши "{niche}":**
   - Кто эти люди? (возраст, пол, интересы, боли)
   - Какие проблемы они хотят решить?
   - Что их мотивирует читать контент?
   - В какое время они активны?

2. **Контентные столпы (Content Pillars) для этой ниши:**
   - Образовательный контент (обучение, советы, гайды)
   - Развлекательный (мемы, истории, тренды)
   - Вовлекающий (опросы, вопросы, дискуссии)
   - Продающий (кейсы, отзывы, офферы)
   - Вдохновляющий (мотивация, успехи, цитаты)

3. **Специфика ниши "{niche}":**
   - Какие темы "заходят" в этой нише?
   - Какие форматы работают лучше?
   - Какие хэштеги популярны?
   - Какие триггеры цепляют аудиторию?

## ТРЕБОВАНИЯ К ПОСТАМ

**Тон контента:** {tone_description}

**Платформы:** {platforms}
{platform_limits}

**Структура каждого поста:**
1. **Hook (первая строка)** - цепляющее начало, которое останавливает скролл
2. **Value (ценность)** - полезная информация или эмоция
3. **CTA (призыв к действию)** - что делать дальше

**Чередование типов контента:**
- Не ставь 2 продающих поста подряд
- Чередуй форматы (вопросы, советы, истории, факты)
- Используй разные медиа-типы

## ФОРМАТ ОТВЕТА

Верни JSON строго в формате:
```json
{{
  "niche_analysis": {{
    "target_audience": "описание ЦА",
    "main_pain_points": ["боль1", "боль2"],
    "content_pillars": ["столп1", "столп2", "столп3"]
  }},
  "posts": [
    {{
      "date": "YYYY-MM-DD",
      "time": "HH:MM",
      "day_of_week": "День недели",
      "topic": "Краткая тема поста",
      "content_pillar": "educational|entertaining|engaging|promotional|inspirational",
      "engagement_hook": "Цепляющая первая строка поста",
      "caption_draft": "Полный текст поста с эмодзи и форматированием",
      "hashtags": ["хэштег1", "хэштег2"],
      "platforms": ["instagram", "telegram"],
      "media_type": "image|video|carousel|text_only",
      "image_prompt": "Detailed English prompt for AI image generation",
      "call_to_action": "Призыв к действию"
    }}
  ]
}}
```

## ВАЖНЫЕ ПРАВИЛА

1. Всего должно быть РОВНО {total_posts} постов
2. Даты идут последовательно с {start_date}
3. Для image_prompt пиши на АНГЛИЙСКОМ детальное описание
4. Хэштеги релевантны теме и нише
5. Caption адаптирован под тон "{tone}"
6. Чередуй content_pillar для разнообразия

Создай план:"""

    # Claude review prompt
    CLAUDE_REVIEW_PROMPT = """Ты - эксперт по контент-маркетингу. Проверь и улучши этот контент-план.

## КОНТЕНТ-ПЛАН ДЛЯ ПРОВЕРКИ

Ниша: {niche}
Тон: {tone}
Платформы: {platforms}

{plan_json}

## ЗАДАЧА

Проверь каждый пост на:

1. **Качество hook (первой строки):**
   - Цепляет ли внимание?
   - Создаёт ли интригу/любопытство?
   - Если слабый - улучши

2. **Релевантность нише:**
   - Соответствует ли пост тематике "{niche}"?
   - Понятен ли контент целевой аудитории?

3. **Баланс типов контента:**
   - Не слишком ли много продающих постов подряд?
   - Есть ли разнообразие форматов?

4. **Качество image_prompt:**
   - Достаточно ли детальный для генерации?
   - На английском?

5. **Хэштеги:**
   - Релевантны теме?
   - Не слишком общие и не слишком узкие?

## ФОРМАТ ОТВЕТА

Верни исправленный JSON в том же формате, что и входной.
Если пост хороший - оставь как есть.
Если нужно улучшить - исправь и добавь в поле "improved": true.

Добавь в начало JSON поле:
"review_summary": {{
  "posts_improved": число улучшенных постов,
  "main_issues": ["проблема1", "проблема2"],
  "overall_quality": "excellent|good|needs_work"
}}

Верни ТОЛЬКО JSON без markdown-блоков и пояснений."""

    def __init__(self):
        """Initialize content planner with both OpenAI and Anthropic clients."""
        self.openai_key = self._get_openai_key()
        self.anthropic_key = self._get_anthropic_key()

        self.openai_client = openai.AsyncOpenAI(api_key=self.openai_key)

        logger.info("Content planner service initialized (o1 + Claude pipeline)")

    def _get_openai_key(self) -> str:
        """Get OpenAI API key."""
        if hasattr(settings, 'openai') and hasattr(settings.openai, 'api_key'):
            key = settings.openai.api_key
            if hasattr(key, 'get_secret_value'):
                return key.get_secret_value()
            return str(key)
        return os.getenv('OPENAI_API_KEY', '')

    def _get_anthropic_key(self) -> str:
        """Get Anthropic API key."""
        if hasattr(settings, 'anthropic') and hasattr(settings.anthropic, 'api_key'):
            key = settings.anthropic.api_key
            if hasattr(key, 'get_secret_value'):
                return key.get_secret_value()
            return str(key)
        return os.getenv('ANTHROPIC_API_KEY', '')

    async def generate_plan(
        self,
        niche: str,
        duration_days: int = 7,
        posts_per_day: int = 1,
        platforms: list[str] = None,
        tone: Tone = Tone.PROFESSIONAL,
        target_audience: str = None,
        brand_guidelines: str = None,
        exclude_topics: list[str] = None,
        preferred_posting_times: list[str] = None
    ) -> ContentPlanResult:
        """
        Generate content plan using two-stage AI pipeline.

        Stage 1: GPT o1-mini generates initial plan with deep reasoning
        Stage 2: Claude reviews and improves the plan

        Args:
            niche: Business niche/topic (e.g., "фитнес", "IT стартапы", "кулинария")
            duration_days: Plan duration in days (7, 14, 30)
            posts_per_day: Number of posts per day (1-3)
            platforms: Target platforms
            tone: Content tone
            target_audience: Description of target audience
            brand_guidelines: Brand voice guidelines
            exclude_topics: Topics to avoid
            preferred_posting_times: Preferred posting times

        Returns:
            ContentPlanResult with generated and reviewed posts
        """
        plan_id = str(uuid.uuid4())

        logger.info(
            "Starting content plan generation",
            plan_id=plan_id,
            niche=niche,
            duration_days=duration_days,
            posts_per_day=posts_per_day
        )

        if platforms is None:
            platforms = ["telegram", "instagram"]

        if preferred_posting_times is None:
            preferred_posting_times = ["10:00", "14:00", "19:00"]

        try:
            # Stage 1: Generate with o1-mini
            logger.info("Stage 1: Generating with o1-mini", plan_id=plan_id)
            raw_plan = await self._stage1_generate_with_o1(
                niche=niche,
                duration_days=duration_days,
                posts_per_day=posts_per_day,
                platforms=platforms,
                tone=tone,
                target_audience=target_audience,
                brand_guidelines=brand_guidelines,
                exclude_topics=exclude_topics,
                preferred_posting_times=preferred_posting_times
            )

            # Stage 2: Review with Claude
            logger.info("Stage 2: Reviewing with Claude", plan_id=plan_id)
            reviewed_plan = await self._stage2_review_with_claude(
                raw_plan=raw_plan,
                niche=niche,
                tone=tone,
                platforms=platforms
            )

            # Parse final posts
            posts = self._parse_posts(reviewed_plan, platforms, preferred_posting_times)

            # Calculate generation cost
            generation_cost = CONTENT_PLAN_COSTS["base_cost_usd"]

            logger.info(
                "Content plan generated successfully",
                plan_id=plan_id,
                total_posts=len(posts),
                cost_usd=generation_cost
            )

            return ContentPlanResult(
                success=True,
                plan_id=plan_id,
                niche=niche,
                duration_days=duration_days,
                posts_per_day=posts_per_day,
                tone=tone.value,
                platforms=platforms,
                posts=posts,
                total_posts=len(posts),
                generation_cost_usd=generation_cost
            )

        except Exception as e:
            logger.error(f"Failed to generate content plan: {e}", exc_info=True)
            return ContentPlanResult(
                success=False,
                plan_id=plan_id,
                niche=niche,
                duration_days=duration_days,
                posts_per_day=posts_per_day,
                tone=tone.value,
                platforms=platforms,
                posts=[],
                total_posts=0,
                error=str(e)
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _stage1_generate_with_o1(
        self,
        niche: str,
        duration_days: int,
        posts_per_day: int,
        platforms: list[str],
        tone: Tone,
        target_audience: str,
        brand_guidelines: str,
        exclude_topics: list[str],
        preferred_posting_times: list[str]
    ) -> dict[str, Any]:
        """Stage 1: Generate initial plan using o1-mini (GPT-5 reasoning)."""
        total_posts = duration_days * posts_per_day
        start_date = datetime.now()

        # Tone descriptions
        tone_descriptions = {
            Tone.PROFESSIONAL: "Профессиональный, экспертный - используй факты, статистику, кейсы. Избегай излишней эмоциональности.",
            Tone.CASUAL: "Дружелюбный, как разговор с другом. Используй разговорные выражения, эмодзи уместны.",
            Tone.HUMOROUS: "С юмором и иронией. Шутки, мемы, актуальные тренды. Но не переборщи - юмор должен быть уместным.",
            Tone.EDUCATIONAL: "Обучающий, познавательный. Пошаговые инструкции, объяснения, разбор сложных тем простым языком.",
            Tone.INSPIRATIONAL: "Вдохновляющий, мотивирующий. Истории успеха, цитаты, позитивные установки."
        }

        # Platform-specific limits
        platform_limits_text = "\n".join([
            "Лимиты платформ:",
            "- Telegram: до 4096 символов, до 10 хэштегов, поддержка Markdown",
            "- Instagram: до 2200 символов, до 30 хэштегов, хэштеги в конце",
            "- VK: до 15000 символов, до 10 хэштегов",
            "- Facebook: до 63206 символов, до 30 хэштегов",
            "- TikTok: до 150 символов (!), до 5 хэштегов, очень короткий текст",
            "- YouTube: до 5000 символов описания, до 15 хэштегов",
            "- RuTube: до 5000 символов, до 20 хэштегов"
        ])

        # Build prompt
        prompt = self.O1_GENERATION_PROMPT.format(
            niche=niche,
            duration_days=duration_days,
            posts_per_day=posts_per_day,
            total_posts=total_posts,
            start_date=start_date.strftime("%Y-%m-%d"),
            platforms=", ".join(platforms),
            platform_limits=platform_limits_text,
            tone=tone.value,
            tone_description=tone_descriptions.get(tone, tone.value)
        )

        # Add optional context
        if target_audience:
            prompt += f"\n\nЦелевая аудитория: {target_audience}"
        if brand_guidelines:
            prompt += f"\n\nБренд-гайдлайны: {brand_guidelines}"
        if exclude_topics:
            prompt += f"\n\nИсключить темы: {', '.join(exclude_topics)}"

        # Call o1-mini (reasoning model)
        # Note: o1 models use different API parameters
        try:
            response = await self.openai_client.chat.completions.create(
                model="o1-mini",  # GPT-5 reasoning model
                messages=[
                    {"role": "user", "content": prompt}
                ],
                # o1 models don't support temperature, max_tokens works differently
                max_completion_tokens=16000
            )
        except Exception as e:
            # Fallback to gpt-4-turbo if o1 not available
            logger.warning(f"o1-mini not available, falling back to gpt-4-turbo: {e}")
            response = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=8000,
                response_format={"type": "json_object"}
            )

        content = response.choices[0].message.content

        # Extract JSON from response (o1 might include reasoning text)
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            json_str = content[json_start:json_end]
            return json.loads(json_str)

        return json.loads(content)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _stage2_review_with_claude(
        self,
        raw_plan: dict[str, Any],
        niche: str,
        tone: Tone,
        platforms: list[str]
    ) -> dict[str, Any]:
        """Stage 2: Review and improve plan using Claude."""

        if not self.anthropic_key:
            logger.warning("Anthropic API key not set, skipping Claude review")
            return raw_plan

        prompt = self.CLAUDE_REVIEW_PROMPT.format(
            niche=niche,
            tone=tone.value,
            platforms=", ".join(platforms),
            plan_json=json.dumps(raw_plan, ensure_ascii=False, indent=2)
        )

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",  # Claude Sonnet 4
                        "max_tokens": 8000,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ]
                    }
                )

                if response.status_code != 200:
                    logger.warning(f"Claude API error: {response.status_code}, using raw plan")
                    return raw_plan

                result = response.json()
                content = result["content"][0]["text"]

                # Extract JSON
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start != -1 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    reviewed = json.loads(json_str)

                    # Log review summary
                    if "review_summary" in reviewed:
                        summary = reviewed["review_summary"]
                        logger.info(
                            "Claude review completed",
                            posts_improved=summary.get("posts_improved", 0),
                            quality=summary.get("overall_quality", "unknown")
                        )

                    return reviewed

                return json.loads(content)

        except Exception as e:
            logger.warning(f"Claude review failed, using raw plan: {e}")
            return raw_plan

    def _parse_posts(
        self,
        plan_data: dict[str, Any],
        platforms: list[str],
        preferred_posting_times: list[str]
    ) -> list[PlannedPost]:
        """Parse plan data into PlannedPost objects."""
        posts_data = plan_data.get("posts", plan_data.get("plan", []))
        if isinstance(posts_data, dict):
            posts_data = posts_data.get("posts", [])

        posts = []
        start_date = datetime.now()
        days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

        for i, post_data in enumerate(posts_data):
            try:
                # Calculate date if not provided
                if "date" not in post_data or not post_data["date"]:
                    day_offset = i // max(1, len(preferred_posting_times))
                    post_date = start_date + timedelta(days=day_offset)
                    post_data["date"] = post_date.strftime("%Y-%m-%d")

                # Get day of week
                if "day_of_week" not in post_data or not post_data["day_of_week"]:
                    post_date = datetime.strptime(post_data["date"], "%Y-%m-%d")
                    post_data["day_of_week"] = days_ru[post_date.weekday()]

                # Get time
                if "time" not in post_data or not post_data["time"]:
                    time_index = i % len(preferred_posting_times)
                    post_data["time"] = preferred_posting_times[time_index]

                # Parse media type
                media_type_str = post_data.get("media_type", "image").lower()
                try:
                    media_type = MediaType(media_type_str)
                except ValueError:
                    media_type = MediaType.IMAGE

                post = PlannedPost(
                    date=post_data["date"],
                    day_of_week=post_data.get("day_of_week", ""),
                    time=post_data.get("time", "12:00"),
                    topic=post_data.get("topic", ""),
                    caption_draft=post_data.get("caption_draft", post_data.get("caption", "")),
                    hashtags=post_data.get("hashtags", []),
                    platforms=post_data.get("platforms", platforms),
                    media_type=media_type,
                    image_prompt=post_data.get("image_prompt"),
                    video_prompt=post_data.get("video_prompt"),
                    call_to_action=post_data.get("call_to_action"),
                    content_pillar=post_data.get("content_pillar"),
                    engagement_hook=post_data.get("engagement_hook")
                )
                posts.append(post)

            except Exception as e:
                logger.warning(f"Failed to parse post {i}: {e}")
                continue

        return posts

    async def regenerate_post(
        self,
        post: PlannedPost,
        feedback: str = None,
        niche: str = ""
    ) -> PlannedPost:
        """Regenerate a single post with optional feedback."""
        prompt = f"""Перегенерируй этот пост для ниши "{niche}".

Текущий пост:
- Тема: {post.topic}
- Content pillar: {post.content_pillar}
- Caption: {post.caption_draft}
- Hashtags: {', '.join(post.hashtags)}
- Media type: {post.media_type.value}

{f'Обратная связь пользователя: {feedback}' if feedback else 'Сделай пост более цепляющим и вовлекающим. Улучши hook (первую строку).'}

Верни JSON:
{{
  "topic": "новая тема",
  "engagement_hook": "цепляющая первая строка",
  "caption_draft": "полный текст поста",
  "hashtags": ["#хэштег1", "#хэштег2"],
  "media_type": "image",
  "image_prompt": "English prompt for image",
  "call_to_action": "призыв к действию",
  "content_pillar": "educational|entertaining|engaging|promotional|inspirational"
}}"""

        response = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)

        return PlannedPost(
            date=post.date,
            day_of_week=post.day_of_week,
            time=post.time,
            topic=data.get("topic", post.topic),
            caption_draft=data.get("caption_draft", post.caption_draft),
            hashtags=data.get("hashtags", post.hashtags),
            platforms=post.platforms,
            media_type=MediaType(data.get("media_type", post.media_type.value)),
            image_prompt=data.get("image_prompt", post.image_prompt),
            video_prompt=data.get("video_prompt", post.video_prompt),
            call_to_action=data.get("call_to_action", post.call_to_action),
            content_pillar=data.get("content_pillar", post.content_pillar),
            engagement_hook=data.get("engagement_hook", post.engagement_hook)
        )

    async def adapt_for_platform(
        self,
        caption: str,
        source_platform: str,
        target_platform: str
    ) -> str:
        """Adapt caption from one platform to another."""
        platform_instructions = {
            "telegram": "Длинный текст OK, используй **bold** и _italic_ Markdown. Можно добавить разбиение на абзацы.",
            "instagram": "Макс 2200 символов, хэштеги в самом конце отдельным блоком. Эмодзи приветствуются.",
            "vk": "Длинный текст OK, используй эмодзи умеренно. Можно добавить опрос или голосование.",
            "facebook": "Развернутый текст, призыв к обсуждению в комментариях. Вопрос в конце.",
            "tiktok": "МАКСИМУМ 150 символов! Очень короткий, цепляющий текст. Только суть.",
            "youtube": "Описание для видео с таймкодами. Ключевые слова для поиска.",
            "rutube": "Описание для видео, ключевые слова на русском."
        }

        prompt = f"""Адаптируй этот текст с {source_platform} для {target_platform}.

Исходный текст:
{caption}

Требования для {target_platform}: {platform_instructions.get(target_platform, '')}

Верни ТОЛЬКО адаптированный текст без пояснений."""

        response = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500
        )

        return response.choices[0].message.content.strip()


# Global instance
content_planner = ContentPlannerService()


# Convenience function
async def generate_content_plan(
    niche: str,
    duration_days: int = 7,
    posts_per_day: int = 1,
    platforms: list[str] = None,
    tone: str = "professional"
) -> ContentPlanResult:
    """Generate content plan using two-stage AI pipeline."""
    tone_enum = Tone(tone) if tone in [t.value for t in Tone] else Tone.PROFESSIONAL
    return await content_planner.generate_plan(
        niche=niche,
        duration_days=duration_days,
        posts_per_day=posts_per_day,
        platforms=platforms,
        tone=tone_enum
    )


def get_content_plan_price(duration_days: int = 7, posts_per_day: int = 1) -> dict[str, float]:
    """
    Calculate content plan price.

    Base plan (7 days, 1 post/day) = $1.00 (~92 RUB)
    Additional posts beyond 7 = $0.03 each

    Market comparison:
    - Competitors: 500-3000 RUB per plan
    - Our price: 92-300 RUB (competitive)
    """
    base_posts = 7
    total_posts = duration_days * posts_per_day

    base_price = CONTENT_PLAN_COSTS["price_per_plan_usd"]
    extra_posts = max(0, total_posts - base_posts)
    extra_cost = extra_posts * CONTENT_PLAN_COSTS["price_per_post_usd"]

    total_usd = base_price + extra_cost
    total_rub = total_usd * 92  # USD to RUB

    return {
        "total_posts": total_posts,
        "price_usd": round(total_usd, 2),
        "price_rub": round(total_rub, 0),
        "cost_per_post_usd": round(total_usd / total_posts, 3),
        "market_comparison": "60-80% дешевле конкурентов"
    }
