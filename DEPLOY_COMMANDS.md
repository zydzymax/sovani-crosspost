# 🚀 КОМАНДЫ ДЛЯ КОПИРОВАНИЯ - Развертывание на VPS

## ШАГ 1: НА MAC - ПОДГОТОВКА (скопировать и выполнить)

```bash
cd /Users/fbi/saleswhisper_crosspost

# Обновляем существующий .env файл для продакшн
cp .env .env.backup
cat >> .env << 'EOF'

# =============================================================================
# PRODUCTION SETTINGS - ДОБАВЛЕНО ДЛЯ VPS
# =============================================================================

# Production Environment
APP_ENV=production
ENVIRONMENT=production
DEBUG=false

# Database Configuration для Docker
DATABASE_URL=postgresql://saleswhisper:saleswhisper_production_pass@postgres:5432/saleswhisper_crosspost
POSTGRES_DB=saleswhisper_crosspost
POSTGRES_USER=saleswhisper
POSTGRES_PASSWORD=saleswhisper_production_pass
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Redis для Docker
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json

# S3 для Docker
S3_ENDPOINT=http://minio:9000
S3_USE_SSL=false

# Production Security Keys
SECRET_KEY=$(openssl rand -hex 32)
EOF

# Создаем архив
tar -czf saleswhisper_crosspost_production.tar.gz --exclude='*.pyc' --exclude='.git' --exclude='venv' --exclude='node_modules' --exclude='*.log' .

echo "✅ Архив создан. Теперь замените YOUR_VPS_IP на реальный IP и выполните:"
echo "scp saleswhisper_crosspost_production.tar.gz root@YOUR_VPS_IP:/root/"
```

## ШАГ 2: ПЕРЕДАЧА НА VPS (замените YOUR_VPS_IP)

```bash
scp saleswhisper_crosspost_production.tar.gz root@YOUR_VPS_IP:/root/
```

## ШАГ 3: НА VPS - УСТАНОВКА ВСЕГО (одной командой)

```bash
ssh root@YOUR_VPS_IP

# Выполните эту команду целиком:
apt update && apt upgrade -y && \
curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh && rm get-docker.sh && \
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
chmod +x /usr/local/bin/docker-compose && \
apt install -y nginx certbot python3-certbot-nginx ufw && \
useradd -m -s /bin/bash saleswhisper && \
usermod -aG docker saleswhisper && \
cd /root && \
tar -xzf saleswhisper_crosspost_production.tar.gz && \
mv /root/saleswhisper_crosspost /home/saleswhisper/crosspost_app && \
mkdir -p /home/saleswhisper/{data/{postgres,redis,minio},logs,backups} && \
chown -R saleswhisper:saleswhisper /home/saleswhisper && \
echo "✅ Установка завершена!"
```

## ШАГ 4: ЗАПУСК ПРОЕКТА

```bash
# Переключаемся на пользователя saleswhisper и запускаем
su - saleswhisper
cd /home/saleswhisper/crosspost_app
docker-compose up -d
sleep 60
docker-compose ps
docker-compose exec api alembic upgrade head
curl http://localhost:8000/health
exit
```

## ШАГ 5: НАСТРОЙКА NGINX (одной командой)

```bash
# Создаем конфигурацию Nginx (замените your-domain.com)
cat > /etc/nginx/sites-available/saleswhisper-crosspost << 'EOF'
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -s /etc/nginx/sites-available/saleswhisper-crosspost /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx
```

## ШАГ 6: SSL И АВТОЗАПУСК

```bash
# SSL (замените домен)
certbot --nginx -d your-domain.com -d www.your-domain.com

# Автозапуск
cat > /etc/systemd/system/saleswhisper-crosspost.service << 'EOF'
[Unit]
Description=SalesWhisper Crosspost Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/saleswhisper/crosspost_app
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
ExecReload=/usr/local/bin/docker-compose restart
User=saleswhisper
Group=saleswhisper

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable saleswhisper-crosspost.service
systemctl start saleswhisper-crosspost.service
```

## ШАГ 7: ФАЙРВОЛ И БЭКАПЫ

```bash
# Файрвол
ufw allow ssh
ufw allow 'Nginx Full'
ufw --force enable

# Скрипт автобэкапа
cat > /home/saleswhisper/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/saleswhisper/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "${BACKUP_DIR}"
docker-compose -f /home/saleswhisper/crosspost_app/docker-compose.yml exec -T postgres pg_dump -U saleswhisper saleswhisper_crosspost > "${BACKUP_DIR}/database_${DATE}.sql"
cp /home/saleswhisper/crosspost_app/.env "${BACKUP_DIR}/env_${DATE}.backup"
cd "${BACKUP_DIR}"
tar -czf "saleswhisper_backup_${DATE}.tar.gz" database_${DATE}.sql env_${DATE}.backup
rm database_${DATE}.sql env_${DATE}.backup
find "${BACKUP_DIR}" -name "*.tar.gz" -mtime +7 -delete
echo "Backup completed: saleswhisper_backup_${DATE}.tar.gz"
EOF

chmod +x /home/saleswhisper/backup.sh
chown saleswhisper:saleswhisper /home/saleswhisper/backup.sh

# Добавляем в cron
(crontab -u saleswhisper -l 2>/dev/null; echo "0 3 * * * /home/saleswhisper/backup.sh >> /home/saleswhisper/logs/backup.log 2>&1") | crontab -u saleswhisper -
```

## ШАГ 8: ФИНАЛЬНЫЙ ТЕСТ

```bash
# Тест всей системы
curl https://your-domain.com/health

# Тестовый пост
curl -X POST "https://your-domain.com/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {
      "message": "🚀 Тестовый пост из SalesWhisper Crosspost!",
      "hashtags": ["#saleswhisper", "#test"]
    },
    "platforms": ["instagram"]
  }'

# Проверка статуса
systemctl status saleswhisper-crosspost
su - saleswhisper -c "cd /home/saleswhisper/crosspost_app && docker-compose ps"
```

---

## 🚀 КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ПОСЛЕ УСТАНОВКИ

```bash
# Перезапуск системы
systemctl restart saleswhisper-crosspost

# Статус
systemctl status saleswhisper-crosspost

# Логи
su - saleswhisper -c "cd /home/saleswhisper/crosspost_app && docker-compose logs -f"

# Ручной бэкап
su - saleswhisper -c "/home/saleswhisper/backup.sh"

# Обновление проекта (если нужно)
systemctl stop saleswhisper-crosspost
# ... загрузить новую версию ...
systemctl start saleswhisper-crosspost
```

---

## ⚡ ЭКСТРЕННОЕ ВОССТАНОВЛЕНИЕ

```bash
# Если что-то сломалось
systemctl stop saleswhisper-crosspost
su - saleswhisper -c "cd /home/saleswhisper/crosspost_app && docker-compose down && docker-compose up -d"
systemctl start saleswhisper-crosspost
```

**✅ Готово! Просто копируйте команды по шагам.**