# 📦 Пошаговая инструкция портирования SoVAni Crosspost на VPS

## ШАГ 1: ПОДГОТОВКА НА MAC (Терминал Mac)

```bash
# Переходим в папку проекта
cd /Users/fbi/sovani_crosspost

# Копируем конфигурацию и заполняем API ключи
cp env.example .env
nano .env
```

**В .env заполните обязательные поля:**
```bash
SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
INSTAGRAM_ACCESS_TOKEN=ваш-токен
VK_ACCESS_TOKEN=ваш-токен
TELEGRAM_BOT_TOKEN=ваш-токен
OPENAI_API_KEY=ваш-ключ
```

```bash
# Создаем архив со всем проектом включая .env
tar -czf sovani_crosspost_production.tar.gz --exclude='*.pyc' --exclude='.git' --exclude='venv' --exclude='node_modules' --exclude='*.log' .

# Передаем архив на VPS (замените YOUR_VPS_IP)
scp sovani_crosspost_production.tar.gz root@YOUR_VPS_IP:/root/
```

---

## ШАГ 2: ПОДКЛЮЧЕНИЕ К VPS И УСТАНОВКА DOCKER

```bash
# Подключаемся к VPS
ssh root@YOUR_VPS_IP
```

**Теперь все команды выполняются на VPS:**

```bash
# Обновляем систему
apt update && apt upgrade -y

# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Устанавливаем Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Проверяем установку
docker --version
docker-compose --version
```

---

## ШАГ 3: НАСТРОЙКА ПОЛЬЗОВАТЕЛЯ И РАСПАКОВКА ПРОЕКТА

```bash
# Создаем пользователя для приложения
useradd -m -s /bin/bash sovani
usermod -aG docker sovani

# Распаковываем проект
cd /root
tar -xzf sovani_crosspost_production.tar.gz
mv /root/sovani_crosspost /home/sovani/crosspost_app
chown -R sovani:sovani /home/sovani/crosspost_app

# Создаем директории для данных
mkdir -p /home/sovani/{data,logs,backups}
mkdir -p /home/sovani/data/{postgres,redis,minio}
chown -R sovani:sovani /home/sovani/data /home/sovani/logs /home/sovani/backups

# Переключаемся на пользователя sovani
su - sovani
cd /home/sovani/crosspost_app
```

---

## ШАГ 4: ПРОВЕРКА И КОРРЕКТИРОВКА КОНФИГУРАЦИИ

```bash
# Проверяем .env файл
head -20 .env

# При необходимости корректируем (убедитесь что все ключи заполнены)
nano .env

# Проверяем docker-compose.yml
head -30 docker-compose.yml

# Проверяем конфигурацию Docker Compose
docker-compose config
```

---

## ШАГ 5: ЗАПУСК СИСТЕМЫ

```bash
# Запускаем все контейнеры
docker-compose up -d

# Ждем инициализации (важно!)
sleep 60

# Проверяем что все контейнеры запустились
docker-compose ps
```

**Ожидаемый результат - все контейнеры в статусе "Up":**
```
NAME                         STATUS
crosspost_app-api-1         Up
crosspost_app-postgres-1    Up  
crosspost_app-redis-1       Up
crosspost_app-minio-1       Up
crosspost_app-worker-1      Up
```

---

## ШАГ 6: ПРОВЕРКА РАБОТОСПОСОБНОСТИ

```bash
# Проверяем API
curl http://localhost:8000/health

# Применяем миграции базы данных
docker-compose exec api alembic upgrade head

# Проверяем Redis
docker-compose exec redis redis-cli ping

# Смотрим логи (убедиться что нет ошибок)
docker-compose logs api
docker-compose logs worker
```

---

## ШАГ 7: НАСТРОЙКА NGINX (возвращаемся к root)

```bash
# Выходим от пользователя sovani
exit

# Устанавливаем Nginx
apt install -y nginx

# Создаем конфигурацию (замените your-domain.com на ваш домен)
cat > /etc/nginx/sites-available/sovani-crosspost << 'EOF'
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

# Активируем сайт
ln -s /etc/nginx/sites-available/sovani-crosspost /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверяем и перезапускаем Nginx
nginx -t
systemctl restart nginx
systemctl enable nginx
```

---

## ШАГ 8: УСТАНОВКА SSL (если есть домен)

```bash
# Устанавливаем Certbot
apt install -y certbot python3-certbot-nginx

# Получаем SSL сертификат (замените на ваш домен)
certbot --nginx -d your-domain.com -d www.your-domain.com

# Настраиваем автообновление
certbot renew --dry-run
```

