# PROD Post-Deploy Runbook

`scripts/prod-post-deploy.sh` объединяет стандартный post-deploy flow в одну команду:

1. `docker compose up -d`
2. применение SQL-миграций из `migrations/*.sql`
3. smoke-check (`scripts/prod-smoke-check.sh`)

## Базовый запуск

```bash
cd /root/saleswhisper_crosspost
bash scripts/prod-post-deploy.sh
```

Если любой шаг завершается ошибкой, скрипт вернёт `exit code 1`.

## Полезные опции

```bash
# Только посмотреть план
bash scripts/prod-post-deploy.sh --dry-run

# Пропустить миграции
bash scripts/prod-post-deploy.sh --no-migrations

# Пропустить smoke-check
bash scripts/prod-post-deploy.sh --no-smoke

# Smoke-check без Celery ping
bash scripts/prod-post-deploy.sh --smoke-skip-celery

# Свой compose файл
bash scripts/prod-post-deploy.sh --compose-file /root/saleswhisper_crosspost/docker-compose.prod.yml
```

## Рекомендация по эксплуатации

- После каждого деплоя запускать `prod-post-deploy.sh`.
- После restore использовать:
  1. `scripts/restore-prod.sh`
  2. `scripts/prod-post-deploy.sh --no-migrations` (если схема БД уже из backup)
- Для автоматических оповещений о сбоях/успехе настроить каналы из `docs/OPS_ALERTS.md`.
- Для автозапуска backup/smoke/rotate по cron использовать `docs/OPS_SCHEDULER.md`.
