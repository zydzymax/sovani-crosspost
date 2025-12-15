# SoVAni Crosspost - Техническая документация

## 🎯 Обзор проекта

**SoVAni Crosspost** - это MVP-система автоматической кроссплатформенной публикации контента из Telegram в социальные сети с интеллектуальной обработкой медиа.

### Основные возможности
- 📱 **5 платформ**: Instagram, VK, Telegram, TikTok, YouTube
- 🤖 **AI-контент**: Генерация описаний через LLM
- 🎥 **Smart Media**: Адаптивное транскодирование без искажений
- 📊 **Enrichment**: Автоматическое обогащение данными товаров
- 🔄 **Pipeline**: 7-этапный конвейер обработки
- 📈 **Мониторинг**: Метрики и трассировка

---

## 🏗️ Архитектура системы

### Стек технологий
- **Backend**: Python 3.11, FastAPI, SQLAlchemy
- **Task Queue**: Celery с 7 специализированными очередями
- **Database**: PostgreSQL 15 с Alembic миграциями
- **Storage**: MinIO (S3-compatible)
- **Cache**: Redis 7
- **Media**: FFmpeg для транскодирования
- **Observability**: Prometheus метрики, структурированные логи

### Компоненты системы

```
┌─────────────────┐    ┌──────────────┐    ┌─────────────────┐
│   Telegram      │───▶│   FastAPI    │───▶│   PostgreSQL    │
│   Input Bot     │    │   API        │    │   Database      │
└─────────────────┘    └──────────────┘    └─────────────────┘
                              │                      │
                              ▼                      ▼
┌─────────────────┐    ┌──────────────┐    ┌─────────────────┐
│   MinIO         │◀───│   Celery     │───▶│   Redis         │
│   S3 Storage    │    │   Workers    │    │   Message Queue │
└─────────────────┘    └──────────────┘    └─────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Social Media Platforms                        │
│  Instagram  │   VK    │ Telegram │  TikTok  │  YouTube    │
└─────────────────────────────────────────────────────────────┘
```

---

## 📋 Структура проекта

```
sovani_crosspost/
├── 📁 app/                          # Основной код приложения
│   ├── 📁 adapters/                 # Интеграции с внешними API
│   │   ├── instagram.py             # Meta Graph API
│   │   ├── vk.py                    # VK API
│   │   ├── telegram.py              # Telegram Bot API
│   │   ├── tiktok.py                # TikTok Content API
│   │   ├── youtube.py               # YouTube Data API
│   │   └── storage_s3.py            # MinIO/S3 клиент
│   │
│   ├── 📁 api/                      # FastAPI endpoints
│   │   ├── routes.py                # HTTP маршруты
│   │   └── deps.py                  # Зависимости DI
│   │
│   ├── 📁 workers/                  # Celery задачи
│   │   ├── celery_app.py            # Конфигурация Celery
│   │   └── 📁 tasks/                # Задачи по очередям
│   │       ├── ingest.py            # Прием контента
│   │       ├── enrich.py            # Обогащение данными
│   │       ├── captionize.py        # AI генерация текста
│   │       ├── transcode.py         # Медиа обработка
│   │       ├── preflight.py         # Финальная проверка
│   │       ├── publish.py           # Публикация
│   │       └── finalize.py          # Завершение и уведомления
│   │
│   ├── 📁 services/                 # Бизнес-логика
│   │   ├── caption_llm.py           # LLM для генерации контента
│   │   ├── enrichment.py            # Обогащение товарными данными
│   │   ├── scheduler.py             # Планировщик публикаций
│   │   ├── preflight_rules.py       # Правила валидации
│   │   └── notifier.py              # Telegram уведомления
│   │
│   ├── 📁 models/                   # Модели данных
│   │   ├── entities.py              # SQLAlchemy модели
│   │   ├── repositories.py          # Паттерн Repository
│   │   └── db.py                    # Подключение к БД
│   │
│   ├── 📁 media/                    # Обработка медиа
│   │   ├── ffmpeg_wrapper.py        # FFmpeg интеграция
│   │   └── smart_crop_stub.py       # MediaPipe обрезка
│   │
│   ├── 📁 core/                     # Конфигурация
│   │   ├── config.py                # Настройки приложения
│   │   ├── security.py              # Безопасность
│   │   └── logging.py               # Структурированные логи
│   │
│   └── main.py                      # Точка входа FastAPI
│
├── 📁 config/                       # Конфигурационные файлы
│   └── publishing_rules.yml         # Правила публикации
│
├── 📁 migrations/                   # SQL миграции
│   ├── 0001_init.sql               # Инициализация схемы
│   └── 0002_outbox_rules.sql       # Outbox pattern
│
├── 📁 tests/                        # Тесты
│   ├── test_e2e_smoke.py           # End-to-end тесты
│   └── test_*.py                    # Unit тесты адаптеров
│
├── 📁 helpers/                      # Утилиты
│   └── ffmpeg_profiles.sh          # FFmpeg профили
│
├── docker-compose.yml              # Оркестрация сервисов
├── Dockerfile                      # Образ приложения
├── Dockerfile.worker               # Образ worker
├── requirements.txt                # Python зависимости
└── README.md                       # Базовая документация
```

