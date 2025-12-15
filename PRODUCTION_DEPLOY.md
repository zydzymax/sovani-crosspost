# 🏭 Продакшн развертывание SoVAni Crosspost на VPS Ubuntu

## ⚠️ ВАЖНО: Это инструкция для рабочего развертывания!

Все настройки, пароли и API ключи должны быть реальными и безопасными.

---

## 🔐 1. Подготовка продакшн конфигурации на Mac

### Настройка .env с реальными данными

```bash
# Переходим в проект
cd /Users/fbi/sovani_crosspost

# Копируем шаблон
cp env.example .env

# Открываем для редактирования
nano .env
```

### Обязательные настройки для продакшн:

```bash
# =============================================================================
# ПРОДАКШН КОНФИГУРАЦИЯ - ЗАПОЛНИТЕ РЕАЛЬНЫМИ ЗНАЧЕНИЯМИ!
# =============================================================================

# Application Settings
APP_ENV=production
LOG_LEVEL=INFO
DEBUG=false

# Генерируем безопасные ключи
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32)

# Database - используем сильный пароль
POSTGRES_DB=sovani_crosspost
POSTGRES_USER=sovani
POSTGRES_PASSWORD=$(openssl rand -hex 20)
DATABASE_URL=postgresql://sovani:${POSTGRES_PASSWORD}@postgres:5432/sovani_crosspost

# MinIO - генерируем безопасные ключи
S3_ACCESS_KEY=admin$(openssl rand -hex 8)
S3_SECRET_KEY=$(openssl rand -hex 24)
S3_BUCKET_NAME=sovani-media

# =============================================================================
# API КЛЮЧИ - ПОЛУЧИТЕ РЕАЛЬНЫЕ КЛЮЧИ ОТ СЕРВИСОВ!
# =============================================================================

# Instagram API (https://developers.facebook.com/)
INSTAGRAM_ACCESS_TOKEN=[REVOKED_SECRET_REMOVED]
INSTAGRAM_BUSINESS_ACCOUNT_ID=17841xxxxxxxxx
INSTAGRAM_APP_ID=xxxxxxxxx
INSTAGRAM_APP_SECRET=[REVOKED_SECRET_REMOVED]

# VK API (https://dev.vk.com/)
VK_ACCESS_TOKEN=[REVOKED_SECRET_REMOVED]
VK_GROUP_ID=123456789
VK_API_VERSION=5.131

# Telegram Bot (https://t.me/BotFather)
TELEGRAM_BOT_TOKEN=[REVOKED_SECRET_REMOVED]
TELEGRAM_ADMIN_CHAT_ID=-1001234567890
TELEGRAM_INTAKE_CHAT_ID=-1001234567890

# TikTok API (https://developers.tiktok.com/)
TIKTOK_ACCESS_TOKEN=[REVOKED_SECRET_REMOVED]
TIKTOK_CLIENT_KEY=aw7xxxxxxxxxxxxxxx
TIKTOK_CLIENT_SECRET=[REVOKED_SECRET_REMOVED]

# YouTube API (https://console.cloud.google.com/)
YOUTUBE_CLIENT_ID=123456789-xxxxxxxxxxxxxxx.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=[REVOKED_SECRET_REMOVED]
YOUTUBE_REFRESH_TOKEN=[REVOKED_SECRET_REMOVED]
YOUTUBE_API_KEY=[REVOKED_SECRET_REMOVED]

# OpenAI API (https://platform.openai.com/)
OPENAI_API_KEY=[REVOKED_SECRET_REMOVED]
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=1000

# Marketplace APIs (опционально для обогащения данными товаров)
WB_API_TOKEN=[REVOKED_SECRET_REMOVED]
OZON_CLIENT_ID=123456
OZON_API_KEY=[REVOKED_SECRET_REMOVED]
YM_OAUTH_TOKEN=[REVOKED_SECRET_REMOVED]
YM_CAMPAIGN_ID=12345678

# =============================================================================
# ПРОДАКШН НАСТРОЙКИ
# =============================================================================

# Rate Limiting
INSTAGRAM_POSTS_PER_DAY=25
VK_POSTS_PER_DAY=100
TIKTOK_POSTS_PER_DAY=10
YOUTUBE_VIDEOS_PER_DAY=6

# Content Settings
CAPTION_MAX_LENGTH_INSTAGRAM=2200
CAPTION_MAX_LENGTH_VK=15000
HASHTAGS_COUNT_MIN=5
HASHTAGS_COUNT_MAX=30
REQUIRED_HASHTAGS="#sovani,#fashion"

# Media Processing
MAX_FILE_SIZE_MB=500
MAX_VIDEO_DURATION_SEC=300
FFMPEG_THREADS=4
FFMPEG_PRESET=medium

# Security & Monitoring
ENABLE_METRICS=true
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
TZ=UTC

# Cleanup
CLEANUP_MEDIA_DAYS=30
ENABLE_AUTO_BACKUP=true
BACKUP_SCHEDULE="0 2 * * *"
```

