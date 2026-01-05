# 📋 TODO: План завершения SalesWhisper Crosspost MVP

**Текущий статус:** 75-80% реализовано
**До production MVP:** 10-14 дней активной разработки
**Обновлено:** 2025-01-XX

---

## 🎯 Критические задачи (MUST HAVE)

Без этих компонентов система не запустится.

### 1. 🔑 Security Keys [1 час]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ❌ Не сделано
**Зависимости:** Нет

**Задачи:**
- [ ] Сгенерировать AES_KEY (32 символа)
- [ ] Сгенерировать TOKEN_ENCRYPTION_KEY (32 символа)
- [ ] Сгенерировать JWT_SECRET_KEY (64 символа)
- [ ] Добавить в `.env` файл
- [ ] Проверить, что ключи корректной длины

**Команды:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])"
python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])"
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Файлы:**
- `.env` - добавить ключи

---

### 2. 🗄️ SQLAlchemy модели [1-2 дня]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ❌ Пустой файл (1 строка)
**Зависимости:** Миграции БД готовы

**Задачи:**
- [ ] **Product model** - товары
  ```python
  - id: UUID (PK)
  - article: str
  - title: str
  - brand: str
  - category: str
  - marketplace_data: JSON
  - created_at, updated_at: datetime
  ```

- [ ] **MediaAsset model** - медиафайлы
  ```python
  - id: UUID (PK)
  - product_id: UUID (FK)
  - original_filename: str
  - file_path: str (S3 path)
  - mime_type: str
  - file_size: int
  - duration: Optional[float]
  - dimensions: str
  - metadata: JSON
  ```

- [ ] **Rendition model** - обработанные версии
  ```python
  - id: UUID (PK)
  - media_asset_id: UUID (FK)
  - platform: str
  - aspect_ratio: str (9:16, 4:5, 1:1, 16:9)
  - file_path: str (S3 path)
  - file_size: int
  - created_at: datetime
  ```

- [ ] **Post model** - публикации
  ```python
  - id: UUID (PK)
  - product_id: Optional[UUID] (FK)
  - source_type: str
  - source_data: JSON
  - status: PostStatus (enum)
  - platforms: ARRAY[str]
  - scheduled_at: Optional[datetime]
  - published_at: Optional[datetime]
  - created_at, updated_at: datetime
  ```

- [ ] **Account model** - аккаунты платформ
  ```python
  - id: UUID (PK)
  - platform: str
  - username: str
  - credentials: JSON (encrypted)
  - is_active: bool
  - last_used_at: Optional[datetime]
  ```

- [ ] **Task model** - задачи Celery
  ```python
  - id: UUID (PK)
  - post_id: UUID (FK)
  - queue_name: str
  - status: TaskStatus (enum)
  - celery_task_id: str
  - started_at, completed_at: Optional[datetime]
  - error_message: Optional[str]
  - retry_count: int
  ```

- [ ] **Log model** - аудит логи
  ```python
  - id: UUID (PK)
  - post_id: Optional[UUID] (FK)
  - level: str
  - message: str
  - context: JSON
  - created_at: datetime
  ```

- [ ] Добавить **relationships** между моделями
- [ ] Настроить **indexes** (id, created_at, status)
- [ ] Добавить **enums** (PostStatus, TaskStatus, Platform)
- [ ] Написать **fixtures** для тестов

**Файлы:**
- `app/models/entities.py` - все модели (сейчас пустой!)
- `app/models/enums.py` - enum типы
- `app/models/__init__.py` - экспорты

**Тесты:**
- `tests/models/test_entities.py` - тесты моделей

---

### 3. 🔌 Database Repository Pattern [1 день]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ❌ Не реализовано
**Зависимости:** SQLAlchemy модели

**Задачи:**
- [ ] **BaseRepository** - базовый класс
  ```python
  - get(id) → Optional[Model]
  - get_many(filters) → List[Model]
  - create(data) → Model
  - update(id, data) → Model
  - delete(id) → bool
  - exists(id) → bool
  ```

- [ ] **ProductRepository**
  ```python
  - get_by_article(article: str) → Optional[Product]
  - search_by_title(query: str) → List[Product]
  ```

- [ ] **PostRepository**
  ```python
  - get_by_status(status: PostStatus) → List[Post]
  - get_scheduled() → List[Post]
  - get_with_media(post_id) → Post + MediaAssets
  ```

