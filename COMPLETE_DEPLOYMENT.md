# 🚀 Полная инструкция развертывания SoVAni Crosspost на VPS Ubuntu

## 📋 Что получится в итоге
- Полнофункциональная система кросспостинга на 5 платформ
- Автоматическая обработка контента через 7 очередей Celery
- Безопасное хранение медиафайлов в MinIO S3
- Мониторинг и автоматические бэкапы
- SSL сертификаты и защищенный доступ

---

## 1️⃣ ПОДГОТОВКА НА MAC (5 минут)

### Настройка .env файла с реальными API ключами

```bash
# Переходим в папку проекта
cd /Users/fbi/sovani_crosspost

# Копируем шаблон конфигурации
cp env.example .env

# Открываем для редактирования
nano .env
```

**Обязательные ключи для заполнения:**

```bash
# =============================================================================
# КРИТИЧЕСКИ ВАЖНЫЕ НАСТРОЙКИ - ОБЯЗАТЕЛЬНО ЗАПОЛНИТЬ
# =============================================================================

# Безопасность (сгенерировать новые!)
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32) 
POSTGRES_PASSWORD=$(openssl rand -hex 16)
S3_SECRET_KEY=$(openssl rand -hex 20)

# Instagram API (Meta Business)
# https://developers.facebook.com/
INSTAGRAM_ACCESS_TOKEN=[REVOKED_SECRET_REMOVED]
INSTAGRAM_BUSINESS_ACCOUNT_ID=12345...ваш-id
INSTAGRAM_APP_ID=12345...ваш-app-id  
INSTAGRAM_APP_SECRET=[REVOKED_SECRET_REMOVED]

# VK API 
# https://dev.vk.com/
VK_ACCESS_TOKEN=[REVOKED_SECRET_REMOVED]
VK_GROUP_ID=12345...id-группы

# Telegram Bot
# Создать через @BotFather
TELEGRAM_BOT_TOKEN=[REVOKED_SECRET_REMOVED]
TELEGRAM_ADMIN_CHAT_ID=12345...ваш-chat-id

# OpenAI API
# https://platform.openai.com/
OPENAI_API_KEY=[REVOKED_SECRET_REMOVED]

# TikTok (опционально)
# https://developers.tiktok.com/
TIKTOK_ACCESS_TOKEN=act.example...ваш-токен
TIKTOK_CLIENT_KEY=aw123...ваш-ключ

# YouTube (опционально) 
# https://console.cloud.google.com/
YOUTUBE_CLIENT_ID=123...apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=[REVOKED_SECRET_REMOVED]
YOUTUBE_REFRESH_TOKEN=[REVOKED_SECRET_REMOVED]
```

### Генерация безопасных ключей

```bash
# Генерируем все необходимые секретные ключи
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" 
echo "S3_SECRET_KEY=$(openssl rand -hex 20)"

# Копируем результат в .env файл
```

### Создание архива для передачи на VPS

```bash
# Проверяем что .env настроен
head -10 .env

# Создаем полный архив включая .env (для продакшн)
tar -czf sovani_crosspost_production.tar.gz \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='*.log' \
  --exclude='downloads/' \
  --exclude='temp/' \
  .

# Проверяем размер архива
ls -lh sovani_crosspost_production.tar.gz
```

---

## 2️⃣ НАСТРОЙКА VPS UBUNTU (10 минут)

### Подключение к VPS и установка зависимостей

```bash
# Замените YOUR_VPS_IP на реальный IP адрес вашего VPS
VPS_IP="YOUR_VPS_IP"

# Передаем архив на VPS
scp sovani_crosspost_production.tar.gz root@${VPS_IP}:/root/

# Подключаемся к VPS
ssh root@${VPS_IP}
```

**На VPS выполняем:**

```bash
# Обновляем систему
apt update && apt upgrade -y

# Устанавливаем необходимые пакеты
apt install -y curl wget git nano htop ufw nginx certbot python3-certbot-nginx

# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
rm get-docker.sh

# Устанавливаем Docker Compose
DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d '"' -f 4)
curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Проверяем установку
docker --version
docker-compose --version
```