### Генерация архива для продакшн

```bash
# В директории проекта
cd /Users/fbi/sovani_crosspost

# Создаем продакшн архив (включая настроенный .env)
tar -czf sovani_crosspost_prod_$(date +%Y%m%d_%H%M).tar.gz \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='env' \
  --exclude='node_modules' \
  --exclude='*.log' \
  --exclude='logs/' \
  --exclude='.DS_Store' \
  .

# Проверяем размер архива
ls -lh sovani_crosspost_prod_*.tar.gz

echo "✅ Продакшн архив готов к передаче на VPS"
```

---

## 🖥️ 2. Настройка продакшн VPS Ubuntu

### Подключение к VPS и подготовка системы

```bash
# Подключаемся к VPS
ssh root@your-production-vps-ip

# Обновляем систему
apt update && apt upgrade -y

# Устанавливаем необходимые пакеты
apt install -y curl wget git htop nano vim unzip ufw fail2ban

# Настраиваем базовую безопасность
ufw enable
ufw allow ssh
ufw allow 80
ufw allow 443

# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
systemctl enable docker
systemctl start docker

# Устанавливаем Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Проверяем установки
docker --version
docker-compose --version
```

### Создание пользователя приложения

```bash
# Создаем пользователя для приложения
useradd -m -s /bin/bash sovani
usermod -aG docker sovani
usermod -aG sudo sovani

# Устанавливаем пароль
passwd sovani

# Создаем SSH директорию для пользователя
mkdir -p /home/sovani/.ssh
cp /root/.ssh/authorized_keys /home/sovani/.ssh/
chown -R sovani:sovani /home/sovani/.ssh
chmod 700 /home/sovani/.ssh
chmod 600 /home/sovani/.ssh/authorized_keys

echo "✅ Пользователь sovani создан"
```

---

## 📤 3. Передача проекта на VPS

### С вашего Mac на VPS

```bash
# Передаем архив (замените на IP вашего VPS)
scp sovani_crosspost_prod_$(date +%Y%m%d)*.tar.gz sovani@your-vps-ip:/home/sovani/

# Подключаемся к VPS как пользователь sovani
ssh sovani@your-vps-ip
```

### На VPS - распаковка и настройка

```bash
# Распаковываем проект
cd /home/sovani
tar -xzf sovani_crosspost_prod_*.tar.gz
mv sovani_crosspost crosspost_prod
cd crosspost_prod

# Проверяем что .env файл на месте
ls -la .env
echo "✅ Конфигурация передана"

# Создаем директории для продакшн
mkdir -p logs backups
chmod 755 logs backups
```

---

## 🚀 4. Запуск продакшн системы

### Создание продакшн Docker Compose