---

## ШАГ 9: НАСТРОЙКА АВТОЗАПУСКА

```bash
# Создаем systemd сервис
cat > /etc/systemd/system/sovani-crosspost.service << 'EOF'
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

## ШАГ 10: НАСТРОЙКА ФАЙРВОЛА

```bash
# Настраиваем UFW
ufw allow ssh
ufw allow 'Nginx Full'
ufw --force enable

# Проверяем статус
ufw status
```

---

## ШАГ 11: ФИНАЛЬНЫЙ ТЕСТ

```bash
# Проверяем API через внешний домен
curl https://your-domain.com/health

# Или через IP если нет домена
curl http://YOUR_VPS_IP/health

# Тестовый пост
curl -X POST "https://your-domain.com/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {
      "message": "Тестовый пост из SoVAni!",
      "hashtags": ["#sovani", "#test"]
    },
    "platforms": ["instagram"]
  }'

# Смотрим логи обработки
su - sovani
cd /home/sovani/crosspost_app
docker-compose logs -f worker
```

---

## ШАГ 12: НАСТРОЙКА АВТОМАТИЧЕСКИХ БЭКАПОВ

```bash
# Создаем скрипт бэкапа
cat > /home/sovani/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/sovani/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "${BACKUP_DIR}"

# Бэкап базы данных
docker-compose -f /home/sovani/crosspost_app/docker-compose.yml \
  exec -T postgres pg_dump -U sovani sovani_crosspost \
  > "${BACKUP_DIR}/database_${DATE}.sql"

# Бэкап конфигурации
cp /home/sovani/crosspost_app/.env "${BACKUP_DIR}/env_${DATE}.backup"

# Архивируем
cd "${BACKUP_DIR}"
tar -czf "sovani_backup_${DATE}.tar.gz" database_${DATE}.sql env_${DATE}.backup
rm database_${DATE}.sql env_${DATE}.backup

# Удаляем старые бэкапы (старше 7 дней)
find "${BACKUP_DIR}" -name "*.tar.gz" -mtime +7 -delete

echo "Backup completed: sovani_backup_${DATE}.tar.gz"
EOF

chmod +x /home/sovani/backup.sh
chown sovani:sovani /home/sovani/backup.sh

# Добавляем в cron (бэкап каждый день в 3:00)
crontab -u sovani << 'EOF'
0 3 * * * /home/sovani/backup.sh >> /home/sovani/logs/backup.log 2>&1
EOF
```

---

## ✅ ПРОВЕРОЧНЫЙ СПИСОК

Убедитесь что все пункты выполнены:

- [ ] Архив передан на VPS
- [ ] Docker и Docker Compose установлены  
- [ ] Пользователь sovani создан
- [ ] Проект распакован в /home/sovani/crosspost_app
- [ ] .env файл заполнен API ключами
- [ ] `docker-compose ps` показывает все контейнеры в статусе Up
- [ ] `curl http://localhost:8000/health` возвращает {"status":"healthy"}
- [ ] Миграции базы данных применены
- [ ] Nginx настроен и работает
- [ ] SSL сертификат установлен (если есть домен)
- [ ] Systemd сервис активирован
- [ ] Файрвол настроен
- [ ] Тестовый API запрос работает
- [ ] Автоматические бэкапы настроены

---

## 🚨 КОМАНДЫ ДЛЯ УСТРАНЕНИЯ ПРОБЛЕМ

**Если контейнеры не запустились:**
```bash
su - sovani
cd /home/sovani/crosspost_app
docker-compose down
docker-compose up -d
docker-compose logs
```

**Если API не отвечает:**
```bash
docker-compose restart api
curl http://localhost:8000/health
docker-compose logs api
```

**Если заняты порты:**
```bash
netstat -tulpn | grep -E "(8000|5432|6379|9000)"
sudo fuser -k 8000/tcp
docker-compose restart
```

**Перезапуск всей системы:**
```bash
systemctl restart sovani-crosspost
systemctl status sovani-crosspost
```

---

## 📞 ИТОГОВЫЕ КОМАНДЫ ПОСЛЕ РАЗВЕРТЫВАНИЯ

**Управление системой:**
```bash
# Перезапуск
systemctl restart sovani-crosspost

# Статус
systemctl status sovani-crosspost

# Логи
su - sovani
cd /home/sovani/crosspost_app
docker-compose logs -f
```

**Мониторинг:**
```bash
# Статус контейнеров
docker-compose ps

# Использование ресурсов
docker stats

# Логи Nginx
tail -f /var/log/nginx/access.log
```

**🎉 Система готова к работе!**