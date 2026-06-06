# PROD Smoke Check

`scripts/prod-smoke-check.sh` выполняет быстрый операционный smoke-check прод-стека после деплоя, рестарта или restore.
Также он автоматически вызывается из `scripts/prod-post-deploy.sh`.

Проверки по умолчанию:

- доступность `docker compose`
- все ключевые сервисы в running (`postgres`, `redis`, `minio`, `api`, `worker`, `beat`)
- `GET /api/v1/health`
- `GET /api/v1/ready` (`ready=true`)
- подключение к PostgreSQL (`SELECT 1`)
- `redis-cli ping`
- MinIO liveness endpoint
- Celery `inspect ping`

## Быстрый запуск

```bash
cd /root/saleswhisper_crosspost
bash scripts/prod-smoke-check.sh
```

Если хотя бы одна проверка не проходит, скрипт завершается с `exit code 1`.

## Полезные опции

```bash
# Посмотреть план проверок без выполнения
bash scripts/prod-smoke-check.sh --dry-run

# Если worker/celery временно недоступен
bash scripts/prod-smoke-check.sh --skip-celery

# Кастомный compose и URL
bash scripts/prod-smoke-check.sh \
  --compose-file /root/saleswhisper_crosspost/docker-compose.prod.yml \
  --api-base-url http://127.0.0.1:8003

# Более мягкие/жесткие retry
bash scripts/prod-smoke-check.sh --retry-attempts 20 --retry-delay-sec 2 --timeout-sec 5
```

## Рекомендуемый порядок после restore

1. Выполнить restore:
   `bash scripts/restore-prod.sh --archive backups/latest.tar.gz --yes-really-restore`
2. Проверить сервисы:
   `bash scripts/prod-smoke-check.sh`
3. Если smoke-check зелёный, вернуть трафик на API.

## Оповещения

При ошибках smoke-check автоматически отправляет OPS alert (если настроены каналы).  
Настройка каналов: `docs/OPS_ALERTS.md`.