### Создание пользователя и настройка проекта

```bash
# Создаем пользователя для приложения
useradd -m -s /bin/bash sovani
usermod -aG docker sovani
mkdir -p /home/sovani

# Распаковываем проект
cd /root
tar -xzf sovani_crosspost_production.tar.gz
mv /root/sovani_crosspost /home/sovani/crosspost_app
chown -R sovani:sovani /home/sovani/crosspost_app

# Создаем директории для данных
mkdir -p /home/sovani/data/{postgres,redis,minio}
mkdir -p /home/sovani/logs
mkdir -p /home/sovani/backups
chown -R sovani:sovani /home/sovani/data /home/sovani/logs /home/sovani/backups

# Переключаемся на пользователя приложения
su - sovani
cd /home/sovani/crosspost_app
```

---

## 3️⃣ НАСТРОЙКА ПРОЕКТА (5 минут)

```bash
# Проверяем .env файл
cat .env | head -20

# При необходимости корректируем настройки для VPS
nano .env
```

**Обновляем для VPS (если нужно):**

```bash
# Основные настройки
APP_ENV=production
LOG_LEVEL=INFO
DEBUG=false

# База данных (пароль должен совпадать с сгенерированным)
DATABASE_URL=postgresql://sovani:[REVOKED_SECRET_REMOVED]@postgres:5432/sovani_crosspost

# S3 хранилище (ключ должен совпадать с сгенерированным)
S3_SECRET_KEY=[REVOKED_SECRET_REMOVED]
```

### Проверка конфигурации Docker Compose

```bash
# Проверяем конфигурацию
cat docker-compose.yml | head -50

# Проверяем что все образы доступны
docker-compose config
```

---

## 4️⃣ ЗАПУСК СИСТЕМЫ (5 минут)

### Первый запуск

```bash
# Запускаем все сервисы в фоне
docker-compose up -d

# Ждем инициализации базы данных
echo "Ждем инициализации системы..."
sleep 60

# Проверяем статус всех сервисов
docker-compose ps
```

**Ожидаемый вывод:**
```
NAME                         IMAGE                    STATUS
sovani-crosspost-api-1       sovani-crosspost-api     Up
sovani-crosspost-postgres-1  postgres:15-alpine       Up
sovani-crosspost-redis-1     redis:7-alpine           Up
sovani-crosspost-minio-1     minio/minio             Up
sovani-crosspost-worker-1    sovani-crosspost-worker  Up
```

### Проверка работоспособности

```bash
# Проверяем здоровье API
curl http://localhost:8000/health

# Ожидаемый ответ:
# {"status":"healthy","version":"1.0.0","timestamp":"2024-..."}

# Проверяем подключение к базе данных
docker-compose exec api python -c "
from database.connection import get_database_url
print('Database URL:', get_database_url())
"

# Проверяем Redis
docker-compose exec redis redis-cli ping
# Ответ: PONG

# Проверяем MinIO
curl http://localhost:9000/minio/health/live
```

### Инициализация базы данных

```bash
# Применяем миграции базы данных
docker-compose exec api alembic upgrade head

# Создаем первого администратора (опционально)
docker-compose exec api python -c "
from database.models import User
from database.connection import get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import bcrypt

# Здесь можно создать первого пользователя
print('База данных инициализирована')
"
```

---

## 5️⃣ НАСТРОЙКА NGINX И SSL (10 минут)

### Настройка Nginx

```bash
# Возвращаемся к root пользователю
exit  # выходим из пользователя sovani
```

**Создаем конфигурацию Nginx:**

```bash
# Замените example.com на ваш домен
DOMAIN="your-domain.com"

cat > /etc/nginx/sites-available/sovani-crosspost << EOF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};
    
    # API эндпоинты
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
    
    # Static files and admin panel (если есть)
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    # MinIO Admin (только для админов)
    location /minio/ {
        allow 127.0.0.1;
        deny all;
        proxy_pass http://127.0.0.1:9001;
    }
}
EOF

# Активируем сайт
ln -s /etc/nginx/sites-available/sovani-crosspost /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверяем конфигурацию
nginx -t

# Перезапускаем Nginx
systemctl restart nginx
systemctl enable nginx
```

