# SoVAni Crosspost

[![CI](https://github.com/zydzymax/sovani-crosspost/actions/workflows/ci.yml/badge.svg)](https://github.com/zydzymax/sovani-crosspost/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Кроссплатформенная публикация контента из Telegram в социальные сети с автоматическим транскодированием медиа и адаптацией под требования каждой платформы.

> **Quick Start:** See [QUICK_START.md](QUICK_START.md) for fast setup.

## Архитектура

- **API**: FastAPI с асинхронной обработкой
- **Workers**: Celery с 7 специализированными очередями
- **База данных**: PostgreSQL с миграциями Alembic
- **Хранилище**: MinIO (S3-compatible) для медиафайлов  
- **Кеширование**: Redis для Celery и кеша
- **Медиа**: FFmpeg для транскодирования без искажений

## Поддерживаемые платформы

- ✅ **Instagram** (посты, сторис, рилсы)
- ✅ **VK** (посты, видео)
- ✅ **Telegram** (каналы, боты)
- ✅ **TikTok** (видео как drafts)
- ✅ **YouTube** (видео, shorts)

## Локальный запуск

### Предварительные требования

- Docker и Docker Compose
- Python 3.11+
- Git

### Быстрый старт

```bash
# 1. Клонирование и переход в директорию
git clone <repository-url>
cd sovani_crosspost

# 2. Настройка переменных окружения
cp .env.example .env
# Отредактируйте .env с вашими API ключами

# 3. Сборка и запуск всех сервисов
docker-compose up -d

# 4. Установка Python зависимостей (для локальной разработки)
pip install -r requirements.txt

# 5. Проверка статуса сервисов
docker-compose ps
```

### Проверка запуска

- **API**: http://localhost:8000/docs (Swagger UI)
- **MinIO Console**: http://localhost:9001 (admin/minioadmin123)
- **PostgreSQL**: localhost:5432 (sovani/sovani_pass)
- **Redis**: localhost:6379

## Миграции базы данных

```bash
# Автоматические миграции при запуске через docker-compose
docker-compose up postgres

# Ручное применение миграций (если нужно)
docker-compose exec api alembic upgrade head

# Создание новой миграции
docker-compose exec api alembic revision --autogenerate -m "Migration description"

# Откат миграций
docker-compose exec api alembic downgrade -1
```

## Тестирование

### Smoke тесты (проверка основных компонентов)

```bash
# Запуск базовых smoke тестов
python -m pytest app/tests/test_e2e_smoke.py -v

# Проверка подключения к сервисам
curl http://localhost:8000/health
curl http://localhost:8000/metrics

# Проверка очередей Celery
docker-compose exec worker celery -A app.workers.celery_app inspect active
```

### Тестовая публикация

```bash
# 1. Создание тестового поста через API
curl -X POST "http://localhost:8000/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "telegram", 
    "source_data": {"message": "Test post"},
    "platforms": ["instagram", "vk", "tiktok"]
  }'

# 2. Мониторинг выполнения задач
docker-compose logs -f worker

# 3. Проверка статуса в админ-панели
curl "http://localhost:8000/api/posts/{post_id}/status"
```

## Разработка

### Структура проекта

```
app/
├── api/           # FastAPI роуты и зависимости
├── workers/       # Celery задачи по очередям
├── adapters/      # Интеграции с внешними API
├── services/      # Бизнес-логика
├── models/        # SQLAlchemy модели
└── core/          # Конфигурация и утилиты
```

### Очереди обработки

1. **ingest** - Прием и валидация контента
2. **enrich** - Обогащение метаданными
3. **captionize** - Генерация описаний через LLM
4. **transcode** - Конвертация медиа под платформы
5. **preflight** - Финальная проверка перед публикацией
6. **publish** - Публикация на платформах
7. **finalize** - Уведомления и очистка

### Мониторинг воркеров

```bash
# Статистика очередей
docker-compose exec worker celery -A app.workers.celery_app inspect stats

# Активные задачи
docker-compose exec worker celery -A app.workers.celery_app inspect active

# Мониторинг в реальном времени
docker-compose exec worker celery -A app.workers.celery_app events
```

## Цели MVP

✅ **Критерий готовности**: 10 постов подряд проходят весь пайплайн:
- Без ручных правок медиа
- Без искажений пропорций
- С корректными форматами для каждой платформы
- Со статусами и ссылками в админ-канале Telegram

## Troubleshooting

### Частые проблемы

1. **Сервисы не запускаются**
   ```bash
   docker-compose down -v  # Очистка volumes
   docker-compose build --no-cache
   docker-compose up -d
   ```

2. **FFmpeg не найден**
   ```bash
   docker-compose exec worker apt-get update
   docker-compose exec worker apt-get install -y ffmpeg
   ```

3. **Ошибки подключения к MinIO**
   ```bash
   # Создание bucket вручную
   docker-compose exec minio mc mb local/sovani-media
   ```

4. **Проблемы с правами доступа**
   ```bash
   sudo chown -R $USER:$USER ./
   ```

### Логи

```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f worker
docker-compose logs -f api

# Последние N строк
docker-compose logs --tail=100 worker
```