- [ ] **MediaRepository**
  ```python
  - get_by_product(product_id) → List[MediaAsset]
  - get_renditions(media_id, platform) → List[Rendition]
  ```

- [ ] **TaskRepository**
  ```python
  - get_by_post(post_id) → List[Task]
  - get_failed_tasks() → List[Task]
  - get_pending_for_queue(queue_name) → List[Task]
  ```

- [ ] Добавить **session management** (context manager)
- [ ] Добавить **transaction support**
- [ ] Добавить **batch operations**

**Файлы:**
- `app/models/repositories/base.py`
- `app/models/repositories/product.py`
- `app/models/repositories/post.py`
- `app/models/repositories/media.py`
- `app/models/repositories/task.py`

**Тесты:**
- `tests/repositories/test_*.py`

---

### 4. 💾 Интеграция БД в Tasks [2 дня]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ⚠️ Только placeholders
**Зависимости:** SQLAlchemy модели + Repositories

**Задачи:**
- [ ] **ingest.py** - заменить placeholders
  ```python
  - Создавать реальные Post записи в БД
  - Сохранять MediaAsset после загрузки
  - Обновлять Task статус
  ```

- [ ] **enrich.py**
  ```python
  - Читать Post из БД
  - Обновлять Product данные
  - Логировать в БД
  ```

- [ ] **captionize.py**
  ```python
  - Читать Post + Product
  - Сохранять сгенерированные caption'ы
  ```

- [ ] **transcode.py**
  ```python
  - Читать MediaAsset
  - Создавать Rendition записи
  - Обновлять progress
  ```

- [ ] **preflight.py**
  ```python
  - Читать Post + Renditions
  - Валидация
  - Обновлять статус
  ```

- [ ] **publish.py**
  ```python
  - Читать Post + Renditions
  - Публикация
  - Сохранять platform_id и URLs
  ```

- [ ] **finalize.py**
  ```python
  - Обновлять Post.status = PUBLISHED
  - Логировать результаты
  ```

**Заменить во всех tasks:**
```python
# БЫЛО (placeholder):
# This is a placeholder - would create actual database record

# СТАЛО:
from app.models.repositories.post import PostRepository
post_repo = PostRepository(db_session)
post = post_repo.create({...})
```

**Файлы:**
- `app/workers/tasks/ingest.py`
- `app/workers/tasks/enrich.py`
- `app/workers/tasks/captionize.py`
- `app/workers/tasks/transcode.py`
- `app/workers/tasks/preflight.py`
- `app/workers/tasks/publish.py`
- `app/workers/tasks/finalize.py`

---

### 5. 📦 S3/MinIO интеграция [1 день]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ⚠️ Stub реализация
**Зависимости:** Нет

**Задачи:**
- [ ] Реализовать **StorageS3** класс полностью
  ```python
  - upload_file(file_path, s3_key) → str (S3 URL)
  - download_file(s3_key, local_path) → bool
  - delete_file(s3_key) → bool
  - list_files(prefix) → List[str]
  - get_presigned_url(s3_key, expires) → str
  - file_exists(s3_key) → bool
  ```

- [ ] Добавить **multipart upload** для больших файлов
- [ ] Реализовать **retry логику** для сетевых ошибок
- [ ] Добавить **progress tracking** для uploads
- [ ] Настроить **bucket lifecycle** (удаление старых файлов)
- [ ] Добавить **CDN** presigned URLs
- [ ] Добавить **метаданные** в S3 objects

**Файлы:**
- `app/adapters/storage_s3.py` (сейчас заглушка)

**Тесты:**
- `tests/adapters/test_storage_s3.py`

---

### 6. 📥 Загрузка медиа из Telegram [1 день]

**Приоритет:** 🔥 КРИТИЧНО
**Статус:** ❌ Не реализовано
**Зависимости:** S3 интеграция

**Задачи:**
- [ ] Реализовать **download_telegram_media()**
  ```python
  async def download_telegram_media(
      file_id: str,
      bot_token: str,
      post_id: str
  ) -> MediaAsset:
      # 1. getFile для получения file_path
      # 2. Download file от Telegram
      # 3. Upload в S3
      # 4. Создать MediaAsset в БД
      # 5. Вернуть MediaAsset
  ```