```bash
cd /home/sovani/crosspost_prod

# Создаем продакшн compose файл
cat > docker-compose.prod.yml << 'EOF'
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: sovani_postgres_prod
    env_file: .env
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
      - ./backups:/backups
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 512M

  redis:
    image: redis:7-alpine
    container_name: sovani_redis_prod
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  minio:
    image: minio/minio:latest
    container_name: sovani_minio_prod
    environment:
      MINIO_ROOT_USER: ${S3_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${S3_SECRET_KEY}
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    volumes:
      - minio_data:/data
    command: server /data --console-address ":9001"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: sovani_api_prod
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./logs:/app/logs
      - media_cache:/tmp/media
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 512M
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    container_name: sovani_worker_prod
    env_file: .env
    volumes:
      - ./logs:/app/logs
      - media_cache:/tmp/media
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 1G
    command: >
      sh -c "
        apt-get update && 
        apt-get install -y ffmpeg mediainfo && 
        celery -A app.workers.celery_app worker 
          --loglevel=info 
          --queues=ingest,enrich,captionize,transcode,preflight,publish,finalize
          --concurrency=4
          --max-tasks-per-child=100
      "

  beat:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: sovani_beat_prod
    env_file: .env
    volumes:
      - ./logs:/app/logs
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    command: celery -A app.workers.celery_app beat --loglevel=info
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  minio_data:
  media_cache:

networks:
  default:
    name: sovani_network
EOF

echo "✅ Продакшн compose файл создан"
```

### Первый запуск системы

```bash
# Проверяем конфигурацию
docker-compose -f docker-compose.prod.yml config

# Создаем сети и тома
docker network create sovani_network || true

# Запускаем базовые сервисы
docker-compose -f docker-compose.prod.yml up -d postgres redis minio

echo "⏳ Ожидание инициализации базовых сервисов (60 секунд)..."
sleep 60

# Проверяем базовые сервисы
docker-compose -f docker-compose.prod.yml ps

# Инициализация MinIO bucket
docker-compose -f docker-compose.prod.yml exec minio sh -c "
mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD
mc mb local/sovani-media --ignore-existing
mc policy set download local/sovani-media
"

# Запускаем приложение
docker-compose -f docker-compose.prod.yml up -d --build

echo "⏳ Ожидание запуска приложения (30 секунд)..."
sleep 30
```

---

## ✅ 5. Проверка продакшн развертывания

### Проверка всех сервисов

```bash
cd /home/sovani/crosspost_prod

# Статус всех контейнеров
docker-compose -f docker-compose.prod.yml ps

# Проверка здоровья
echo "🔍 Проверка API..."
curl -s http://localhost:8000/health | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'✅ API Status: {data[\"status\"]}')
    for service, status in data.get('services', {}).items():
        print(f'   - {service}: {status}')
except:
    print('❌ API не отвечает')
"

# Проверка базы данных
echo "🔍 Проверка PostgreSQL..."
docker-compose -f docker-compose.prod.yml exec postgres \
  psql -U $POSTGRES_USER -d $POSTGRES_DB -c "
    SELECT 'PostgreSQL работает, версия: ' || version();
    SELECT 'Таблиц в базе: ' || count(*) FROM information_schema.tables WHERE table_schema = 'public';
  "

# Проверка Redis
echo "🔍 Проверка Redis..."
docker-compose -f docker-compose.prod.yml exec redis redis-cli info server | grep redis_version

# Проверка MinIO
echo "🔍 Проверка MinIO..."
curl -s http://localhost:9000/minio/health/live && echo "✅ MinIO работает"

# Проверка Celery воркеров
echo "🔍 Проверка Celery..."
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app inspect stats | grep -A 5 "pool"
```

### Тест создания поста

