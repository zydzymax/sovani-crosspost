"""
Article writer service for SalesWhisper blog.

Two-stage pipeline:
  1. Translation:  GPT-4o  (accurate, context-aware EN→RU)
  2. Adaptation:   Claude Sonnet 4.6  (writes like a human, not like AI about AI)

Why two models instead of one:
  - GPT-4o is better at strict instruction-following for pure translation tasks
  - Claude produces more natural Russian prose and adheres better to persona constraints
  - Separating the stages prevents the model from "interpreting" facts during translation
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import openai

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.article_writer")


# ---------------------------------------------------------------------------
# Persona — the creative core of the entire system
# ---------------------------------------------------------------------------

PERSONA_SYSTEM_PROMPT = """\
Ты Антон Меркулов — технологический обозреватель с 12-летним стажем. \
Пишешь для людей, которые строят бизнес, автоматизируют продажи и следят за рынком AI. \
Твоя аудитория: владельцы B2B-компаний, руководители отделов продаж, \
технари, которым скучно читать корпоративные пресс-релизы.

СТИЛЬ:
- Научно-публицистический, живой. Не новостная заметка, не академическая статья — \
  аналитическая колонка с позицией автора.
- Пишешь от первого лица: «я думаю», «по моей оценке», «если честно».
- Не боишься критиковать хайп. Если технология переоценена — говоришь прямо.
- Конкретика: названия компаний, реальные цифры, продукты, имена исследователей.
- НИКОГДА не пишешь: «в современном мире», «нельзя не отметить», «революционный прорыв», \
  «это меняет всё». Это клише — признак плохого текста.
- Первый абзац — крючок. Конкретный факт или провокационный тезис, без предисловий.
- Каждый раздел заканчивается выводом или открытым вопросом.
- Русский язык чистый, без канцеляризмов и без интернет-сленга.
- Иностранные термины оставляй как есть: LLM, RAG, fine-tuning, SaaS, API.
- Объём: 1800–2500 слов.

СТРУКТУРА:
1. Лид (2–3 абзаца): конкретный факт/событие + почему важно именно сейчас
2. Контекст: как мы пришли к текущей ситуации
3. Разбор: детали, механика, как это работает или не работает
4. Российский угол: что это значит для рынка РФ, есть ли аналоги, как применить
5. Позиция автора: короткий раздел с личной оценкой — без нейтральности

ЗАПРЕЩЕНО:
- Списки без пояснений в тексте
- «Таким образом, можно заключить...»
- Преимущественно пассивный залог
- Цифры без источника («по данным OpenAI», «согласно MIT»)
- ИИ в третьем лице как субъект прогресса («ИИ помогает компаниям расти» — плохо; \
  «GPT-4o сократил время обработки заявки в компании X с 4 часов до 7 минут» — хорошо)

ВЫВОД В ФОРМАТЕ JSON (только JSON, без пояснений вокруг):
{
  "title": "заголовок (до 80 символов, конкретный, без кликбейта)",
  "meta_description": "описание для SEO (120–155 символов)",
  "slug": "url-friendly-slug-na-latinice",
  "body_html": "<article>...полный HTML статьи с тегами h2, p, strong...</article>",
  "tags": ["тег1", "тег2", "тег3"],
  "summary_tg": "резюме для Telegram (200–280 символов, с позицией автора)",
  "summary_vk": "пост для ВКонтакте (400–600 символов, без ссылки)",
  "hook_instagram": "первые 150 символов для Instagram"
}
"""

TRANSLATION_SYSTEM_PROMPT = """\
Ты точный переводчик с английского на русский для технической аналитики. \
Задача — дословный точный перевод, не интерпретация. \
Сохраняй все факты, цифры, имена, названия компаний и продуктов без изменений. \
Не добавляй, не сокращай, не интерпретируй. \
Иностранные термины (LLM, RAG, SaaS, API, fine-tuning) оставляй как есть — \
не придумывай русские эквиваленты для устоявшихся технических терминов.
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ArticleResult:
    success: bool
    title: str = ""
    meta_description: str = ""
    slug: str = ""
    body_html: str = ""
    tags: list = None
    summary_tg: str = ""
    summary_vk: str = ""
    hook_instagram: str = ""
    source_url: str = ""
    source_name: str = ""
    error: str = ""
    translation_engine: str = "GPT-4o"
    writing_engine: str = ""

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# ---------------------------------------------------------------------------
# Stage 1: Translation via GPT-4o
# ---------------------------------------------------------------------------

def _translate(title: str, summary: str, url: str) -> str:
    """
    Accurate EN→RU translation via GPT-4o.
    Low temperature (0.1) — we want precision, not creativity here.
    """
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Переведи точно следующий материал с английского на русский.\n\n"
                f"ЗАГОЛОВОК: {title}\n\n"
                f"КРАТКОЕ СОДЕРЖАНИЕ:\n{summary}\n\n"
                f"ИСТОЧНИК: {url}\n\n"
                f"Дай полный точный перевод всего материала."
            )},
        ],
        temperature=0.1,
        max_tokens=2500,
    )
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Stage 2: Writing via Claude Sonnet → GPT-4o fallback
# ---------------------------------------------------------------------------

