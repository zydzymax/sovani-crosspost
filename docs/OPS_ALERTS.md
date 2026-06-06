# OPS Alerts

Скрипт `scripts/send_ops_alert.py` отправляет операционные уведомления в:

- Telegram Bot
- HTTP webhook
- SMTP email

Этот скрипт автоматически вызывается из:

- `scripts/backup-prod.sh`
- `scripts/prod-smoke-check.sh`
- `scripts/prod-post-deploy.sh`
- `scripts/rotate-ops-logs.sh`

По умолчанию отправляются только уведомления об ошибках.  
Чтобы включить success-уведомления, установите `OPS_ALERT_NOTIFY_SUCCESS=1`.

## Переменные окружения

### Общие

```bash
OPS_ALERT_NOTIFY_SUCCESS=0
```

### Telegram

```bash
OPS_ALERT_TELEGRAM_BOT_TOKEN=[REVOKED_SECRET_REMOVED]
OPS_ALERT_TELEGRAM_CHAT_ID=-1001234567890
```

Если `chat_id` неизвестен:

```bash
cd /root/saleswhisper_crosspost
bash scripts/get-telegram-chat-id.sh --token-source TG_PUBLISHING_BOT_TOKEN
```

Перед запуском отправьте сообщение боту (или добавьте бота в канал/группу и сделайте пост), затем повторите команду.

### Webhook

```bash
OPS_ALERT_WEBHOOK_URL=https://hooks.example.com/ops-alerts
# Опционально:
OPS_ALERT_WEBHOOK_BEARER_TOKEN=[REVOKED_SECRET_REMOVED]
OPS_ALERT_WEBHOOK_AUTH_HEADER=X-Webhook-Token: secret-value
```

### SMTP Email

```bash
OPS_ALERT_SMTP_HOST=smtp.yandex.ru
OPS_ALERT_SMTP_PORT=465
OPS_ALERT_SMTP_USER=alerts@example.com
OPS_ALERT_SMTP_PASSWORD=[REVOKED_SECRET_REMOVED]
OPS_ALERT_SMTP_FROM=SalesWhisper Ops <alerts@example.com>
OPS_ALERT_SMTP_TO=devops@example.com,owner@example.com
OPS_ALERT_SMTP_USE_SSL=1
OPS_ALERT_SMTP_STARTTLS=0
```

## Проверка конфигурации

```bash
cd /root/saleswhisper_crosspost
python3 scripts/send_ops_alert.py \
  --event manual-test \
  --status info \
  --message "OPS alerts test" \
  --detail "source=manual"
```

Dry-run:

```bash
python3 scripts/send_ops_alert.py \
  --event manual-test \
  --status info \
  --message "OPS alerts dry-run" \
  --dry-run
```

## Примечания

- Если каналы не настроены, скрипт завершится без ошибки.
- Если каналы настроены, но все отправки провалились, скрипт вернет `exit code 1`.
- Встроенные вызовы в `backup/smoke/post-deploy` всегда non-blocking: сбой отправки алерта не останавливает основной сценарий.