- [ ] Добавить поддержку всех типов медиа:
  ```python
  - Photo (одно фото и массив)
  - Video
  - Document
  - Animation (GIF)
  - Video note (кружочки)
  - Voice/Audio
  ```

- [ ] Реализовать **media groups** (альбомы)
- [ ] Добавить **metadata extraction**:
  ```python
  - Dimensions (width, height)
  - Duration (для видео)
  - File size
  - MIME type
  - Codec info
  ```

- [ ] Добавить **validation**:
  ```python
  - Max file size (500MB)
  - Supported formats
  - Min/max duration для видео
  ```

**Файлы:**
- `app/adapters/telegram.py` - добавить download функцию
- `app/workers/tasks/ingest.py` - использовать download

**Интеграция в ingest task:**
```python
# В _download_media_file():
media_info = await download_telegram_media(
    file_id=media_data["file_id"],
    bot_token=settings.telegram.bot_token,
    post_id=post_id
)
```

---

## 🎨 Важные задачи (SHOULD HAVE)

Нужны для полноценной работы MVP.

### 7. 🎬 YouTube Adapter [2-3 дня]

**Приоритет:** 🟠 Высокий
**Статус:** ❌ Пустой файл (1 строка!)
**Зависимости:** Токены YouTube

**Задачи:**
- [ ] Создать класс **YouTubeAdapter**
- [ ] Реализовать **OAuth 2.0** flow
  ```python
  - get_authorization_url()
  - exchange_code_for_token(code)
  - refresh_access_token()
  ```

- [ ] Реализовать **video upload**
  ```python
  - Chunked upload (ResumableUpload)
  - Progress tracking
  - Retry на сбоях
  ```

- [ ] Добавить **metadata**:
  ```python
  - Title (max 100 chars)
  - Description (max 5000 chars)
  - Tags (max 500 chars total)
  - Category (ID from predefined list)
  - Privacy status (public/private/unlisted)
  ```

- [ ] Реализовать **YouTube Shorts**:
  ```python
  - Detect видео ≤60 sec + vertical
  - Set hashtag #Shorts
  - Optimize title/description
  ```

- [ ] Добавить **quota management**:
  ```python
  - Track daily quota usage
  - Pause uploads if quota exceeded
  - Resume next day
  ```

- [ ] Error handling для YouTube errors:
  ```python
  - Quota exceeded
  - Invalid video
  - Upload failed
  - Processing failed
  ```

**Файлы:**
- `app/adapters/youtube.py` (сейчас ПУСТО!)

**Тесты:**
- `tests/adapters/test_youtube.py`

**Примеры:**
- Посмотреть на реализацию в `vk.py`, `instagram.py`

---

### 8. 🔔 Notifier Service [1 день]

**Приоритет:** 🟠 Высокий
**Статус:** ❌ Пустой файл
**Зависимости:** Telegram adapter

**Задачи:**
- [ ] Реализовать **NotifierService**
  ```python
  async def notify_post_created(post_id, platforms)
  async def notify_post_processing(post_id, stage, progress)
  async def notify_post_published(post_id, results)
  async def notify_post_failed(post_id, error)
  ```

- [ ] Форматировать красивые сообщения:
  ```
  ✅ Пост опубликован!

  📝 ID: abc-123
  📱 Платформы: Instagram, VK, TikTok

  🔗 Ссылки:
  • Instagram: https://instagram.com/p/xyz
  • VK: https://vk.com/wall-123_456
  • TikTok: https://tiktok.com/@user/video/789

  ⏱ Время обработки: 3 мин 24 сек
  ```

- [ ] Добавить **inline buttons**:
  ```python
  - Просмотреть на платформе
  - Удалить пост
  - Повторить публикацию
  ```

- [ ] Реализовать **admin channel posting**
- [ ] Добавить **error alerts** для критических сбоев
- [ ] Группировать уведомления (batch notifications)

**Файлы:**
- `app/services/notifier.py` (сейчас ПУСТО!)

---

### 9. 🔐 OAuth Flows [1-2 дня]

**Приоритет:** 🟠 Высокий
**Статус:** ❌ Не реализовано
**Зависимости:** Нет

**Задачи:**
- [ ] **TikTok OAuth**
  ```python
  - Authorization URL generation
  - Code exchange for tokens
  - Refresh token logic
  - Token storage (encrypted)
  ```