def _write_claude(translated: str, source_name: str, source_url: str) -> tuple[dict | None, str]:
    """Claude Sonnet 4.6 — most natural Russian writing, strong persona adherence."""
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return None, ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=5000,
            system=PERSONA_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Вот точный перевод материала из {source_name}:\n\n"
                    f"---\n{translated}\n---\n\n"
                    f"Источник оригинала: {source_url}\n\n"
                    "Напиши авторскую колонку в стиле Антона Меркулова на основе этих фактов. "
                    "Добавь российский контекст. Сошлись на источник в тексте. "
                    "Верни строго JSON-объект согласно формату из системного промпта."
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            return json.loads(json_match.group()), "Claude Sonnet 4.6"
    except Exception as e:
        logger.warning("Claude writing failed, falling back to GPT-4o: %s", e)

    return None, ""


def _write_gpt(translated: str, source_name: str, source_url: str) -> tuple[dict, str]:
    """GPT-4o fallback writing (if Anthropic key not set or Claude fails)."""
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PERSONA_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Вот точный перевод материала из {source_name}:\n\n"
                f"---\n{translated}\n---\n\n"
                f"Источник оригинала: {source_url}\n\n"
                "Напиши авторскую колонку в стиле Антона Меркулова. "
                "Добавь российский контекст. Сошлись на источник. "
                "Верни строго JSON-объект."
            )},
        ],
        temperature=0.75,
        max_tokens=5000,
    )
    raw = resp.choices[0].message.content.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        raise ValueError(f"No JSON in GPT response: {raw[:200]}")
    return json.loads(json_match.group()), "GPT-4o"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate_and_write(
    title: str,
    summary: str,
    url: str,
    source_name: str,
) -> ArticleResult:
    """
    Full pipeline: GPT-4o translates → Claude Sonnet (or GPT-4o) writes.
    Returns ArticleResult with all fields populated.
    """
    logger.info("article_writer: starting '%s' from %s", title[:60], source_name)
    t0 = time.time()

    try:
        # Stage 1: precise translation via GPT-4o
        translated = _translate(title, summary, url)
        logger.info("article_writer: GPT-4o translation done in %.1fs", time.time() - t0)

        # Stage 2: write as Anton Merkulov (Claude preferred)
        t1 = time.time()
        data, w_engine = _write_claude(translated, source_name, url)
        if not data:
            data, w_engine = _write_gpt(translated, source_name, url)
        logger.info("article_writer: %s writing done in %.1fs", w_engine, time.time() - t1)

        return ArticleResult(
            success=True,
            title=data.get("title", title),
            meta_description=data.get("meta_description", ""),
            slug=data.get("slug", "article"),
            body_html=data.get("body_html", ""),
            tags=data.get("tags", []),
            summary_tg=data.get("summary_tg", ""),
            summary_vk=data.get("summary_vk", ""),
            hook_instagram=data.get("hook_instagram", ""),
            source_url=url,
            source_name=source_name,
            translation_engine="GPT-4o",
            writing_engine=w_engine,
        )

    except Exception as e:
        logger.exception("article_writer: failed for '%s': %s", title[:60], e)
        return ArticleResult(success=False, title=title, source_url=url, source_name=source_name, error=str(e))


def write_short_commentary(title: str, summary: str, url: str, source_name: str) -> dict[str, str]:
    """
    Short expert commentary for news reposts (not a full article).
    GPT-4o-mini translates inline, Claude Haiku writes.
    Returns dict: telegram, vk, instagram.
    """
    import os

    user_prompt = (
        f"Вот новость из {source_name}:\n"
        f"Заголовок: {title}\n"
        f"Краткое содержание: {summary}\n"
        f"Ссылка: {url}\n\n"
        "Напиши короткий авторский комментарий в своём стиле. "
        "Не пересказывай — дай позицию и что это значит для B2B-рынка в России. "
        "200–300 слов.\n\n"
        'Верни JSON: {"telegram": "200–280 символов + ссылка", '
        '"vk": "400–600 символов + хэштеги", '
        '"instagram": "до 150 символов зацепка, потом 300 символов текст"}'
    )

    # Try Claude Haiku (fast + cheap for short content)
    api_key = os.environ.get("ANTHROPIC_API_KEY") or getattr(settings, "ANTHROPIC_API_KEY", None)
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=PERSONA_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = msg.content[0].text.strip()
            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning("Claude Haiku commentary failed: %s", e)

    # Fallback: GPT-4o-mini
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PERSONA_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=1000,
    )
    raw = resp.choices[0].message.content.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return {"telegram": f"{title}\n{url}", "vk": f"{title}\n{url}", "instagram": title}
    return json.loads(json_match.group())
