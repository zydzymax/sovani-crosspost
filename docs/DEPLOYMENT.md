# Deployment Guide

## Quick Start

### Development
```bash
# Start all services with hot reload
docker compose -f docker-compose.dev.yml up

# Or start in background
docker compose -f docker-compose.dev.yml up -d

# View logs
docker compose -f docker-compose.dev.yml logs -f api
```

### Production
```bash
# Create .env from example
cp .env.example .env
# Edit .env with production values

# Start all services
docker compose -f docker-compose.prod.yml up -d

# Check health
curl http://localhost:8003/api/v1/health
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| api | 8003 | FastAPI application |
| postgres | 5433 | PostgreSQL database |
| redis | 6380 | Redis cache & Celery broker |
| minio | 9000/9001 | S3-compatible storage |
| worker | - | Celery worker |
| beat | - | Celery beat scheduler |

## Environment Variables

Required variables in `.env`:

```env
# Database
DATABASE_URL=postgresql://user:pass@localhost:5433/dbname
POSTGRES_PASSWORD=your_secure_password

# Redis
REDIS_URL=redis://localhost:6380/2

# S3/MinIO
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_BUCKET_NAME=media-bucket

# Security
JWT_SECRET_KEY=your_jwt_secret
AES_KEY=your_32_byte_key
TOKEN_ENCRYPTION_KEY=your_32_byte_key

# Telegram
TG_BOT_TOKEN=your_bot_token
```

## Rollback Procedure

### 1. Database Rollback
```bash
# View migration history
alembic history

# Downgrade one revision
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade <revision_id>
```

### 2. Application Rollback
```bash
# Stop current version
docker compose -f docker-compose.prod.yml down

# Checkout previous version
git checkout <previous_tag>

# Rebuild and start
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

### 3. Quick Rollback (PM2)
```bash
# If running via PM2, restart previous ecosystem
pm2 stop all
pm2 delete all
pm2 start ecosystem.config.js
```

## Health Checks

```bash
# API health
curl http://localhost:8003/api/v1/health

# API readiness
curl http://localhost:8003/api/v1/ready

# Database
PGPASSWORD=your_pass psql -h localhost -p 5433 -U saleswhisper -d saleswhisper_crosspost -c "SELECT 1"

# Redis
redis-cli -p 6380 ping

# Celery
celery -A app.workers.celery_app inspect ping
```

## Logs

```bash
# Docker logs
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker

# PM2 logs
pm2 logs crosspost-api
pm2 logs crosspost-worker
```

## Backup

### Database
```bash
# Backup
PGPASSWORD=your_pass pg_dump -h localhost -p 5433 -U saleswhisper saleswhisper_crosspost > backup.sql

# Restore
PGPASSWORD=your_pass psql -h localhost -p 5433 -U saleswhisper saleswhisper_crosspost < backup.sql
```

### MinIO/S3
```bash
# Use mc (MinIO Client)
mc alias set local http://localhost:9000 minioadmin minioadmin123
mc mirror local/media-bucket /backup/media/
```

## Monitoring

- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090
- MinIO Console: http://localhost:9001

## Troubleshooting

### API not starting
```bash
# Check logs
docker logs saleswhisper_api

# Check dependencies
docker compose ps
```

### Celery tasks stuck
```bash
# Purge queues
celery -A app.workers.celery_app purge

# Restart worker
docker compose restart worker
```

### Database connection issues
```bash
# Check PostgreSQL
docker exec -it saleswhisper_postgres pg_isready

# Check connection from API
docker exec -it saleswhisper_api python -c "from app.models.db import db_manager; print(db_manager.health_check())"
```
