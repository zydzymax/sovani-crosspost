# 🛠️ Руководство для разработчиков

Полное руководство по разработке и работе с кодовой базой SoVAni Crosspost.

---

## 📚 Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Структура проекта](#структура-проекта)
3. [Ключевые концепции](#ключевые-концепции)
4. [Работа с кодом](#работа-с-кодом)
5. [Добавление новой платформы](#добавление-новой-платформы)
6. [Debugging](#debugging)
7. [Best Practices](#best-practices)

---

## 🚀 Быстрый старт

### Требования

- Python 3.11+
- Docker & Docker Compose
- Git
- IDE с поддержкой Python (VSCode, PyCharm)

### Установка окружения

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd sovani_crosspost

# 2. Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

# 3. Установить зависимости
pip install -r requirements.txt
pip install -r requirements-dev.txt  # dev зависимости

# 4. Настроить .env
cp .env.example .env
# Отредактировать .env с вашими значениями

# 5. Запустить инфраструктуру
docker-compose up -d postgres redis minio

# 6. Применить миграции
docker-compose exec postgres psql -U sovani -d sovani_crosspost -f /docker-entrypoint-initdb.d/0001_init.sql
```

### Запуск для разработки

```bash
# Терминал 1: FastAPI сервер
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Терминал 2: Celery worker
celery -A app.workers.celery_app worker --loglevel=info --concurrency=4

# Терминал 3: Celery beat (scheduler)
celery -A app.workers.celery_app beat --loglevel=info

# Или всё через docker-compose:
docker-compose up
```

### Проверка установки

```bash
# Health check
curl http://localhost:8000/health

# Swagger docs
open http://localhost:8000/docs

# MinIO console
open http://localhost:9001  # admin / minioadmin123
```

---

## 📁 Структура проекта

### Обзор директорий

```
sovani_crosspost/
├── app/                      # Основной код приложения
│   ├── adapters/            # Интеграции с внешними API
│   ├── api/                 # FastAPI endpoints
│   ├── core/                # Конфигурация, безопасность, логирование
│   ├── media/               # Обработка медиа (FFmpeg)
│   ├── models/              # SQLAlchemy модели и repositories
│   ├── observability/       # Метрики, трейсинг
│   ├── services/            # Бизнес-логика
│   ├── workers/             # Celery tasks
│   └── main.py              # Точка входа FastAPI
│
├── config/                  # Конфигурационные файлы
├── docs/                    # Документация
├── helpers/                 # Bash скрипты (FFmpeg профили)
├── migrations/              # SQL миграции
├── tests/                   # Тесты
│
├── docker-compose.yml       # Оркестрация сервисов
├── Dockerfile               # API контейнер
├── Dockerfile.worker        # Worker контейнер
└── requirements.txt         # Python зависимости
```

### Ключевые модули

#### `app/adapters/` - Адаптеры платформ

Каждый адаптер - это изолированный модуль для работы с API платформы:

```python
app/adapters/
├── telegram.py      # ✅ Готов (1024 строки)
├── instagram.py     # ✅ Готов (812 строк)
├── tiktok.py        # ✅ Готов (788 строк)
├── vk.py            # ✅ Готов (726 строк)
├── youtube.py       # ❌ ПУСТО (нужно реализовать!)
└── storage_s3.py    # ⚠️ Stub (нужно доделать)
```

**Паттерн адаптера:**
```python
class PlatformAdapter:
    def __init__(self):
        self.api_base = "https://api.platform.com"
        self.http_client = httpx.AsyncClient()

    async def publish_post(self, post: PostData) -> PublishResult:
        # Реализация публикации
        pass

    async def upload_media(self, media: MediaFile) -> UploadResult:
        # Реализация загрузки медиа
        pass
```

#### `app/workers/tasks/` - Celery задачи

7 специализированных очередей для обработки контента:

```python
app/workers/tasks/
├── ingest.py       # Прием контента из Telegram
├── enrich.py       # Обогащение данными о продукте
├── captionize.py   # AI-генерация описаний
├── transcode.py    # Транскодирование медиа
├── preflight.py    # Валидация перед публикацией
├── publish.py      # Публикация на платформах
└── finalize.py     # Уведомления и cleanup
```

**Паттерн task:**
```python
@celery.task(bind=True, name="app.workers.tasks.stage.task_name")
def process_stage(self, stage_data: Dict[str, Any]) -> Dict[str, Any]:
    task_start = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting stage", post_id=post_id)

        try:
            # Обработка
            result = do_work(stage_data)

            # Запуск следующей стадии
            from .next_stage import next_task
            next_task.delay({**stage_data, **result})

            return {"success": True, ...}
        except Exception as e:
            logger.error("Stage failed", error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60)
            raise
```

#### `app/models/` - Модели данных

**⚠️ ВНИМАНИЕ:** Модели пока не реализованы! `entities.py` практически пустой.

```python
app/models/
├── entities.py      # ❌ ПУСТО - нужно создать все модели!
├── repositories.py  # ❌ Нет - нужен repository pattern
├── enums.py         # ❌ Нет - enum типы
└── db.py            # ✅ Есть подключение к БД
```

**Нужно реализовать:**
- Product, MediaAsset, Rendition, Post, Account, Task, Log

#### `app/services/` - Бизнес-логика

```python
app/services/
├── caption_llm.py        # ✅ Готов - AI генерация
├── enrichment.py         # ✅ Готов - обогащение товарами
├── preflight_rules.py    # ✅ Готов - валидация (48KB!)
├── notifier.py           # ❌ ПУСТО
├── outbox.py             # ❌ ПУСТО
└── scheduler.py          # ❌ ПУСТО
```

---

## 🔑 Ключевые концепции

### 1. Pipeline обработки

Каждый пост проходит через 7 стадий:

```
INGEST → ENRICH → CAPTIONIZE → TRANSCODE → PREFLIGHT → PUBLISH → FINALIZE
```

**Как работает:**
1. Task получает `stage_data` dict
2. Обрабатывает данные
3. Запускает следующий task с обновленным `stage_data`
4. Логирует результат в БД

**Пример stage_data:**
```python
{
    "post_id": "uuid-123",
    "source": "telegram",
    "platforms": ["instagram", "vk"],
    "media_count": 1,
    "text_content": "Новая коллекция",
    # ... accumulating data from each stage
}
```

### 2. Celery Queue Priorities

```python
QUEUE_PRIORITIES = {
    "ingest": 9,      # Самый высокий
    "enrich": 8,
    "captionize": 7,
    "transcode": 6,
    "preflight": 5,
    "publish": 4,
    "finalize": 3
}
```

**Rate Limits** для API вызовов:
```python
"ingest": "10/s",      # 10 tasks per second
"enrich": "5/s",
"captionize": "3/s",   # LLM API ограничения
"transcode": "2/s",    # CPU-intensive
"publish": "1/s"       # External API limits
```

### 3. Aspect Ratio Management

Система создает 4 версии каждого медиа:

```python
ASPECT_RATIOS = {
    "9:16": ["tiktok", "instagram_story"],     # Vertical
    "4:5": ["instagram_feed"],                 # Portrait
    "1:1": ["instagram_square", "vk"],         # Square
    "16:9": ["youtube", "vk_horizontal"]       # Landscape
}
```

**FFmpeg обработка:**
```bash
# 9:16 (1080x1920) - pad strategy
ffmpeg -i input.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k output_9x16.mp4

# 4:5 (1080x1350) - для Instagram feed
ffmpeg -i input.mp4 -vf "scale=1080:1350:force_original_aspect_ratio=decrease,pad=1080:1350:(ow-iw)/2:(oh-ih)/2:black" ...

# И так далее для каждого соотношения
```

### 4. Error Handling Pattern

**Retry логика с exponential backoff:**

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((httpx.RequestError, RateLimitError))
)
async def api_call(self, ...):
    try:
        response = await self.http_client.post(...)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:  # Rate limit
            raise RateLimitError()
        raise
```

**В Celery tasks:**

```python
try:
    result = process()
    return result
except TemporaryError as e:
    # Retriable error
    if self.request.retries < self.max_retries:
        logger.warning(f"Retry {self.request.retries + 1}/{self.max_retries}")
        raise self.retry(countdown=60 * (self.request.retries + 1))
    else:
        # Max retries exceeded
        notify_failure(self.request.id, str(e))
        raise
except PermanentError as e:
    # Non-retriable error
    notify_failure(self.request.id, str(e))
    raise
```

### 5. Structured Logging

**Всегда используй контекст:**

```python
from app.core.logging import get_logger, with_logging_context

logger = get_logger("module_name")

# В task или функции:
with with_logging_context(task_id=task_id, post_id=post_id):
    logger.info(
        "Processing started",
        platform="instagram",
        media_count=3,
        estimated_duration="5min"
    )

    # Все логи внутри будут иметь task_id и post_id
    logger.error("Upload failed", error=str(e), retry_count=2)
```

**Логи выглядят так:**
```json
{
  "timestamp": "2025-01-20T10:30:45.123Z",
  "level": "INFO",
  "logger": "tasks.publish",
  "message": "Processing started",
  "task_id": "abc-123",
  "post_id": "post-456",
  "platform": "instagram",
  "media_count": 3,
  "estimated_duration": "5min"
}
```

### 6. Configuration Management

**Pydantic Settings:**

```python
from app.core.config import settings

# Доступ к настройкам:
token = settings.telegram.bot_token  # SecretStr автоматически
db_url = settings.get_database_url()
redis_url = settings.get_redis_url(db=1)

# В коде адаптера:
class InstagramAdapter:
    def __init__(self):
        self.access_token = settings.social_media.meta_access_token.get_secret_value()
        self.business_account_id = settings.social_media.meta_app_id
```

**Environment variables:**
```bash
# Читаются автоматически из .env
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
VK_SERVICE_TOKEN=vk1.a....
```

---

## 💻 Работа с кодом

### Добавление нового endpoint

```python
# app/api/routes.py

from fastapi import APIRouter, Depends, HTTPException
from app.models.entities import Post
from app.core.logging import get_logger

router = APIRouter(prefix="/api/v1", tags=["posts"])
logger = get_logger("api.posts")

@router.post("/posts")
async def create_post(
    post_data: PostCreateRequest,
    db: Session = Depends(get_db)
):
    """Создать новый пост."""
    logger.info("Creating new post", platforms=post_data.platforms)

    try:
        # Валидация
        if not post_data.media_url and not post_data.text:
            raise HTTPException(400, "Media or text required")

        # Создание в БД
        post = Post(
            source_type=post_data.source_type,
            source_data=post_data.dict(),
            status=PostStatus.DRAFT
        )
        db.add(post)
        db.commit()

        # Запуск обработки
        from app.workers.tasks.ingest import process_telegram_update
        task = process_telegram_update.delay(post_data.dict(), str(post.id))

        return {
            "id": str(post.id),
            "status": "processing",
            "task_id": task.id
        }

    except Exception as e:
        logger.error("Failed to create post", error=str(e))
        raise HTTPException(500, "Internal error")
```

### Добавление нового Celery task

```python
# app/workers/tasks/my_stage.py

from ..celery_app import celery
from app.core.logging import get_logger, with_logging_context
from app.observability.metrics import metrics

logger = get_logger("tasks.my_stage")

@celery.task(bind=True, name="app.workers.tasks.my_stage.process")
def process_my_stage(self, stage_data: Dict[str, Any]) -> Dict[str, Any]:
    """My processing stage."""
    start_time = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting my stage", post_id=post_id)

        try:
            # Your processing logic
            result = do_something(stage_data)

            processing_time = time.time() - start_time

            # Track metrics
            metrics.track_celery_task(
                "my_stage",
                "my_queue",
                "success",
                processing_time
            )

            # Trigger next stage
            from .next_stage import next_task
            next_task.delay({**stage_data, "my_result": result})

            logger.info("Stage completed", processing_time=processing_time)

            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time
            }

        except Exception as e:
            logger.error("Stage failed", error=str(e), exc_info=True)

            # Retry logic
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise
```

**Регистрация queue в celery_app.py:**

```python
# app/workers/celery_app.py

task_routes={
    'app.workers.tasks.my_stage.*': {
        'queue': 'my_queue',
        'priority': 7,
        'rate_limit': '5/s'
    },
}

task_queues=[
    Queue('my_queue', Exchange('my_queue', type='direct'), routing_key='my_queue'),
]
```

---

## 🆕 Добавление новой платформы

Пошаговое руководство по добавлению поддержки новой социальной сети.

### Шаг 1: Создать adapter файл

```python
# app/adapters/new_platform.py

import asyncio
import httpx
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("adapters.new_platform")


class NewPlatformError(Exception):
    """Base exception for platform errors."""
    pass


@dataclass
class PlatformPost:
    """Represents a post for the platform."""
    caption: str
    media_urls: List[str]
    hashtags: List[str]
    scheduled_at: Optional[datetime] = None


@dataclass
class PublishResult:
    """Result of post publishing."""
    success: bool
    platform_post_id: Optional[str]
    platform_url: Optional[str]
    error_message: Optional[str] = None


class NewPlatformAdapter:
    """Adapter for New Platform API."""

    def __init__(self):
        self.api_base = "https://api.newplatform.com/v1"
        self.access_token = self._get_access_token()
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "SoVAni-Crosspost/1.0"
            }
        )

        # Rate limiting
        self.rate_limit_per_second = 10
        self.last_request_times = []
        self.rate_limit_lock = asyncio.Lock()

        logger.info("New Platform adapter initialized")

    def _get_access_token(self) -> str:
        """Get access token from settings."""
        if hasattr(settings, 'new_platform') and hasattr(settings.new_platform, 'access_token'):
            token = settings.new_platform.access_token
            if hasattr(token, 'get_secret_value'):
                return token.get_secret_value()
            return str(token)
        raise NewPlatformError("Access token not configured")

    async def publish_post(self, post: PlatformPost, correlation_id: str = None) -> PublishResult:
        """Publish post to platform."""
        with with_logging_context(correlation_id=correlation_id):
            logger.info("Publishing post", caption_length=len(post.caption))

            try:
                # 1. Upload media
                media_ids = []
                for media_url in post.media_urls:
                    media_id = await self._upload_media(media_url)
                    media_ids.append(media_id)

                # 2. Create post
                response = await self._make_api_request(
                    "POST",
                    "/posts",
                    json={
                        "caption": post.caption,
                        "media_ids": media_ids,
                        "hashtags": post.hashtags,
                        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None
                    }
                )

                post_id = response["id"]
                post_url = f"https://newplatform.com/post/{post_id}"

                logger.info("Post published successfully", post_id=post_id)

                return PublishResult(
                    success=True,
                    platform_post_id=post_id,
                    platform_url=post_url
                )

            except Exception as e:
                logger.error("Failed to publish post", error=str(e))
                return PublishResult(
                    success=False,
                    platform_post_id=None,
                    platform_url=None,
                    error_message=str(e)
                )

    async def _upload_media(self, media_url: str) -> str:
        """Upload media file."""
        # Implementation
        pass

    async def _make_api_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make API request with rate limiting."""
        await self._check_rate_limits()

        url = f"{self.api_base}{endpoint}"

        try:
            response = await self.http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise NewPlatformError("Rate limit exceeded")
            raise

    async def _check_rate_limits(self):
        """Check and enforce rate limits."""
        async with self.rate_limit_lock:
            current_time = time.time()
            self.last_request_times = [
                t for t in self.last_request_times
                if current_time - t < 1.0
            ]

            if len(self.last_request_times) >= self.rate_limit_per_second:
                wait_time = 1.0 - (current_time - min(self.last_request_times))
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self.last_request_times.append(time.time())

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()


# Global instance
new_platform_adapter = NewPlatformAdapter()


# Convenience function
async def publish_to_new_platform(
    caption: str,
    media_urls: List[str],
    hashtags: List[str] = None,
    correlation_id: str = None
) -> PublishResult:
    """Convenience function to publish."""
    post = PlatformPost(
        caption=caption,
        media_urls=media_urls,
        hashtags=hashtags or []
    )
    return await new_platform_adapter.publish_post(post, correlation_id)
```

### Шаг 2: Добавить в config

```python
# app/core/config.py

class NewPlatformConfig(BaseSettings):
    """New Platform configuration."""
    access_token: SecretStr = Field(env="NEW_PLATFORM_ACCESS_TOKEN")
    client_id: str = Field(env="NEW_PLATFORM_CLIENT_ID")

    class Config:
        env_prefix = "NEW_PLATFORM_"

class Settings:
    def __init__(self):
        # ... existing configs
        self.new_platform = NewPlatformConfig()
```

### Шаг 3: Обновить publish task

```python
# app/workers/tasks/publish.py

# Добавить импорт
from ...adapters.new_platform import publish_to_new_platform

# В функции publish_to_platforms:
if "new_platform" in platforms:
    result = await publish_to_new_platform(
        caption=post_data["caption"],
        media_urls=post_data["media_urls"],
        hashtags=post_data["hashtags"],
        correlation_id=correlation_id
    )
    publish_results["new_platform"] = result.dict()
```

### Шаг 4: Добавить в .env

```bash
# .env

NEW_PLATFORM_ACCESS_TOKEN=your_token_here
NEW_PLATFORM_CLIENT_ID=your_client_id
```

### Шаг 5: Добавить тесты

```python
# tests/adapters/test_new_platform.py

import pytest
from app.adapters.new_platform import NewPlatformAdapter, PlatformPost

@pytest.mark.asyncio
async def test_publish_post():
    adapter = NewPlatformAdapter()

    post = PlatformPost(
        caption="Test post",
        media_urls=["https://example.com/image.jpg"],
        hashtags=["#test"]
    )

    result = await adapter.publish_post(post)

    assert result.success is True
    assert result.platform_post_id is not None

@pytest.mark.asyncio
async def test_rate_limiting():
    adapter = NewPlatformAdapter()

    # Make multiple requests
    tasks = [adapter._make_api_request("GET", "/test") for _ in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Should not hit rate limits
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 0
```

---

## 🐛 Debugging

### Debugging Celery Tasks

**1. Запустить task синхронно (без Celery):**

```python
from app.workers.tasks.ingest import process_telegram_update

# Вместо .delay() используй .apply()
result = process_telegram_update.apply(
    args=[update_data, post_id]
).get()

print(result)
```

**2. Celery в debug режиме:**

```bash
# Запустить worker с одним процессом и DEBUG логами
celery -A app.workers.celery_app worker --loglevel=DEBUG --concurrency=1 --pool=solo
```

**3. Посмотреть активные tasks:**

```bash
docker-compose exec worker celery -A app.workers.celery_app inspect active
docker-compose exec worker celery -A app.workers.celery_app inspect reserved
```

**4. Очистить очереди:**

```bash
docker-compose exec worker celery -A app.workers.celery_app purge
```

### Debugging FastAPI

**1. Breakpoints в VSCode:**

`.vscode/launch.json`:
```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "FastAPI",
            "type": "python",
            "request": "launch",
            "module": "uvicorn",
            "args": [
                "app.main:app",
                "--reload",
                "--host", "0.0.0.0",
                "--port", "8000"
            ],
            "jinja": true,
            "justMyCode": false
        }
    ]
}
```

**2. Request logging:**

```python
# app/main.py

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    logger.info(
        "Request started",
        method=request.method,
        url=str(request.url),
        client=request.client.host
    )

    response = await call_next(request)

    duration = time.time() - start_time
    logger.info(
        "Request completed",
        status_code=response.status_code,
        duration=duration
    )

    return response
```

### Debugging Database

**1. Включить SQL echo:**

```python
# .env
DB_ECHO_SQL=true
```

**2. Подключиться к БД напрямую:**

```bash
docker-compose exec postgres psql -U sovani -d sovani_crosspost

# SQL команды:
\dt                    # Список таблиц
\d+ posts              # Описание таблицы
SELECT * FROM posts;   # Запрос
```

**3. Алембик миграции:**

```bash
# Посмотреть текущую версию
docker-compose exec api alembic current

# История миграций
docker-compose exec api alembic history

# Откатить
docker-compose exec api alembic downgrade -1
```

---

## ✅ Best Practices

### 1. Всегда используй typing

```python
# ❌ Плохо
def process_data(data):
    return data["result"]

# ✅ Хорошо
def process_data(data: Dict[str, Any]) -> str:
    return data["result"]

# ✅ Еще лучше с Pydantic
from pydantic import BaseModel

class ProcessData(BaseModel):
    result: str
    status: int

def process_data(data: ProcessData) -> str:
    return data.result
```

### 2. Используй dataclasses

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class MediaFile:
    file_path: str
    mime_type: str
    file_size: int
    duration: Optional[float] = None

    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")
```

### 3. Обрабатывай ошибки правильно

```python
# ❌ Плохо - глотаем все ошибки
try:
    result = api_call()
except:
    pass

# ✅ Хорошо - конкретные исключения
try:
    result = api_call()
except httpx.HTTPStatusError as e:
    if e.response.status_code == 429:
        logger.warning("Rate limit hit, retrying")
        await asyncio.sleep(60)
        result = api_call()
    else:
        logger.error(f"API error: {e}")
        raise
except httpx.RequestError as e:
    logger.error(f"Network error: {e}")
    raise
```

### 4. Логируй с контекстом

```python
# ❌ Плохо
logger.info("Processing post")

# ✅ Хорошо
logger.info(
    "Processing post",
    post_id=post.id,
    platform="instagram",
    media_count=len(post.media),
    user_id=post.user_id
)
```

### 5. Используй async где возможно

```python
# ❌ Плохо - блокирующие вызовы
def upload_many(files):
    results = []
    for file in files:
        result = upload_file(file)  # Блокирует
        results.append(result)
    return results

# ✅ Хорошо - параллельная загрузка
async def upload_many(files):
    tasks = [upload_file(file) for file in files]
    results = await asyncio.gather(*tasks)
    return results
```

### 6. Проверяй входные данные

```python
# ❌ Плохо
def create_post(data):
    post = Post(**data)
    db.add(post)

# ✅ Хорошо - Pydantic validation
from pydantic import BaseModel, validator

class PostCreate(BaseModel):
    title: str
    content: str
    platforms: List[str]

    @validator('platforms')
    def validate_platforms(cls, v):
        allowed = ["instagram", "vk", "tiktok", "youtube"]
        for platform in v:
            if platform not in allowed:
                raise ValueError(f"Unknown platform: {platform}")
        return v

def create_post(data: PostCreate):
    post = Post(**data.dict())
    db.add(post)
```

### 7. Используй константы

```python
# ❌ Плохо - магические числа
if status_code == 429:
    wait_time = 60

# ✅ Хорошо - именованные константы
from enum import IntEnum

class HTTPStatus(IntEnum):
    RATE_LIMIT = 429

RATE_LIMIT_RETRY_SECONDS = 60

if status_code == HTTPStatus.RATE_LIMIT:
    wait_time = RATE_LIMIT_RETRY_SECONDS
```

### 8. Тестируй критичный функционал

```python
# tests/adapters/test_instagram.py

@pytest.mark.asyncio
async def test_instagram_photo_upload():
    adapter = InstagramAdapter()

    result = await adapter.publish_photo({
        "image_url": "https://example.com/test.jpg",
        "caption": "Test caption"
    })

    assert result.success is True
    assert result.platform_id is not None

@pytest.mark.asyncio
async def test_instagram_rate_limiting():
    adapter = InstagramAdapter()

    # Should handle rate limits gracefully
    with pytest.raises(RateLimitError):
        for _ in range(100):
            await adapter._make_api_request("GET", "/test")
```

---

## 📚 Полезные команды

### Docker

```bash
# Пересобрать контейнеры
docker-compose build --no-cache

# Просмотр логов
docker-compose logs -f api
docker-compose logs -f worker --tail=100

# Зайти в контейнер
docker-compose exec api bash
docker-compose exec worker python

# Очистить всё
docker-compose down -v
docker system prune -af
```

### Database

```bash
# Создать миграцию
docker-compose exec api alembic revision --autogenerate -m "Add new table"

# Применить миграции
docker-compose exec api alembic upgrade head

# Откатить
docker-compose exec api alembic downgrade -1

# SQL напрямую
docker-compose exec postgres psql -U sovani -d sovani_crosspost -c "SELECT COUNT(*) FROM posts;"
```

### Celery

```bash
# Статус воркеров
docker-compose exec worker celery -A app.workers.celery_app inspect ping

# Активные задачи
docker-compose exec worker celery -A app.workers.celery_app inspect active

# Очистить очереди
docker-compose exec worker celery -A app.workers.celery_app purge

# Flower (web UI для мониторинга)
docker-compose exec worker celery -A app.workers.celery_app flower --port=5555
```

---

## 🆘 Troubleshooting

### Проблема: Import errors

```bash
# Решение: установить dev зависимости
pip install -e .
```

### Проблема: Database connection refused

```bash
# Проверить что PostgreSQL запущен
docker-compose ps postgres

# Перезапустить
docker-compose restart postgres

# Проверить порт
netstat -an | grep 5432
```

### Проблема: Celery tasks не выполняются

```bash
# Проверить Redis
docker-compose exec redis redis-cli ping

# Проверить что worker запущен
docker-compose ps worker

# Посмотреть логи
docker-compose logs worker

# Очистить очереди
docker-compose exec worker celery -A app.workers.celery_app purge
```

---

*Документация обновлена: 2025-01-XX*