---

## 🔄 Пайплайн обработки

### Очереди Celery (7 этапов)

1. **🗂️ INGEST** - Прием контента из Telegram
   - Загрузка медиафайлов в MinIO
   - Парсинг метаданных
   - Создание записи в БД

2. **🔍 ENRICH** - Обогащение товарными данными
   - Извлечение артикула из сообщения
   - API запросы к маркетплейсам (WB/Ozon/YM)
   - Сохранение атрибутов товара

3. **✍️ CAPTIONIZE** - AI-генерация контента
   - Формирование промптов под каждую платформу
   - LLM генерация описаний и хештегов
   - Адаптация под требования платформ

4. **🎬 TRANSCODE** - Медиа обработка
   - FFmpeg транскодирование в 4 формата:
     - 9:16 (TikTok/Instagram Stories)
     - 4:5 (Instagram Feed)
     - 1:1 (Instagram/VK квадрат)
     - 16:9 (YouTube/VK горизонтальное)
   - MediaPipe умная обрезка (без обрезки лиц)
   - Оптимизация размера файлов

5. **✅ PREFLIGHT** - Финальная проверка
   - Валидация контента по правилам
   - Проверка размеров и форматов
   - Конфликт-резолюшн

6. **📤 PUBLISH** - Публикация на платформах
   - Параллельная отправка через адаптеры
   - Обработка rate limits
   - Retry логика с exponential backoff

7. **🏁 FINALIZE** - Завершение
   - Обновление статусов в БД
   - Отправка уведомлений в Telegram
   - Очистка временных файлов

### Диаграмма потока

```mermaid
graph TD
    A[Telegram Input] --> B[INGEST Queue]
    B --> C[ENRICH Queue]
    C --> D[CAPTIONIZE Queue]
    D --> E[TRANSCODE Queue]
    E --> F[PREFLIGHT Queue]
    F --> G[PUBLISH Queue]
    G --> H[FINALIZE Queue]
    
    G --> I[Instagram API]
    G --> J[VK API]
    G --> K[TikTok API]
    G --> L[YouTube API]
    G --> M[Telegram API]
    
    H --> N[Admin Notifications]
```

---

## 🗃️ Модель данных

### Основные сущности

```python
# Товары из маркетплейсов
class Product:
    id: UUID
    article: str                    # Артикул товара
    title: str                     # Название
    brand: str                     # Бренд
    category: str                  # Категория
    fabric: str                    # Состав ткани
    density: str                   # Плотность
    care_instructions: str         # Инструкции по уходу
    marketplace_data: JSON         # Данные с маркетплейсов

# Медиа файлы
class MediaAsset:
    id: UUID
    original_filename: str         # Исходное имя файла
    file_path: str                # Путь в S3
    mime_type: str                # MIME тип
    file_size: int                # Размер файла
    duration: Optional[float]      # Длительность видео
    dimensions: str               # Разрешение (WxH)

# Публикации
class Post:
    id: UUID
    source_type: str              # telegram/manual/api
    source_data: JSON             # Исходные данные
    product_id: Optional[UUID]    # Связанный товар
    status: PostStatus            # Статус обработки
    platforms: List[str]          # Целевые платформы
    created_at: datetime

# Версии медиа для разных платформ
class Rendition:
    id: UUID
    media_asset_id: UUID          # Исходный медиа файл
    platform: str                # instagram/tiktok/youtube
    aspect_ratio: str            # 9:16, 4:5, 1:1, 16:9
    file_path: str               # Путь к обработанному файлу
    
# Аккаунты платформ
class Account:
    id: UUID
    platform: str                # Название платформы
    username: str                # Имя аккаунта
    credentials: JSON            # Зашифрованные токены
    is_active: bool              # Активен ли аккаунт

# Задачи обработки
class Task:
    id: UUID
    post_id: UUID                # Связанная публикация
    queue_name: str              # Название очереди
    status: TaskStatus           # pending/running/success/failed
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
```