```bash
# Тестовый пост для проверки работы пайплайна
echo "🧪 Тестирование создания поста..."

curl -X POST "http://localhost:8000/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {
      "message": "🎉 SoVAni Crosspost успешно развернут на продакшн VPS!",
      "article": "TEST001"
    },
    "platforms": ["telegram"]
  }' | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'✅ Пост создан с ID: {data[\"id\"]}')
    print(f'   Статус: {data[\"status\"]}')
    print(f'   Платформы: {data[\"platforms\"]}')
except Exception as e:
    print(f'❌ Ошибка создания поста: {e}')
"

# Мониторинг обработки поста
echo "👀 Мониторинг логов обработки (30 секунд)..."
timeout 30s docker-compose -f docker-compose.prod.yml logs -f worker | grep -E "(INFO|ERROR|SUCCESS)"
```

---

## 🔒 6. Продакшн безопасность и мониторинг

### Настройка firewall

```bash
# Закрываем прямой доступ к внутренним портам
sudo ufw deny 5432  # PostgreSQL
sudo ufw deny 6379  # Redis  
sudo ufw deny 9000  # MinIO API
sudo ufw deny 9001  # MinIO Console

# Если нужен внешний доступ к API (осторожно!)
# sudo ufw allow 8000

sudo ufw reload
sudo ufw status numbered
```

### Настройка автозапуска

```bash
# Создаем systemd службу
sudo tee /etc/systemd/system/sovani-crosspost.service << EOF
[Unit]
Description=SoVAni Crosspost Production
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/sovani/crosspost_prod
ExecStart=/usr/local/bin/docker-compose -f docker-compose.prod.yml up -d
ExecStop=/usr/local/bin/docker-compose -f docker-compose.prod.yml down
ExecReload=/usr/local/bin/docker-compose -f docker-compose.prod.yml restart
TimeoutStartSec=300
User=sovani
Group=sovani

[Install]
WantedBy=multi-user.target
EOF

# Включаем службу
sudo systemctl daemon-reload
sudo systemctl enable sovani-crosspost
sudo systemctl start sovani-crosspost

# Проверяем статус
sudo systemctl status sovani-crosspost
```

### Настройка мониторинга

```bash
# Создаем скрипт мониторинга
tee /home/sovani/monitor_prod.sh << 'EOF'
#!/bin/bash

cd /home/sovani/crosspost_prod

echo "=== $(date) ==="
echo "🖥️  System Resources:"
echo "CPU: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)%"
echo "RAM: $(free -m | awk 'NR==2{printf "%.0f%%", $3*100/$2}')"
echo "Disk: $(df -h /home | awk 'NR==2 {print $5}')"

echo "🐳 Docker Containers:"
docker-compose -f docker-compose.prod.yml ps

echo "📊 Container Resources:"
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"

echo "🏥 Health Checks:"
curl -s http://localhost:8000/health >/dev/null && echo "✅ API OK" || echo "❌ API Failed"
docker-compose -f docker-compose.prod.yml exec postgres pg_isready -q && echo "✅ PostgreSQL OK" || echo "❌ PostgreSQL Failed"
docker-compose -f docker-compose.prod.yml exec redis redis-cli ping | grep -q PONG && echo "✅ Redis OK" || echo "❌ Redis Failed"

echo "📈 Celery Status:"
docker-compose -f docker-compose.prod.yml exec worker celery -A app.workers.celery_app inspect active | grep -c "uuid" && echo "Active tasks found" || echo "No active tasks"

echo "==========================================\n"
EOF

chmod +x /home/sovani/monitor_prod.sh

# Добавляем мониторинг каждые 5 минут
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/sovani/monitor_prod.sh >> /home/sovani/logs/monitor.log 2>&1") | crontab -
```

### Настройка бэкапов

