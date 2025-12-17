# Quick Start

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- Node.js 18+ (for frontend)

## 1. Clone & Setup

```bash
git clone https://github.com/zydzymax/sovani-crosspost.git
cd sovani-crosspost

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## 2. Start Infrastructure

```bash
# Start PostgreSQL, Redis, MinIO
docker compose up -d postgres redis minio

# Wait for services
docker compose ps
```

## 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required variables:
- `DATABASE_URL` - PostgreSQL connection
- `REDIS_URL` - Redis connection
- `TG_BOT_TOKEN` - Telegram bot token
- `JWT_SECRET_KEY` - JWT secret

## 4. Run Application

### Development (with hot reload)
```bash
# API
uvicorn app.main:app --reload --port 8002

# Worker (in another terminal)
celery -A app.workers.celery_app worker --loglevel=info
```

### Production (Docker)
```bash
docker compose -f docker-compose.prod.yml up -d
```

## 5. Verify

```bash
# Health check
curl http://localhost:8002/api/v1/health

# Open API docs
open http://localhost:8002/docs
```

## Useful Commands

```bash
# View logs
pm2 logs crosspost-api

# Restart services
pm2 restart all

# Database shell
PGPASSWORD=your_pass psql -h localhost -p 5433 -U sovani -d sovani_crosspost

# Redis CLI
redis-cli -p 6380
```

## Next Steps

1. Set up Telegram bot (@BotFather)
2. Connect social accounts in dashboard
3. Create first post via Telegram

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production deployment.