### Установка SSL сертификата

```bash
# Устанавливаем SSL сертификат (замените на ваш домен)
certbot --nginx -d your-domain.com -d www.your-domain.com

# Проверяем автообновление сертификата
certbot renew --dry-run
```

### Настройка файрвола

```bash
# Настраиваем UFW
ufw allow ssh
ufw allow 'Nginx Full'
ufw --force enable

# Проверяем статус
ufw status
```

---

## 6️⃣ НАСТРОЙКА SYSTEMD СЛУЖБ (5 минут)

### Создание systemd сервиса

```bash
cat > /etc/systemd/system/sovani-crosspost.service << EOF
[Unit]
Description=SoVAni Crosspost Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/sovani/crosspost_app
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
ExecReload=/usr/local/bin/docker-compose restart
User=sovani
Group=sovani

[Install]
WantedBy=multi-user.target
EOF

# Активируем сервис
systemctl daemon-reload
systemctl enable sovani-crosspost.service
systemctl start sovani-crosspost.service

# Проверяем статус
systemctl status sovani-crosspost.service
```

---

## 7️⃣ ПЕРВЫЙ ТЕСТ СИСТЕМЫ (2 минуты)

### Базовые проверки

```bash
# Проверяем API
curl https://your-domain.com/health

# Тестовый пост
curl -X POST "https://your-domain.com/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {
      "message": "🎉 Тестовый пост из SoVAni Crosspost!",
      "hashtags": ["#sovani", "#crosspost", "#test"]
    },
    "platforms": ["instagram"]
  }'

# Смотрим логи обработки
su - sovani
cd /home/sovani/crosspost_app
docker-compose logs -f worker
```

### Проверка очередей Celery

```bash
# Статистика очередей
docker-compose exec worker celery -A app.celery inspect active_queues

# Статистика воркеров
docker-compose exec worker celery -A app.celery inspect stats
```

---

## 8️⃣ НАСТРОЙКА МОНИТОРИНГА И БЭКАПОВ (10 минут)

### Скрипт автоматических бэкапов

```bash
cat > /home/sovani/backup.sh << 'EOF'
#!/bin/bash

BACKUP_DIR="/home/sovani/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Создаем директорию для бэкапа
mkdir -p "${BACKUP_DIR}/${DATE}"

# Бэкап базы данных
docker-compose -f /home/sovani/crosspost_app/docker-compose.yml \
  exec -T postgres pg_dump -U sovani sovani_crosspost \
  > "${BACKUP_DIR}/${DATE}/database.sql"

# Бэкап MinIO данных
docker-compose -f /home/sovani/crosspost_app/docker-compose.yml \
  exec -T minio mc mirror --overwrite /data \
  "${BACKUP_DIR}/${DATE}/minio_data/"

# Бэкап конфигурации
cp /home/sovani/crosspost_app/.env "${BACKUP_DIR}/${DATE}/"
cp /home/sovani/crosspost_app/docker-compose.yml "${BACKUP_DIR}/${DATE}/"

# Архивируем
cd "${BACKUP_DIR}"
tar -czf "sovani_backup_${DATE}.tar.gz" "${DATE}/"
rm -rf "${DATE}/"

# Удаляем старые бэкапы (старше 30 дней)
find "${BACKUP_DIR}" -name "*.tar.gz" -mtime +30 -delete

echo "Backup completed: sovani_backup_${DATE}.tar.gz"
EOF

chmod +x /home/sovani/backup.sh
chown sovani:sovani /home/sovani/backup.sh

# Добавляем в cron (бэкап каждый день в 3:00)
crontab -u sovani << EOF
0 3 * * * /home/sovani/backup.sh >> /home/sovani/logs/backup.log 2>&1
EOF
```

### Скрипт мониторинга