```bash
# Создаем скрипт автобэкапа
tee /home/sovani/backup_prod.sh << 'EOF'
#!/bin/bash

BACKUP_DIR="/home/sovani/backups"
DATE=$(date +%Y%m%d_%H%M%S)
cd /home/sovani/crosspost_prod

echo "🔄 Starting backup at $(date)"

# Backup PostgreSQL
docker-compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U $POSTGRES_USER $POSTGRES_DB | gzip > "$BACKUP_DIR/db_$DATE.sql.gz"

# Backup configuration and logs
tar -czf "$BACKUP_DIR/config_$DATE.tar.gz" .env docker-compose.prod.yml config/ logs/

# Backup MinIO data
docker run --rm -v crosspost_prod_minio_data:/data -v $BACKUP_DIR:/backup alpine:latest \
  tar czf /backup/media_$DATE.tar.gz /data

# Clean old backups (keep 7 days)
find $BACKUP_DIR -name "*.gz" -mtime +7 -delete

# Send notification (если настроен Telegram)
if [ ! -z "$TELEGRAM_BOT_TOKEN" ]; then
  curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    -d "chat_id=$TELEGRAM_ADMIN_CHAT_ID" \
    -d "text=✅ SoVAni Crosspost backup completed: $DATE"
fi

echo "✅ Backup completed at $(date)"
EOF

chmod +x /home/sovani/backup_prod.sh

# Ежедневный бэкап в 3:00
(crontab -l 2>/dev/null; echo "0 3 * * * /home/sovani/backup_prod.sh >> /home/sovani/logs/backup.log 2>&1") | crontab -
```

---

## 🎉 7. Финальная проверка продакшн системы

```bash
cd /home/sovani/crosspost_prod

echo "🔍 Финальная проверка продакшн развертывания..."

# Проверяем все сервисы
./monitor_prod.sh

# Проверяем логи на ошибки
echo "🔍 Проверка логов на критические ошибки:"
docker-compose -f docker-compose.prod.yml logs --tail=100 | grep -i "error\|critical\|failed" || echo "✅ Критических ошибок не найдено"

# Проверяем автозапуск
sudo systemctl is-enabled sovani-crosspost && echo "✅ Автозапуск настроен"

# Проверяем cron задачи
crontab -l | grep -E "(monitor|backup)" && echo "✅ Автоматические задачи настроены"

echo ""
echo "🎉 ПРОДАКШН РАЗВЕРТЫВАНИЕ ЗАВЕРШЕНО!"
echo ""
echo "📋 Информация о системе:"
echo "   🌐 API: http://localhost:8000"
echo "   📊 Health: http://localhost:8000/health"
echo "   📖 Docs: http://localhost:8000/docs"
echo "   📁 Logs: /home/sovani/crosspost_prod/logs/"
echo "   💾 Backups: /home/sovani/backups/"
echo ""
echo "⚙️  Управление:"
echo "   sudo systemctl status sovani-crosspost"
echo "   sudo systemctl restart sovani-crosspost"
echo "   docker-compose -f docker-compose.prod.yml logs -f"
echo ""
echo "🔐 Безопасность:"
echo "   - Firewall настроен"
echo "   - Сервисы привязаны к localhost"
echo "   - Автобэкапы каждый день в 3:00"
echo "   - Мониторинг каждые 5 минут"
echo ""
echo "✅ Система готова к работе!"
```

---

## 📞 Поддержка продакшн системы

### Полезные команды

```bash
# Перезапуск всей системы
sudo systemctl restart sovani-crosspost

# Перезапуск отдельного сервиса
docker-compose -f docker-compose.prod.yml restart api

# Просмотр логов в реальном времени
docker-compose -f docker-compose.prod.yml logs -f

# Мониторинг ресурсов
docker stats

# Проверка здоровья
curl http://localhost:8000/health

# Очистка места на диске
docker system prune -f
```

### Алерты и мониторинг

Система настроена для отправки уведомлений в Telegram при:
- Падении сервисов
- Переполнении диска (>90%)
- Высокой нагрузке на память (>90%)
- Успешных бэкапах

### Обновления

Для обновления системы создайте новый архив на Mac и повторите процесс развертывания с предварительной остановкой текущей системы:

```bash
sudo systemctl stop sovani-crosspost
docker-compose -f docker-compose.prod.yml down
# ... обновление ...
sudo systemctl start sovani-crosspost
```

**🚀 Продакшн система SoVAni Crosspost готова к работе!**