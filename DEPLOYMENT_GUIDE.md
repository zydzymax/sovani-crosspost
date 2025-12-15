# 🚀 Инструкция по развертыванию SoVAni Crosspost на VPS Ubuntu

## 📋 Содержание
1. [Подготовка VPS](#подготовка-vps)
2. [Подготовка файлов на Mac](#подготовка-файлов-на-mac)
3. [Передача проекта на VPS](#передача-проекта-на-vps)
4. [Установка зависимостей на VPS](#установка-зависимостей-на-vps)
5. [Конфигурация проекта](#конфигурация-проекта)
6. [Запуск системы](#запуск-системы)
7. [Проверка работоспособности](#проверка-работоспособности)
8. [Настройка автозапуска](#настройка-автозапуска)
9. [Мониторинг и обслуживание](#мониторинг-и-обслуживание)
10. [Troubleshooting](#troubleshooting)

---

## 🔧 1. Подготовка VPS

### Требования к серверу
- **OS**: Ubuntu 20.04 LTS или новее
- **RAM**: минимум 4GB, рекомендуется 8GB
- **CPU**: минимум 2 ядра, рекомендуется 4
- **Disk**: минимум 20GB SSD, рекомендуется 50GB
- **Network**: минимум 100 Mbps

### Подключение к VPS

На вашем Mac выполните:

```bash
# Замените your-server-ip на IP вашего VPS
ssh root@your-server-ip

# Или если настроен пользователь
ssh username@your-server-ip
```

### Обновление системы

```bash
# Обновляем списки пакетов
sudo apt update

# Устанавливаем обновления
sudo apt upgrade -y

# Устанавливаем базовые утилиты
sudo apt install -y curl wget git htop nano vim unzip
```

### Создание пользователя для приложения

```bash
# Создаем пользователя sovani
sudo useradd -m -s /bin/bash sovani

# Добавляем в группу sudo
sudo usermod -aG sudo sovani

# Устанавливаем пароль
sudo passwd sovani

# Переключаемся на нового пользователя
sudo su - sovani
```

---

## 📦 2. Подготовка файлов на Mac

### Создание архива проекта

На вашем Mac в терминале:

```bash
# Переходим в директорию с проектом
cd /Users/fbi/sovani_crosspost

# Создаем архив (исключая ненужные файлы)
tar -czf sovani_crosspost.tar.gz \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='.env' \
  --exclude='*.log' \
  .

# Проверяем размер архива
ls -lh sovani_crosspost.tar.gz
```

### Альтернативно: создание Git репозитория

```bash
# Инициализируем Git репозиторий (если еще не сделано)
cd /Users/fbi/sovani_crosspost
git init

# Создаем .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Environment files
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/
*.swp
*.swo

# Logs
*.log
logs/

# Docker volumes
postgres_data/
redis_data/
minio_data/
media_cache/

# OS
.DS_Store
Thumbs.db
EOF

# Добавляем файлы и делаем коммит
git add .
git commit -m "Initial commit: SoVAni Crosspost MVP"
```

---

## 📤 3. Передача проекта на VPS

### Вариант 1: Передача через SCP

На Mac:

```bash
# Передаем архив на VPS
scp sovani_crosspost.tar.gz sovani@your-server-ip:/home/sovani/

# Подключаемся к VPS
ssh sovani@your-server-ip

# На VPS распаковываем архив
cd /home/sovani
tar -xzf sovani_crosspost.tar.gz
mv sovani_crosspost crosspost_app
cd crosspost_app
```

### Вариант 2: Клонирование из Git (если настроен репозиторий)

```bash
# На VPS
cd /home/sovani
git clone https://github.com/your-username/sovani_crosspost.git crosspost_app
cd crosspost_app
```

### Вариант 3: Прямая передача через rsync

На Mac:

```bash
# Синхронизируем директорию с исключениями
rsync -avz --exclude='__pycache__' \
           --exclude='*.pyc' \
           --exclude='.git' \
           --exclude='venv' \
           --exclude='.env' \
           /Users/fbi/sovani_crosspost/ \
           sovani@your-server-ip:/home/sovani/crosspost_app/
```

---

## ⚙️ 4. Установка зависимостей на VPS

### Docker и Docker Compose

```bash
# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Добавляем пользователя в группу docker
sudo usermod -aG docker sovani

# Перезапускаем сессию для применения изменений
sudo su - sovani

# Проверяем установку Docker
docker --version
```

### Docker Compose (последняя версия)

```bash
# Скачиваем последнюю версию Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose

# Делаем исполняемым
sudo chmod +x /usr/local/bin/docker-compose

# Проверяем установку
docker-compose --version
```

### Python (для локальной разработки и скриптов)

```bash
# Устанавливаем Python 3.11
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Создаем симлинк
sudo ln -sf /usr/bin/python3.11 /usr/bin/python3
```

### Дополнительные системные пакеты

```bash
# FFmpeg для обработки медиа
sudo apt install -y ffmpeg

# Утилиты для работы с изображениями
sudo apt install -y imagemagick

# Инструменты для мониторинга
sudo apt install -y htop iotop nethogs

# Настройка Nginx (опционально, для reverse proxy)
sudo apt install -y nginx
sudo systemctl enable nginx
```

---

## 🔧 5. Конфигурация проекта

### Настройка переменных окружения

```bash
# Переходим в директорию проекта
cd /home/sovani/crosspost_app

# Копируем пример конфигурации
cp env.example .env

# Редактируем конфигурацию
nano .env
```

### Пример продакшн конфигурации .env:

```bash
# Application
APP_ENV=production
LOG_LEVEL=INFO
SECRET_KEY=$(openssl rand -hex 32)

# Database
DATABASE_URL=postgresql://sovani:$(openssl rand -hex 16)@postgres:5432/sovani_crosspost
POSTGRES_DB=sovani_crosspost
POSTGRES_USER=sovani
POSTGRES_PASSWORD=$(openssl rand -hex 16)

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0

# MinIO
S3_ACCESS_KEY=[REVOKED_SECRET_REMOVED]
S3_SECRET_KEY=$(openssl rand -hex 20)
S3_BUCKET_NAME=sovani-media

# === ВАЖНО: Заполните реальными API ключами ===
# Instagram
INSTAGRAM_ACCESS_TOKEN=your-instagram-token
INSTAGRAM_BUSINESS_ACCOUNT_ID=your-instagram-account-id

# VK
VK_ACCESS_TOKEN=your-vk-token
VK_GROUP_ID=your-vk-group-id

# Telegram
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-admin-chat-id

# TikTok
TIKTOK_ACCESS_TOKEN=your-tiktok-token
TIKTOK_CLIENT_KEY=your-tiktok-client-key

# YouTube
YOUTUBE_CLIENT_ID=your-youtube-client-id
YOUTUBE_CLIENT_SECRET=[REVOKED_SECRET_REMOVED]
YOUTUBE_REFRESH_TOKEN=your-youtube-refresh-token

# OpenAI
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
```

### Настройка Docker Compose для продакшн

Создайте `docker-compose.prod.yml`:

```bash
cat > docker-compose.prod.yml << 'EOF'
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: sovani_postgres
    env_file: .env
    ports:
      - "127.0.0.1:5432:5432"  # Привязываем только к localhost
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sovani -d sovani_crosspost"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 1G
        reservations:
          memory: 512M

  redis:
    image: redis:7-alpine
    container_name: sovani_redis
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  minio:
    image: minio/minio:latest
    container_name: sovani_minio
    env_file: .env
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
    container_name: sovani_api
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
          memory: 1G
        reservations:
          memory: 256M

  worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    container_name: sovani_worker
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
          memory: 2G
        reservations:
          memory: 512M

  beat:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: sovani_beat
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
EOF
```

### Настройка логирования

```bash
# Создаем директорию для логов
mkdir -p /home/sovani/crosspost_app/logs

# Настраиваем logrotate
sudo tee /etc/logrotate.d/sovani_crosspost << 'EOF'
/home/sovani/crosspost_app/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    sharedscripts
    postrotate
        docker kill -s USR1 sovani_api 2>/dev/null || true
        docker kill -s USR1 sovani_worker 2>/dev/null || true
    endscript
}
EOF
```

---

## 🚀 6. Запуск системы

### Первоначальный запуск

```bash
cd /home/sovani/crosspost_app

# Проверяем конфигурацию Docker Compose
docker-compose -f docker-compose.prod.yml config

# Скачиваем образы
docker-compose -f docker-compose.prod.yml pull

# Собираем образы приложения
docker-compose -f docker-compose.prod.yml build

# Запускаем базовые сервисы сначала
docker-compose -f docker-compose.prod.yml up -d postgres redis minio

# Ждем 30 секунд для инициализации
sleep 30

# Проверяем статус базовых сервисов
docker-compose -f docker-compose.prod.yml ps

# Запускаем все остальные сервисы
docker-compose -f docker-compose.prod.yml up -d
```

### Инициализация базы данных

```bash
# Применяем миграции
docker-compose -f docker-compose.prod.yml exec api python -c "
from app.models.db import init_db
init_db()
print('Database initialized')
"

# Альтернативно через SQL файлы
docker-compose -f docker-compose.prod.yml exec postgres psql -U sovani -d sovani_crosspost -f /docker-entrypoint-initdb.d/0001_init.sql
```

### Создание MinIO bucket

```bash
# Подключаемся к MinIO контейнеру
docker-compose -f docker-compose.prod.yml exec minio sh

# Внутри контейнера MinIO
mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD
mc mb local/sovani-media
mc policy set public local/sovani-media
exit
```

---

## ✅ 7. Проверка работоспособности

### Проверка статуса сервисов

```bash
# Статус всех контейнеров
docker-compose -f docker-compose.prod.yml ps

# Логи всех сервисов
docker-compose -f docker-compose.prod.yml logs -f --tail=50

# Проверка здоровья сервисов
curl http://localhost:8000/health
curl http://localhost:9000/minio/health/live
```

### Тестирование API

```bash
# Проверка API документации
curl http://localhost:8000/docs

# Тест создания поста
curl -X POST "http://localhost:8000/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {"message": "Test post from VPS"},
    "platforms": ["instagram"]
  }'
```

### Проверка очередей Celery

```bash
# Активные задачи
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app inspect active

# Статистика очередей
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app inspect stats

# Мониторинг событий
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app events
```

### Проверка подключений к базе данных

```bash
# PostgreSQL
docker-compose -f docker-compose.prod.yml exec postgres \
  psql -U sovani -d sovani_crosspost -c "SELECT version();"

# Redis
docker-compose -f docker-compose.prod.yml exec redis \
  redis-cli ping
```

---

## 🔄 8. Настройка автозапуска

### Создание systemd службы

```bash
# Создаем service файл
sudo tee /etc/systemd/system/sovani-crosspost.service << 'EOF'
[Unit]
Description=SoVAni Crosspost Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/sovani/crosspost_app
ExecStart=/usr/local/bin/docker-compose -f docker-compose.prod.yml up -d
ExecStop=/usr/local/bin/docker-compose -f docker-compose.prod.yml down
TimeoutStartSec=300
User=sovani
Group=sovani

[Install]
WantedBy=multi-user.target
EOF

# Перезагружаем systemd
sudo systemctl daemon-reload

# Включаем автозапуск
sudo systemctl enable sovani-crosspost

# Запускаем службу
sudo systemctl start sovani-crosspost

# Проверяем статус
sudo systemctl status sovani-crosspost
```

### Настройка автообновления

```bash
# Создаем скрипт обновления
tee /home/sovani/crosspost_app/update.sh << 'EOF'
#!/bin/bash
set -e

echo "Starting SoVAni Crosspost update..."

cd /home/sovani/crosspost_app

# Останавливаем сервисы
docker-compose -f docker-compose.prod.yml down

# Создаем бэкап базы данных
docker run --rm -v postgres_data:/var/lib/postgresql/data \
  -v $(pwd)/backups:/backup postgres:15-alpine \
  tar czf /backup/postgres-$(date +%Y%m%d_%H%M%S).tar.gz /var/lib/postgresql/data

# Получаем обновления (если используется Git)
if [ -d .git ]; then
    git pull origin main
fi

# Пересобираем образы
docker-compose -f docker-compose.prod.yml build --no-cache

# Запускаем сервисы
docker-compose -f docker-compose.prod.yml up -d

echo "Update completed successfully!"
EOF

# Делаем скрипт исполняемым
chmod +x /home/sovani/crosspost_app/update.sh

# Создаем cron job для автообновлений (опционально)
# (crontab -l 2>/dev/null; echo "0 3 * * 1 /home/sovani/crosspost_app/update.sh >> /home/sovani/crosspost_app/logs/update.log 2>&1") | crontab -
```

---

## 📊 9. Мониторинг и обслуживание

### Настройка мониторинга ресурсов

```bash
# Установка htop для мониторинга системы
sudo apt install -y htop iotop nethogs

# Создание скрипта мониторинга
tee /home/sovani/monitor.sh << 'EOF'
#!/bin/bash

echo "=== System Resources ==="
echo "CPU and Memory:"
htop -n 1 | head -10

echo -e "\n=== Disk Usage ==="
df -h

echo -e "\n=== Docker Containers ==="
cd /home/sovani/crosspost_app
docker-compose -f docker-compose.prod.yml ps

echo -e "\n=== Container Resources ==="
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}"

echo -e "\n=== Service Health ==="
curl -s http://localhost:8000/health | python3 -m json.tool || echo "API not responding"

echo -e "\n=== Recent Logs ==="
docker-compose -f docker-compose.prod.yml logs --tail=5 api worker
EOF

chmod +x /home/sovani/monitor.sh
```

### Настройка алертов

```bash
# Создание скрипта проверки здоровья
tee /home/sovani/healthcheck.sh << 'EOF'
#!/bin/bash

TELEGRAM_BOT_TOKEN="your-bot-token"
TELEGRAM_CHAT_ID="your-chat-id"

send_alert() {
    local message="$1"
    curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d "chat_id=$TELEGRAM_CHAT_ID" \
        -d "text=🚨 SoVAni Crosspost Alert: $message"
}

# Проверка API
if ! curl -s -f http://localhost:8000/health > /dev/null; then
    send_alert "API is not responding"
fi

# Проверка использования диска
DISK_USAGE=$(df -h /home/sovani | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
    send_alert "Disk usage is $DISK_USAGE%"
fi

# Проверка использования памяти
MEMORY_USAGE=$(free | awk 'FNR==2{printf "%.0f", $3/$2*100}')
if [ "$MEMORY_USAGE" -gt 90 ]; then
    send_alert "Memory usage is $MEMORY_USAGE%"
fi

# Проверка контейнеров
cd /home/sovani/crosspost_app
STOPPED_CONTAINERS=$(docker-compose -f docker-compose.prod.yml ps | grep "Exit" | wc -l)
if [ "$STOPPED_CONTAINERS" -gt 0 ]; then
    send_alert "$STOPPED_CONTAINERS containers are stopped"
fi
EOF

chmod +x /home/sovani/healthcheck.sh

# Добавляем в crontab проверку каждые 5 минут
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/sovani/healthcheck.sh") | crontab -
```

### Настройка backup'ов

```bash
# Создание скрипта бэкапа
tee /home/sovani/backup.sh << 'EOF'
#!/bin/bash

BACKUP_DIR="/home/sovani/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

echo "Starting backup at $(date)"

# Бэкап базы данных
docker-compose -f /home/sovani/crosspost_app/docker-compose.prod.yml exec -T postgres \
    pg_dump -U sovani sovani_crosspost | gzip > "$BACKUP_DIR/db_$DATE.sql.gz"

# Бэкап конфигурации
tar -czf "$BACKUP_DIR/config_$DATE.tar.gz" -C /home/sovani/crosspost_app \
    .env docker-compose.prod.yml config/

# Бэкап MinIO данных (если нужно)
docker run --rm -v minio_data:/data -v $BACKUP_DIR:/backup alpine:latest \
    tar czf /backup/minio_$DATE.tar.gz /data

# Удаляем старые бэкапы (старше 30 дней)
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete

echo "Backup completed at $(date)"
EOF

chmod +x /home/sovani/backup.sh

# Добавляем в crontab ежедневный бэкап в 2:00
(crontab -l 2>/dev/null; echo "0 2 * * * /home/sovani/backup.sh >> /home/sovani/logs/backup.log 2>&1") | crontab -
```

---

## 🛡️ 10. Настройка безопасности

### Конфигурация firewall

```bash
# Включаем UFW
sudo ufw enable

# Разрешаем SSH
sudo ufw allow ssh

# Разрешаем HTTP/HTTPS для Nginx
sudo ufw allow 'Nginx Full'

# Блокируем прямой доступ к сервисам приложения
sudo ufw deny 5432  # PostgreSQL
sudo ufw deny 6379  # Redis
sudo ufw deny 8000  # API (если не нужен внешний доступ)
sudo ufw deny 9000  # MinIO
sudo ufw deny 9001  # MinIO Console

# Проверяем статус
sudo ufw status numbered
```

### Настройка Nginx как reverse proxy

```bash
# Создаем конфигурацию Nginx
sudo tee /etc/nginx/sites-available/sovani-crosspost << 'EOF'
server {
    listen 80;
    server_name your-domain.com;  # Замените на ваш домен

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    location / {
        limit_req zone=api burst=20 nodelay;
        
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # Buffer sizes
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_busy_buffers_size 8k;
    }

    # MinIO Console (опционально, для админа)
    location /minio/ {
        allow your-admin-ip;  # Замените на ваш IP
        deny all;
        
        proxy_pass http://127.0.0.1:9001/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Disable access to sensitive files
    location ~ /\. {
        deny all;
    }
}
EOF

# Активируем сайт
sudo ln -s /etc/nginx/sites-available/sovani-crosspost /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Проверяем конфигурацию
sudo nginx -t

# Перезапускаем Nginx
sudo systemctl restart nginx
```

### SSL-сертификат через Let's Encrypt

```bash
# Устанавливаем Certbot
sudo apt install -y certbot python3-certbot-nginx

# Получаем сертификат
sudo certbot --nginx -d your-domain.com

# Настраиваем автообновление
sudo crontab -l | { cat; echo "0 12 * * * /usr/bin/certbot renew --quiet"; } | sudo crontab -
```

---

## 🔧 11. Troubleshooting

### Общие проблемы и решения

#### Проблема: Контейнеры не запускаются

```bash
# Проверка логов
docker-compose -f docker-compose.prod.yml logs

# Проверка системных ресурсов
df -h  # Проверка места на диске
free -h  # Проверка памяти

# Очистка Docker
docker system prune -f
docker volume prune -f
```

#### Проблема: База данных не подключается

```bash
# Проверка подключения
docker-compose -f docker-compose.prod.yml exec postgres \
  psql -U sovani -d sovani_crosspost -c "SELECT 1;"

# Пересоздание базы данных
docker-compose -f docker-compose.prod.yml down -v
docker volume rm crosspost_app_postgres_data
docker-compose -f docker-compose.prod.yml up -d postgres
```

#### Проблема: Celery worker не обрабатывает задачи

```bash
# Проверка активных воркеров
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app inspect active

# Очистка очередей
docker-compose -f docker-compose.prod.yml exec worker \
  celery -A app.workers.celery_app purge

# Перезапуск воркера
docker-compose -f docker-compose.prod.yml restart worker
```

#### Проблема: MinIO не работает

```bash
# Проверка здоровья MinIO
curl http://localhost:9000/minio/health/live

# Проверка логов MinIO
docker-compose -f docker-compose.prod.yml logs minio

# Пересоздание bucket
docker-compose -f docker-compose.prod.yml exec minio \
  mc mb local/sovani-media --ignore-existing
```

### Логи для диагностики

```bash
# Все логи
docker-compose -f docker-compose.prod.yml logs -f

# Конкретный сервис
docker-compose -f docker-compose.prod.yml logs -f api

# С фильтром по ошибкам
docker-compose -f docker-compose.prod.yml logs | grep ERROR

# Системные логи
sudo journalctl -u sovani-crosspost -f
```

### Полезные команды для обслуживания

```bash
# Рестарт всех сервисов
sudo systemctl restart sovani-crosspost

# Обновление только кода без пересборки
cd /home/sovani/crosspost_app
git pull origin main
docker-compose -f docker-compose.prod.yml restart api worker beat

# Масштабирование воркеров
docker-compose -f docker-compose.prod.yml up -d --scale worker=3

# Просмотр метрик контейнеров
docker stats

# Подключение к контейнеру для отладки
docker-compose -f docker-compose.prod.yml exec api bash
```

---

## 📞 Поддержка

При возникновении проблем:

1. **Проверьте логи**: `docker-compose logs -f`
2. **Проверьте ресурсы**: `htop`, `df -h`
3. **Проверьте сеть**: `netstat -tulpn`
4. **Проверьте переменные**: `docker-compose config`

### Контакты для поддержки
- 📧 Email: support@sovani.ru
- 💬 Telegram: @sovani_support
- 📚 Документация: [GitHub Repository]

---

**Успешного развертывания! 🚀**

*Последнее обновление инструкции: 2024-12-26*