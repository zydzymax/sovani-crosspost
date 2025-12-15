"""AI-powered content planner service for Crosspost.

Generates content plans based on niche, platforms, and posting frequency.
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import uuid

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
    date: str  # ISO format
    day_of_week: str
    time: str  # HH:MM
    topic: str
    caption_draft: str
    hashtags: List[str]
    platforms: List[str]
    media_type: MediaType
    image_prompt: Optional[str] = None
    video_prompt: Optional[str] = None
    call_to_action: Optional[str] = None


@dataclass
class ContentPlanResult:
    """Result of content plan generation."""
    success: bool
    plan_id: str
    niche: str
    duration_days: int
    posts_per_day: int
    tone: str
    platforms: List[str]
    posts: List[PlannedPost]
    total_posts: int
    error: Optional[str] = None


class ContentPlannerService:
    """AI-powered content planner."""

    # System prompt for GPT
    SYSTEM_PROMPT = """Ты - эксперт по контент-маркетингу и SMM.
Твоя задача - создавать контент-планы для социальных сетей.

Правила:
1. Каждый пост должен быть уникальным и интересным для целевой аудитории
2. Используй разнообразные форматы: вопросы, советы, истории, факты, лайфхаки
3. Hashtags должны быть релевантными и не более 5-7 штук
4. Caption должен быть адаптирован под tone of voice
5. Для image_prompt создавай детальные описания на английском
6. Учитывай особенности каждой платформы
7. Чередуй типы контента для разнообразия

Формат вывода - JSON array постов."""

    def __init__(self, openai_api_key: str = None):
        """Initialize content planner."""
        self.api_key = openai_api_key or self._get_api_key()
        self.client = openai.AsyncOpenAI(api_key=self.api_key)
        logger.info("Content planner service initialized")

    def _get_api_key(self) -> str:
        """Get OpenAI API key."""
        if hasattr(settings, 'openai') and hasattr(settings.openai, 'api_key'):
            key = settings.openai.api_key
            if hasattr(key, 'get_secret_value'):
                return key.get_secret_value()
            return str(key)

        import os
        key = os.getenv('OPENAI_API_KEY')
        if key:
            return key

        raise ValueError("OpenAI API key not configured")

    async def generate_plan(
        self,
        niche: str,
        duration_days: int = 7,
        posts_per_day: int = 1,
        platforms: List[str] = None,
        tone: Tone = Tone.PROFESSIONAL,
        target_audience: str = None,
        brand_guidelines: str = None,
        exclude_topics: List[str] = None,
        preferred_posting_times: List[str] = None
    ) -> ContentPlanResult:
        """
        Generate content plan using AI.

        Args:
            niche: Business niche/topic (e.g., "фитнес", "IT", "кулинария")
            duration_days: Plan duration in days (7, 14, 30)
            posts_per_day: Number of posts per day (1-3)
            platforms: Target platforms
            tone: Content tone
            target_audience: Description of target audience
            brand_guidelines: Brand voice guidelines
            exclude_topics: Topics to avoid
            preferred_posting_times: Preferred posting times

        Returns:
            ContentPlanResult with generated posts
        """
        plan_id = str(uuid.uuid4())

        logger.info(
            "Generating content plan",
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
            # Generate posts using GPT
            posts = await self._generate_posts(
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

            logger.info(
                "Content plan generated",
                plan_id=plan_id,
                total_posts=len(posts)
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
                total_posts=len(posts)
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
    async def _generate_posts(
        self,
        niche: str,
        duration_days: int,
        posts_per_day: int,
        platforms: List[str],
        tone: Tone,
        target_audience: str,
        brand_guidelines: str,
        exclude_topics: List[str],
        preferred_posting_times: List[str]
    ) -> List[PlannedPost]:
        """Generate posts using GPT-4."""
        total_posts = duration_days * posts_per_day
        start_date = datetime.now()

        # Build user prompt
        user_prompt = self._build_user_prompt(
            niche=niche,
            duration_days=duration_days,
            posts_per_day=posts_per_day,
            platforms=platforms,
            tone=tone,
            target_audience=target_audience,
            brand_guidelines=brand_guidelines,
            exclude_topics=exclude_topics,
            preferred_posting_times=preferred_posting_times,
            start_date=start_date,
            total_posts=total_posts
        )

        # Call GPT-4
        response = await self.client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.8,
            max_tokens=4000,
            response_format={"type": "json_object"}
        )

        # Parse response
        content = response.choices[0].message.content
        data = json.loads(content)

        posts_data = data.get("posts", data.get("plan", []))
        if isinstance(posts_data, dict):
            posts_data = posts_data.get("posts", [])

        # Convert to PlannedPost objects
        posts = []
        for i, post_data in enumerate(posts_data):
            try:
                # Calculate date if not provided
                if "date" not in post_data:
                    day_offset = i // posts_per_day
                    post_date = start_date + timedelta(days=day_offset)
                    post_data["date"] = post_date.strftime("%Y-%m-%d")

                # Get day of week
                if "day_of_week" not in post_data:
                    post_date = datetime.strptime(post_data["date"], "%Y-%m-%d")
                    days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    post_data["day_of_week"] = days_ru[post_date.weekday()]

                # Get time
                if "time" not in post_data:
                    time_index = i % len(preferred_posting_times)
                    post_data["time"] = preferred_posting_times[time_index]

                # Parse media type
                media_type_str = post_data.get("media_type", "image").lower()
                try:
                    media_type = MediaType(media_type_str)
                except:
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
                    call_to_action=post_data.get("call_to_action")
                )
                posts.append(post)

            except Exception as e:
                logger.warning(f"Failed to parse post {i}: {e}")
                continue

        return posts

    def _build_user_prompt(
        self,
        niche: str,
        duration_days: int,
        posts_per_day: int,
        platforms: List[str],
        tone: Tone,
        target_audience: str,
        brand_guidelines: str,
        exclude_topics: List[str],
        preferred_posting_times: List[str],
        start_date: datetime,
        total_posts: int
    ) -> str:
        """Build user prompt for GPT."""
        # Tone descriptions
        tone_descriptions = {
            Tone.PROFESSIONAL: "профессиональный, экспертный, деловой",
            Tone.CASUAL: "дружелюбный, неформальный, простой",
            Tone.HUMOROUS: "юмористический, развлекательный, с шутками",
            Tone.EDUCATIONAL: "образовательный, познавательный, обучающий",
            Tone.INSPIRATIONAL: "вдохновляющий, мотивирующий, позитивный"
        }

        # Platform limits for reference
        platform_limits = {
            "telegram": "4096 символов, до 10 хэштегов",
            "instagram": "2200 символов, до 30 хэштегов",
            "vk": "15000 символов, до 10 хэштегов",
            "facebook": "63206 символов, до 30 хэштегов",
            "tiktok": "150 символов, до 5 хэштегов",
            "youtube": "5000 символов (описание), до 15 хэштегов"
        }

        prompt_parts = [
            f"Создай контент-план на {duration_days} дней для ниши: {niche}",
            f"Количество постов в день: {posts_per_day}",
            f"Всего нужно создать {total_posts} постов",
            f"Начальная дата: {start_date.strftime('%Y-%m-%d')}",
            f"Платформы: {', '.join(platforms)}",
            f"Тон контента: {tone_descriptions.get(tone, tone.value)}",
            "",
            "Лимиты платформ:"
        ]

        for platform in platforms:
            if platform in platform_limits:
                prompt_parts.append(f"- {platform}: {platform_limits[platform]}")

        if target_audience:
            prompt_parts.append(f"\nЦелевая аудитория: {target_audience}")

        if brand_guidelines:
            prompt_parts.append(f"\nБренд-гайдлайны: {brand_guidelines}")

        if exclude_topics:
            prompt_parts.append(f"\nИсключить темы: {', '.join(exclude_topics)}")

        prompt_parts.extend([
            "",
            "Формат ответа - JSON:",
            """{
  "posts": [
    {
      "date": "YYYY-MM-DD",
      "time": "HH:MM",
      "topic": "Тема поста",
      "caption_draft": "Текст поста...",
      "hashtags": ["#хэштег1", "#хэштег2"],
      "platforms": ["instagram", "telegram"],
      "media_type": "image",
      "image_prompt": "Detailed English prompt for image generation",
      "call_to_action": "Призыв к действию"
    }
  ]
}"""
        ])

        return "\n".join(prompt_parts)

    async def regenerate_post(
        self,
        post: PlannedPost,
        feedback: str = None
    ) -> PlannedPost:
        """Regenerate a single post with optional feedback."""
        prompt = f"""Перегенерируй этот пост с учетом обратной связи.

