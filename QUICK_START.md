# 🚀 Быстрый старт - Развертывание на рабочий VPS Ubuntu

## 📋 Продакшн развертывание (10 минут)

### 1️⃣ На вашем Mac - подготовка для продакшн

```bash
# Переходим в папку проекта
cd /Users/fbi/sovani_crosspost

# Создаем .env с продакшн настройками
cp env.example .env

# ВАЖНО: Настройте реальные API ключи перед передачей на VPS
nano .env

# Заполните обязательные ключи:
# INSTAGRAM_ACCESS_TOKEN=your-real-token
# VK_ACCESS_TOKEN=your-real-token  
# TELEGRAM_BOT_TOKEN=your-real-bot-token
# OPENAI_API_KEY=your-real-openai-key
# SECRET_KEY=$(openssl rand -hex 32)
# POSTGRES_PASSWORD=$(openssl rand -hex 16)
# S3_SECRET_KEY=$(openssl rand -hex 20)

# Создаем полный архив для продакшн (включая .env)
tar -czf sovani_crosspost_production.tar.gz \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='*.log' \
  .

# Передаем на VPS (замените your-server-ip)
scp sovani_crosspost_production.tar.gz root@your-server-ip:/root/
```

### 2️⃣ На VPS Ubuntu

```bash
# Подключаемся к VPS
ssh root@your-server-ip

# Обновляем систему
apt update && apt upgrade -y

# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Устанавливаем Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Создаем пользователя для приложения
useradd -m -s /bin/bash sovani
usermod -aG docker sovani

# Распаковываем проект
cd /root
tar -xzf sovani_crosspost_production.tar.gz
mv sovani_crosspost /home/sovani/crosspost_app
chown -R sovani:sovani /home/sovani/crosspost_app

# Переключаемся на пользователя приложения
su - sovani
cd /home/sovani/crosspost_app
```

### 3️⃣ Продакшн настройка проекта

```bash
# .env файл уже настроен на Mac, но можно проверить
cat .env | head -20

# Если нужно что-то изменить (например, добавить IP сервера)
nano .env

# Обновляем настройки для продакшн VPS:
# APP_ENV=production
# LOG_LEVEL=INFO  
# DATABASE_URL=postgresql://sovani:your-password@postgres:5432/sovani_crosspost
```

### 4️⃣ Запуск системы

```bash
# Запускаем все сервисы
docker-compose up -d

# Ждем инициализации (30 секунд)
sleep 30

# Проверяем статус
docker-compose ps

# Проверяем здоровье API
curl http://localhost:8000/health
```

### 5️⃣ Первый тест

```bash
# Тестовый пост
curl -X POST "http://localhost:8000/api/posts" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "manual",
    "source_data": {"message": "Hello from SoVAni Crosspost!"},
    "platforms": ["instagram"]
  }'

# Смотрим логи обработки
docker-compose logs -f worker
```

---

## ⚙️ Полезные команды

```bash
# Остановка всех сервисов
docker-compose down

# Перезапуск
docker-compose restart

# Просмотр логов
docker-compose logs -f

# Обновление проекта
docker-compose down
docker-compose pull
docker-compose up -d --build

# Бэкап базы данных
docker-compose exec postgres pg_dump -U sovani sovani_crosspost > backup.sql
```

---

## 🛠️ Если что-то пошло не так

### Проблема: Порты заняты
```bash
# Проверить занятые порты
netstat -tulpn | grep -E "(8000|5432|6379|9000)"

# Убить процессы на портах
sudo fuser -k 8000/tcp
sudo fuser -k 5432/tcp
```

### Проблема: Недостаточно места
```bash
# Проверить место на диске
df -h

# Очистить Docker
docker system prune -a -f
docker volume prune -f
```

### Проблема: Ошибки API ключей
```bash
# Проверить переменные окружения
docker-compose exec api env | grep -E "(INSTAGRAM|VK|TELEGRAM|OPENAI)"

# Отредактировать .env
nano .env

# Перезапустить
docker-compose restart
```

---

## 📞 Нужна помощь?

1. **Смотрим логи**: `docker-compose logs -f`
2. **Проверяем здоровье**: `curl http://localhost:8000/health`
3. **Читаем полную инструкцию**: `DEPLOYMENT_GUIDE.md`
4. **Изучаем документацию**: `DOCUMENTATION.md`

**Успехов! 🎉**