```bash
cat > /home/sovani/monitor.sh << 'EOF'
#!/bin/bash

LOG_FILE="/home/sovani/logs/monitor.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

# Проверка API
API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)

# Проверка контейнеров
CONTAINERS_UP=$(docker-compose -f /home/sovani/crosspost_app/docker-compose.yml ps --services --filter "status=running" | wc -l)
TOTAL_CONTAINERS=$(docker-compose -f /home/sovani/crosspost_app/docker-compose.yml ps --services | wc -l)

# Проверка дискового пространства
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')

# Логирование
echo "[$DATE] API: $API_STATUS, Containers: $CONTAINERS_UP/$TOTAL_CONTAINERS, Disk: ${DISK_USAGE}%" >> "$LOG_FILE"

# Алерты
if [ "$API_STATUS" != "200" ]; then
    echo "[$DATE] ALERT: API не отвечает!" >> "$LOG_FILE"
fi

if [ "$CONTAINERS_UP" -lt "$TOTAL_CONTAINERS" ]; then
    echo "[$DATE] ALERT: Не все контейнеры запущены!" >> "$LOG_FILE"
    # Перезапускаем при необходимости
    cd /home/sovani/crosspost_app && docker-compose restart
fi

if [ "$DISK_USAGE" -gt 85 ]; then
    echo "[$DATE] WARNING: Заканчивается место на диске: ${DISK_USAGE}%" >> "$LOG_FILE"
fi
EOF

chmod +x /home/sovani/monitor.sh
chown sovani:sovani /home/sovani/monitor.sh

# Добавляем мониторинг каждые 5 минут
crontab -u sovani -l | { cat; echo "*/5 * * * * /home/sovani/monitor.sh"; } | crontab -u sovani -
```

---

## 9️⃣ ПОЛЕЗНЫЕ КОМАНДЫ ДЛЯ ЭКСПЛУАТАЦИИ

### Управление сервисами

```bash
# Остановка системы
sudo systemctl stop sovani-crosspost

# Запуск системы
sudo systemctl start sovani-crosspost

# Перезапуск системы
sudo systemctl restart sovani-crosspost

# Статус системы
sudo systemctl status sovani-crosspost

# Логи системы
journalctl -u sovani-crosspost -f
```

### Управление Docker Compose

```bash
# Переходим к проекту
su - sovani
cd /home/sovani/crosspost_app

# Просмотр всех контейнеров
docker-compose ps

# Просмотр логов
docker-compose logs -f

# Просмотр логов конкретного сервиса
docker-compose logs -f worker
docker-compose logs -f api

# Перезапуск конкретного сервиса
docker-compose restart api
docker-compose restart worker

# Обновление образов
docker-compose pull
docker-compose up -d --build
```

### Работа с базой данных

```bash
# Подключение к PostgreSQL
docker-compose exec postgres psql -U sovani -d sovani_crosspost

# Бэкап базы
docker-compose exec postgres pg_dump -U sovani sovani_crosspost > backup.sql

# Восстановление из бэкапа
docker-compose exec -T postgres psql -U sovani -d sovani_crosspost < backup.sql

# Применение миграций
docker-compose exec api alembic upgrade head

# Создание новой миграции
docker-compose exec api alembic revision --autogenerate -m "описание изменений"
```

### Мониторинг производительности

```bash
# Использование ресурсов Docker
docker stats

# Логи Nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# Использование диска
df -h
du -sh /home/sovani/*

# Процессы системы
htop

# Статистика очередей
docker-compose exec worker celery -A app.celery inspect active
docker-compose exec worker celery -A app.celery inspect registered
docker-compose exec worker celery -A app.celery flower  # Web UI на :5555
```

### Обновление проекта

```bash
# На Mac создаем новый архив
cd /Users/fbi/sovani_crosspost
tar -czf sovani_crosspost_update.tar.gz \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='*.log' \
  .

# Передаем на VPS
scp sovani_crosspost_update.tar.gz root@YOUR_VPS_IP:/root/

# На VPS
ssh root@YOUR_VPS_IP
systemctl stop sovani-crosspost

# Бэкап старой версии
su - sovani
cp -r /home/sovani/crosspost_app /home/sovani/crosspost_app.backup

# Обновление
cd /root
tar -xzf sovani_crosspost_update.tar.gz
cp -r sovani_crosspost/* /home/sovani/crosspost_app/
chown -R sovani:sovani /home/sovani/crosspost_app

# Применение миграций и перезапуск
cd /home/sovani/crosspost_app
docker-compose exec api alembic upgrade head
exit

systemctl start sovani-crosspost
```