- [ ] **YouTube OAuth**
  ```python
  - Authorization URL
  - Token exchange
  - Refresh tokens
  ```

- [ ] **Instagram Long-Lived Token refresh**
  ```python
  - Auto-refresh before expiry
  - Handle refresh failures
  - Re-authorization flow
  ```

- [ ] Создать **OAuth callback endpoints**:
  ```python
  GET /auth/tiktok/callback
  GET /auth/youtube/callback
  GET /auth/instagram/callback
  ```

- [ ] Добавить **token encryption** в БД
- [ ] Реализовать **token rotation**

**Файлы:**
- `app/api/auth_routes.py` (новый)
- `app/services/oauth_manager.py` (новый)

---

### 10. 🪝 Webhook Endpoints [1 день]

**Приоритет:** 🟠 Средний
**Статус:** ⚠️ Частично (только Telegram)
**Зависимости:** Нет

**Задачи:**
- [ ] **Telegram webhook** - уже есть, проверить
- [ ] **TikTok webhook** для уведомлений:
  ```python
  POST /api/webhooks/tiktok
  - Video processing completed
  - Video published
  - Video failed
  ```

- [ ] **Instagram webhook** (опционально):
  ```python
  POST /api/webhooks/instagram
  - Media comments
  - Mentions
  ```

- [ ] Добавить **signature verification**:
  ```python
  - TikTok: X-TikTok-Signature
  - Instagram: X-Hub-Signature
  ```

- [ ] Добавить **idempotency** (дедупликация events)
- [ ] Логировать все webhooks в БД

**Файлы:**
- `app/api/webhooks.py`

---

## 🔧 Дополнительные задачи (NICE TO HAVE)

Улучшают функционал, но не критичны для MVP.

### 11. 🎨 Smart Crop [2-3 дня]

**Приоритет:** 🟢 Средний
**Статус:** ⚠️ Только stub
**Зависимости:** MediaPipe или Cloud Vision API

**Задачи:**
- [ ] Интегрировать **MediaPipe** для определения лиц
- [ ] Реализовать **intelligent cropping**:
  ```python
  - Detect faces, objects
  - Calculate optimal crop area
  - Preserve important content
  - Multiple aspect ratios
  ```

- [ ] Fallback на **центральный crop** если нет лиц
- [ ] Добавить **manual crop** через API

**Файлы:**
- `app/media/smart_crop.py` (сейчас stub!)

---

### 12. 📊 Admin Dashboard API [2-3 дня]

**Приоритет:** 🟢 Низкий
**Статус:** ❌ Не реализовано

**Задачи:**
- [ ] **Posts Management**:
  ```python
  GET /api/admin/posts - список постов
  GET /api/admin/posts/{id} - детали поста
  POST /api/admin/posts - создать пост вручную
  DELETE /api/admin/posts/{id} - удалить
  ```

- [ ] **Queue Monitoring**:
  ```python
  GET /api/admin/queues - статистика очередей
  GET /api/admin/queues/{name}/tasks - задачи
  POST /api/admin/queues/{name}/purge - очистить
  ```

- [ ] **Platform Accounts**:
  ```python
  GET /api/admin/accounts - список аккаунтов
  POST /api/admin/accounts/{id}/test - тест публикации
  PUT /api/admin/accounts/{id}/toggle - вкл/выкл
  ```

- [ ] **Analytics**:
  ```python
  GET /api/admin/stats/daily - дневная статистика
  GET /api/admin/stats/platform - по платформам
  ```

**Файлы:**
- `app/api/admin_routes.py`

---

### 13. 🧪 Тестирование [2-3 дня]

**Приоритет:** 🟢 Средний
**Статус:** ⚠️ Структура есть, тесты не работают

**Задачи:**
- [ ] **Unit tests** для adapters:
  ```python
  - test_instagram_adapter.py
  - test_vk_adapter.py
  - test_tiktok_adapter.py
  - test_youtube_adapter.py
  - test_telegram_adapter.py
  ```

- [ ] **Integration tests** для tasks:
  ```python
  - test_ingest_task.py
  - test_publish_task.py
  - test_full_pipeline.py
  ```

- [ ] **E2E tests**:
  ```python
  - Полный флоу от Telegram до публикации
  - Test с реальными файлами
  - Mock внешние API
  ```