### Статусы

```python
class PostStatus(Enum):
    DRAFT = "draft"              # Черновик
    PROCESSING = "processing"     # В обработке
    READY = "ready"              # Готов к публикации
    PUBLISHING = "publishing"     # Публикуется
    PUBLISHED = "published"       # Опубликован
    FAILED = "failed"            # Ошибка
    CANCELLED = "cancelled"       # Отменен

class TaskStatus(Enum):
    PENDING = "pending"          # В очереди
    RUNNING = "running"          # Выполняется
    SUCCESS = "success"          # Успешно
    FAILED = "failed"            # Ошибка
    RETRY = "retry"              # Повтор
```

---

## 🔌 Интеграции с платформами

### 📷 Instagram (Meta Graph API)
```python
# Поддерживаемые форматы
FORMATS = {
    'photo': ['jpg', 'jpeg', 'png'],
    'video': ['mp4', 'mov'],
    'story': ['jpg', 'mp4'],
    'reel': ['mp4']
}

# Ограничения
LIMITS = {
    'photo_max_size': '8MB',
    'video_max_size': '100MB', 
    'video_max_duration': '60s',
    'caption_max_length': 2200
}
```

### 🔵 VK API
```python
# Методы API
METHODS = {
    'wall.post': 'Публикация на стене',
    'video.save': 'Загрузка видео',
    'photos.getWallUploadServer': 'Загрузка фото'
}

# Ограничения
LIMITS = {
    'photo_max_size': '50MB',
    'video_max_size': '2GB',
    'post_max_length': 15000
}
```

### 📱 TikTok Content Posting API
```python
# Поддерживаемые форматы
FORMATS = {
    'video': ['mp4', 'mov', 'mpeg', 'flv', 'avi', '3gpp', 'webm']
}

# Требования к видео
REQUIREMENTS = {
    'min_duration': '3s',
    'max_duration': '180s', 
    'min_resolution': '540x960',
    'max_resolution': '1080x1920',
    'max_file_size': '500MB'
}
```

### ▶️ YouTube Data API v3
```python
# Типы контента
CONTENT_TYPES = {
    'video': 'Обычное видео',
    'short': 'YouTube Shorts (≤60s)'
}

# Метаданные
METADATA = {
    'title': 'max 100 chars',
    'description': 'max 5000 chars',
    'tags': 'max 500 chars total',
    'category': 'predefined list'
}
```

### 💬 Telegram Bot API
```python
# Методы отправки
METHODS = {
    'sendPhoto': 'Фото с описанием',
    'sendVideo': 'Видео',
    'sendMediaGroup': 'Медиа-группа до 10 файлов'
}

# Ограничения
LIMITS = {
    'photo_max_size': '10MB',
    'video_max_size': '50MB',
    'caption_max_length': 1024
}
```

---

## ⚙️ Конфигурация

### Переменные окружения

```bash
# Database
DATABASE_URL=postgresql://user:[REVOKED_SECRET_REMOVED]@host:5432/db
POSTGRES_DB=sovani_crosspost
POSTGRES_USER=sovani  
POSTGRES_PASSWORD=[REVOKED_SECRET_REMOVED]

# Redis & Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# MinIO S3 Storage
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=[REVOKED_SECRET_REMOVED]
S3_SECRET_KEY=[REVOKED_SECRET_REMOVED]
S3_BUCKET_NAME=sovani-media
S3_REGION=us-east-1

# Platform API Keys (обязательно задать)
INSTAGRAM_ACCESS_TOKEN=your_token
INSTAGRAM_BUSINESS_ACCOUNT_ID=your_id

VK_ACCESS_TOKEN=your_token
VK_GROUP_ID=your_group_id

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ADMIN_CHAT_ID=your_chat_id

TIKTOK_ACCESS_TOKEN=your_token
TIKTOK_CLIENT_KEY=your_client_key

YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_secret
YOUTUBE_REFRESH_TOKEN=your_token

# LLM для генерации контента
OPENAI_API_KEY=your_openai_key
# или
ANTHROPIC_API_KEY=your_claude_key

# Application Settings  
APP_ENV=development  # development/staging/production
LOG_LEVEL=INFO       # DEBUG/INFO/WARNING/ERROR
WORKERS_COUNT=4      # Количество Celery workers
```