---

## 🆘 УСТРАНЕНИЕ ПРОБЛЕМ

### Проблема: Порты заняты

```bash
# Проверка занятых портов
netstat -tulpn | grep -E "(8000|5432|6379|9000)"

# Освобождение портов
sudo fuser -k 8000/tcp
sudo fuser -k 5432/tcp
sudo fuser -k 6379/tcp
sudo fuser -k 9000/tcp

# Перезапуск системы
systemctl restart sovani-crosspost
```

### Проблема: Недостаточно места на диске

```bash
# Проверка места
df -h

# Очистка Docker
docker system prune -a -f
docker volume prune -f

# Очистка логов
truncate -s 0 /var/log/nginx/access.log
truncate -s 0 /var/log/nginx/error.log
journalctl --vacuum-time=3d

# Удаление старых бэкапов
find /home/sovani/backups -name "*.tar.gz" -mtime +7 -delete
```

### Проблема: API не отвечает

```bash
# Проверка статуса контейнеров
docker-compose ps

# Перезапуск API
docker-compose restart api

# Логи API
docker-compose logs -f api

# Проверка подключения к базе
docker-compose exec api python -c "
from database.connection import test_connection
test_connection()
"
```

### Проблема: Ошибки в работе с соцсетями

```bash
# Проверка переменных окружения
docker-compose exec api env | grep -E "(INSTAGRAM|VK|TELEGRAM|OPENAI)"

# Тестирование API ключей
docker-compose exec api python -c "
from integrations.instagram import test_instagram_api
from integrations.vk import test_vk_api
from integrations.telegram import test_telegram_api

test_instagram_api()
test_vk_api()
test_telegram_api()
"

# Редактирование .env
nano /home/sovani/crosspost_app/.env

# Перезапуск после изменений
docker-compose restart
```

---

## ✅ ЧЕКЛИСТ УСПЕШНОГО РАЗВЕРТЫВАНИЯ

- [ ] VPS с Ubuntu настроен и обновлен
- [ ] Docker и Docker Compose установлены
- [ ] Пользователь `sovani` создан с правильными правами
- [ ] Проект распакован в `/home/sovani/crosspost_app`
- [ ] .env файл заполнен реальными API ключами
- [ ] Все 5 контейнеров запущены (`docker-compose ps`)
- [ ] API отвечает на `/health` со статусом 200
- [ ] База данных инициализирована (миграции применены)
- [ ] Nginx настроен и работает
- [ ] SSL сертификат установлен
- [ ] Файрвол UFW настроен
- [ ] Systemd сервис активирован и работает
- [ ] Тестовый пост успешно обработан
- [ ] Очереди Celery работают
- [ ] Автоматические бэкапы настроены
- [ ] Мониторинг настроен

---

## 🎉 ПОЗДРАВЛЯЕМ!

**SoVAni Crosspost успешно развернут на продакшн VPS!**

📊 **Доступные эндпоинты:**
- API: `https://your-domain.com/api/`
- Здоровье: `https://your-domain.com/health`
- Документация: `https://your-domain.com/docs`

🔧 **Система включает:**
- ✅ 7 очередей Celery для обработки контента
- ✅ Автоматическое масштабирование воркеров
- ✅ Безопасное хранение медиафайлов в MinIO S3
- ✅ Интеграция с 5 социальными платформами
- ✅ SSL шифрование всех соединений
- ✅ Автоматические бэкапы каждый день
- ✅ Мониторинг системы каждые 5 минут

📞 **Поддержка:**
Логи: `docker-compose logs -f`
Мониторинг: `/home/sovani/logs/monitor.log`
Бэкапы: `/home/sovani/backups/`

**Система готова к работе! 🚀**