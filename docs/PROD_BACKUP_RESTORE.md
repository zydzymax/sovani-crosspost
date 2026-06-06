# PROD Backup / Restore Runbook

Скрипты находятся в `scripts/`:

- `scripts/backup-prod.sh` - полный backup PostgreSQL + MinIO + Redis (по умолчанию)
- `scripts/restore-prod.sh` - восстановление из backup с обязательным флагом подтверждения
- `scripts/verify-restore.sh` - проверка целостности backup и тестовый `pg_restore` во временную БД

## 1. Backup

```bash
cd /root/saleswhisper_crosspost
bash scripts/backup-prod.sh
```

Полезные опции:

```bash
# Проверить, что будет выполнено
bash scripts/backup-prod.sh --dry-run

# Не включать Redis и MinIO
bash scripts/backup-prod.sh --no-redis --no-minio

# Включить .env в backup (чувствительные данные)
bash scripts/backup-prod.sh --include-env

# Свой путь и retention
bash scripts/backup-prod.sh --backup-dir /root/backups/crosspost --retention-days 30
```

Результат:

- Архив: `backups/saleswhisper_backup_<UTC_TIMESTAMP>.tar.gz`
- Симлинк на последний backup: `backups/latest.tar.gz`
- Внутри архива есть `SHA256SUMS` для проверки целостности

## 2. Verify backup before restore

```bash
cd /root/saleswhisper_crosspost
bash scripts/verify-restore.sh --archive backups/latest.tar.gz
```

Что проверяется:

- `SHA256SUMS` (если файл есть в архиве)
- целостность `minio_data.tar` и `redis_data.tar` (если присутствуют)
- тестовое восстановление `postgres.dump` во временную БД и удаление этой БД

## 3. Restore

Важно: скрипт разрушает текущие данные в PostgreSQL/MinIO/Redis (в зависимости от флагов).

```bash
cd /root/saleswhisper_crosspost
bash scripts/restore-prod.sh \
  --archive backups/saleswhisper_backup_YYYYMMDDTHHMMSSZ.tar.gz \
  --yes-really-restore
```

Поведение по умолчанию:

- Останавливает `api/worker/beat`
- Делает safety backup в `backups/pre-restore/`
- Восстанавливает PostgreSQL, MinIO и Redis
- Поднимает сервисы обратно (`docker compose up -d`)

Полезные опции:

```bash
# Сначала посмотреть план
bash scripts/restore-prod.sh --archive backups/latest.tar.gz --yes-really-restore --dry-run

# Пропустить checksum и pre-backup
bash scripts/restore-prod.sh --archive backups/latest.tar.gz --yes-really-restore --skip-checksum --no-pre-backup

# Восстановить только PostgreSQL
bash scripts/restore-prod.sh --archive backups/latest.tar.gz --yes-really-restore --no-redis --no-minio
```

## 4. Cron (пример)

```bash
# Ежедневный backup в 03:00 UTC
0 3 * * * cd /root/saleswhisper_crosspost && bash scripts/backup-prod.sh >> /var/log/crosspost-backup.log 2>&1
```

## 5. Рекомендации

- Хранить копии backup вне сервера (object storage / другой VPS)
- Периодически запускать `verify-restore.sh` на свежем архиве
- Раз в неделю делать полный тест restore в staging
- После restore запускать `bash scripts/prod-smoke-check.sh` перед возвратом трафика
- Для стандартизированного запуска после деплоя/restore использовать `bash scripts/prod-post-deploy.sh`
- Для алертов при сбоях backup/restore/smoke включить `OPS_ALERT_*` из `docs/OPS_ALERTS.md`