### Правила публикации (config/publishing_rules.yml)

```yaml
platforms:
  instagram:
    enabled: true
    max_daily_posts: 5
    optimal_times: ["09:00", "18:00", "21:00"]
    hashtags_count: [10, 30]
    caption_templates:
      fashion: "✨ {title}\n\n📝 {description}\n\n🏷️ {hashtags}"
      
  vk:
    enabled: true
    max_daily_posts: 10
    hashtags_count: [5, 15]
    
  tiktok:
    enabled: true
    max_daily_posts: 3
    video_duration: [15, 60]
    
  youtube:
    enabled: true
    max_daily_posts: 1
    
  telegram:
    enabled: true
    channels: ["@channel1", "@channel2"]

content_rules:
  min_video_quality: 720p
  max_file_size: 100MB
  forbidden_words: ["спам", "реклама"]
  required_hashtags: ["#sovani"]
```

---

## 🧪 Тестирование

### End-to-End тесты

```python
# Пример E2E теста полного пайплайна
async def test_full_pipeline():
    # 1. Отправка контента через API
    response = await client.post("/api/posts", json={
        "source_type": "telegram",
        "source_data": {
            "message": "Новая коллекция весна 2024",
            "media_url": "https://example.com/video.mp4",
            "article": "WB123456789"
        },
        "platforms": ["instagram", "vk", "tiktok"]
    })
    post_id = response.json()["id"]
    
    # 2. Ожидание прохождения всех этапов
    await wait_for_pipeline_completion(post_id)
    
    # 3. Проверка результатов
    post = await get_post(post_id)
    assert post.status == PostStatus.PUBLISHED
    assert len(post.renditions) == 3  # По одному на платформу
    
    # 4. Проверка публикации на платформах
    for platform in ["instagram", "vk", "tiktok"]:
        publication = await get_publication(post_id, platform)
        assert publication.published_url is not None
```

### Unit тесты адаптеров

```python
# Тест Instagram адаптера
async def test_instagram_post_photo():
    adapter = InstagramAdapter(access_token="test_token")
    
    photo_data = {
        "image_url": "https://example.com/photo.jpg",
        "caption": "Test caption #test"
    }
    
    result = await adapter.publish_photo(photo_data)
    
    assert result.success is True
    assert result.platform_id is not None
    assert "instagram.com" in result.published_url
```

### Smoke тесты сервисов

```python
# Проверка работоспособности всех компонентов
def test_services_health():
    # Database
    assert db.execute("SELECT 1").scalar() == 1
    
    # Redis
    assert redis.ping() is True
    
    # MinIO
    assert s3_client.bucket_exists("sovani-media") is True
    
    # Celery
    inspect = celery_app.control.inspect()
    assert len(inspect.active()) >= 0
```

---

## 📊 Мониторинг и логирование

### Метрики Prometheus

```python
# Счетчики бизнес-метрик
posts_total = Counter('posts_total', 'Total posts processed', ['platform', 'status'])
processing_duration = Histogram('processing_duration_seconds', 'Processing time', ['stage'])
api_requests = Counter('api_requests_total', 'API requests', ['platform', 'method', 'status'])

# Системные метрики
celery_tasks = Gauge('celery_tasks_active', 'Active Celery tasks', ['queue'])
db_connections = Gauge('db_connections_active', 'Active DB connections')
```

### Структурированные логи

```python
# Пример лога с контекстом
logger.info(
    "Post processing started",
    post_id=post.id,
    platforms=post.platforms,
    stage="ingest",
    user_id=post.user_id,
    extra={
        "media_count": len(post.media_assets),
        "estimated_duration": "5min"
    }
)
```

### Health Check эндпоинты

```python
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "services": {
            "database": await check_db_health(),
            "redis": await check_redis_health(), 
            "s3": await check_s3_health(),
            "celery": await check_celery_health()
        }
    }
```

