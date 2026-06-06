# OPS Scheduler (Cron)

Этот runbook включает расписание под ключ для:

- регулярного backup (`backup-prod.sh`)
- периодического smoke-check (`prod-smoke-check.sh`)
- ротации ops-логов (`rotate-ops-logs.sh`)

Установка выполняется скриптом:

`scripts/install-ops-cron.sh`

## 1. Предпросмотр

```bash
cd /root/saleswhisper_crosspost
bash scripts/install-ops-cron.sh --print-only
```

## 2. Установка расписания

```bash
cd /root/saleswhisper_crosspost
bash scripts/install-ops-cron.sh
```

Дефолтные расписания:

- backup: `0 3 * * *`
- smoke-check: `*/10 * * * *`
- log-rotate: `20 3 * * *`

Логи по умолчанию:

- `logs/ops/backup.log`
- `logs/ops/smoke.log`
- `logs/ops/log-rotate.log`

## 3. Кастомизация

```bash
# smoke без celery ping
bash scripts/install-ops-cron.sh --smoke-skip-celery

# свои cron-выражения
bash scripts/install-ops-cron.sh \
  --backup-schedule "30 2 * * *" \
  --smoke-schedule "*/5 * * * *" \
  --rotate-schedule "45 3 * * *"

# отключить конкретные задачи
bash scripts/install-ops-cron.sh --disable-smoke
bash scripts/install-ops-cron.sh --disable-rotate
```

## 4. Проверка

```bash
crontab -l
```

Все managed-задачи оборачиваются блоком:

- `# >>> saleswhisper-ops >>>`
- `# <<< saleswhisper-ops <<<`

Повторный запуск `install-ops-cron.sh` безопасен: блок заменяется идемпотентно.

## 5. Ротация логов вручную

```bash
cd /root/saleswhisper_crosspost
bash scripts/rotate-ops-logs.sh --log-dir logs/ops
```

Dry-run:

```bash
bash scripts/rotate-ops-logs.sh --dry-run
```

## 6. Alerts

Скрипты scheduler/backup/smoke/rotate поддерживают OPS alerts.  
Настройка каналов: `docs/OPS_ALERTS.md`.