- [ ] Настроить **pytest fixtures**
- [ ] Добавить **test coverage** reporting
- [ ] Настроить **CI/CD** с тестами

**Файлы:**
- `tests/adapters/test_*.py`
- `tests/tasks/test_*.py`
- `tests/e2e/test_*.py`
- `conftest.py` - fixtures

---

### 14. 📈 Monitoring & Alerting [1-2 дня]

**Приоритет:** 🟢 Низкий
**Статус:** ⚠️ Metrics есть, dashboards нет

**Задачи:**
- [ ] Настроить **Grafana dashboards**:
  ```
  - Posts per day by platform
  - Success/failure rates
  - Processing times by stage
  - Queue depths
  - API response times
  ```

- [ ] Настроить **Alerting rules**:
  ```
  - Queue depth > threshold
  - Success rate < 90%
  - Worker down
  - Database connection issues
  ```

- [ ] Добавить **Jaeger** для distributed tracing
- [ ] Настроить **Sentry** для error tracking

---

## 📅 Roadmap по фазам

### 🔴 Фаза 1: Критический функционал (5-7 дней)

**Цель:** Базовая работоспособность с Telegram + VK

1. ✅ Security Keys (1 час)
2. SQLAlchemy модели (1-2 дня)
3. Repository pattern (1 день)
4. Интеграция БД в tasks (2 дня)
5. S3/MinIO интеграция (1 день)
6. Загрузка медиа из Telegram (1 день)

**Результат:** Система может принять пост из Telegram, обработать и опубликовать в VK

---

### 🟠 Фаза 2: Расширение платформ (3-5 дней)

**Цель:** Добавить YouTube и улучшить функционал

7. YouTube Adapter (2-3 дня)
8. Notifier Service (1 день)
9. OAuth Flows (1-2 дня)
10. Webhook Endpoints (1 день)

**Результат:** Работают все 5 платформ, есть уведомления в админ-канал

---

### 🟢 Фаза 3: Улучшения и тестирование (2-4 дня)

**Цель:** Polishing и готовность к production

11. Smart Crop (2-3 дня) - опционально
12. Admin Dashboard API (2-3 дня) - опционально
13. Тестирование (2-3 дня)
14. Monitoring & Alerting (1-2 дня)

**Результат:** Production-ready MVP

---

## ✅ Критерии готовности MVP

### Функциональные:
- [ ] Прием контента из Telegram
- [ ] Публикация на 4 платформы (Telegram, VK, Instagram, YouTube)
- [ ] Автоматическая адаптация медиа (4 формата)
- [ ] AI-генерация описаний
- [ ] Уведомления в админ-канал со ссылками

### Технические:
- [ ] 10 постов подряд проходят весь пайплайн без ошибок
- [ ] Все медиа публикуются без искажений
- [ ] Ручная коррекция не требуется
- [ ] Rate limits соблюдаются
- [ ] Токены безопасно хранятся в БД
- [ ] Логи структурированы и информативны

### Performance:
- [ ] Processing time < 10 минут на пост
- [ ] Success rate ≥ 95%
- [ ] Throughput: 5-10 постов/день

---

## 📊 Трекинг прогресса

| Компонент | Статус | Прогресс |
|-----------|--------|----------|
| Security Keys | ❌ | 0% |
| SQLAlchemy модели | ❌ | 0% |
| Repository Pattern | ❌ | 0% |
| БД интеграция в tasks | ⚠️ | 10% |
| S3/MinIO | ⚠️ | 30% |
| Telegram media download | ❌ | 0% |
| YouTube adapter | ❌ | 0% |
| Notifier service | ❌ | 0% |
| OAuth flows | ❌ | 0% |
| Webhooks | ⚠️ | 40% |
| Smart Crop | ⚠️ | 10% |
| Admin API | ❌ | 0% |
| Tests | ⚠️ | 20% |
| Monitoring | ⚠️ | 50% |

**Общий прогресс:** ~75-80% (архитектура и адаптеры готовы)

---

## 🚀 Следующие шаги

**Сегодня:**
1. Сгенерировать Security Keys
2. Начать SQLAlchemy модели

**Эта неделя:**
3. Repository pattern
4. Интеграция БД
5. S3/MinIO + Telegram download

**Следующая неделя:**
6. YouTube adapter
7. Notifier + OAuth
8. Тестирование

---

*Обновлено: 2025-01-XX*