---

## 🚀 MVP Критерии готовности

### ✅ Функциональные требования
- [ ] **Telegram Input**: Прием медиа + артикул из приватного канала
- [ ] **Multi-Platform**: Публикация на 5 платформах одновременно  
- [ ] **Smart Media**: 4 формата без искажений (9:16, 4:5, 1:1, 16:9)
- [ ] **AI Content**: LLM генерация описаний под каждую платформу
- [ ] **Product Enrichment**: Автоматическое обогащение данными товаров
- [ ] **Admin Notifications**: Статусы и ссылки в админ-канал

### ✅ Технические требования  
- [ ] **Pipeline Reliability**: 10 постов подряд без ошибок
- [ ] **Zero Manual Fixes**: Без ручной коррекции медиа
- [ ] **Format Compliance**: Соответствие требованиям платформ
- [ ] **Error Handling**: Graceful degradation при сбоях
- [ ] **Rate Limiting**: Соблюдение лимитов API
- [ ] **Security**: Шифрование токенов и безопасное хранение

### 📈 Метрики успеха
- **Throughput**: 3-5 постов/день на платформу
- **Success Rate**: ≥95% успешных публикаций  
- **Processing Time**: ≤10 минут от загрузки до публикации
- **Manual Intervention**: 0% для корректного контента
- **Platform Coverage**: 100% заявленных платформ
- **Content Quality**: AI-описания соответствуют товару

---

## 🛠️ Troubleshooting

### Частые проблемы

#### 1. Сервисы не запускаются
```bash
# Полная очистка и перезапуск
docker-compose down -v
docker system prune -f
docker-compose build --no-cache
docker-compose up -d

# Проверка логов
docker-compose logs -f api
docker-compose logs -f worker
```

#### 2. Ошибки FFmpeg
```bash
# Установка FFmpeg в контейнер worker
docker-compose exec worker apt-get update
docker-compose exec worker apt-get install -y ffmpeg

# Проверка версии
docker-compose exec worker ffmpeg -version
```

#### 3. Проблемы с MinIO
```bash
# Создание bucket
docker-compose exec minio mc alias set local http://localhost:9000 [REVOKED_SECRET_REMOVED] [REVOKED_SECRET_REMOVED]
docker-compose exec minio mc mb local/sovani-media

# Проверка доступа
curl http://localhost:9000/minio/health/live
```

#### 4. Celery задачи зависают
```bash
# Мониторинг очередей
docker-compose exec worker celery -A app.workers.celery_app inspect active
docker-compose exec worker celery -A app.workers.celery_app inspect reserved

# Очистка очередей  
docker-compose exec worker celery -A app.workers.celery_app purge
```

#### 5. Проблемы с API платформ
```bash
# Проверка токенов
curl -X GET "http://localhost:8000/api/accounts/test"

# Просмотр логов адаптеров
docker-compose logs -f worker | grep "adapter"
```

### Логи и диагностика

```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис с фильтром
docker-compose logs -f worker | grep "ERROR"

# Последние N строк  
docker-compose logs --tail=100 api

# Экспорт логов в файл
docker-compose logs api > api.log 2>&1
```

### Полезные команды

```bash
# Статистика ресурсов
docker stats

# Подключение к PostgreSQL
docker-compose exec postgres psql -U sovani -d sovani_crosspost

# Подключение к Redis CLI
docker-compose exec redis redis-cli

# Просмотр содержимого MinIO
docker-compose exec minio mc ls local/sovani-media
```

---

## 📚 Ресурсы и ссылки

### API документация платформ
- [Instagram Basic Display API](https://developers.facebook.com/docs/instagram-basic-display-api)
- [VK API методы](https://dev.vk.com/method)  
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [TikTok for Developers](https://developers.tiktok.com/)
- [YouTube Data API](https://developers.google.com/youtube/v3)

### Инструменты разработки
- [FastAPI документация](https://fastapi.tiangolo.com/)
- [Celery документация](https://docs.celeryproject.org/)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/)
- [FFmpeg гайды](https://ffmpeg.org/documentation.html)

### Мониторинг
- [Prometheus](https://prometheus.io/docs/)
- [Grafana dashboards](https://grafana.com/grafana/)
- [Sentry error tracking](https://sentry.io/)

---

*Документация обновлена: 2024-12-26*