Текущий пост:
- Тема: {post.topic}
- Caption: {post.caption_draft}
- Hashtags: {', '.join(post.hashtags)}
- Media type: {post.media_type.value}

{f'Обратная связь: {feedback}' if feedback else 'Сделай пост более интересным и вовлекающим.'}

Верни JSON с полями: topic, caption_draft, hashtags, media_type, image_prompt, call_to_action"""

        response = await self.client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
            max_tokens=1000,
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
            call_to_action=data.get("call_to_action", post.call_to_action)
        )

    async def adapt_for_platform(
        self,
        caption: str,
        source_platform: str,
        target_platform: str
    ) -> str:
        """Adapt caption from one platform to another."""
        platform_instructions = {
            "telegram": "Длинный текст OK, используй форматирование Markdown",
            "instagram": "Макс 2200 символов, хэштеги в конце",
            "vk": "Длинный текст OK, используй смайлики умеренно",
            "facebook": "Развернутый текст, призыв к обсуждению",
            "tiktok": "Максимум 150 символов, короткий и цепляющий",
            "youtube": "Описание для видео, ключевые слова"
        }

        prompt = f"""Адаптируй этот текст с {source_platform} для {target_platform}.

Исходный текст:
{caption}

Требования для {target_platform}: {platform_instructions.get(target_platform, '')}

Верни только адаптированный текст."""

        response = await self.client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "user", "content": prompt}
            ],
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
    platforms: List[str] = None,
    tone: str = "professional"
) -> ContentPlanResult:
    """Generate content plan."""
    tone_enum = Tone(tone) if tone in [t.value for t in Tone] else Tone.PROFESSIONAL
    return await content_planner.generate_plan(
        niche=niche,
        duration_days=duration_days,
        posts_per_day=posts_per_day,
        platforms=platforms,
        tone=tone_enum
    )
