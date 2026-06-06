#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
TOKEN_SOURCE="OPS_ALERT_TELEGRAM_BOT_TOKEN"

usage() {
  cat <<'EOF'
Usage: scripts/get-telegram-chat-id.sh [options]

Read recent Telegram updates and print discovered chat IDs.

Options:
  --token-source NAME   Env key in .env to use as bot token
                        (default: OPS_ALERT_TELEGRAM_BOT_TOKEN)
                        alternatives: TG_BOT_TOKEN, TG_PUBLISHING_BOT_TOKEN
  -h, --help            Show help
EOF
}

fail() {
  printf '[tg-chat-id][ERROR] %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --token-source)
      [ "$#" -ge 2 ] || fail "--token-source requires a value"
      TOKEN_SOURCE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[ -f "${ENV_FILE}" ] || fail ".env not found: ${ENV_FILE}"

token="$(awk -F= -v k="${TOKEN_SOURCE}" '$1==k {print substr($0,index($0,"=")+1)}' "${ENV_FILE}" | tail -n1)"
[ -n "${token}" ] || fail "Token is empty for ${TOKEN_SOURCE}"

response="$(curl -s "https://api.telegram.org/bot${token}/getUpdates?limit=25")"
if ! grep -q '"ok":true' <<<"${response}"; then
  printf '%s\n' "${response}"
  fail "Telegram API returned non-ok response"
fi

printf '%s\n' "${response}" | python3 - <<'PY'
import json
import sys

data = json.loads(sys.stdin.read() or "{}")
updates = data.get("result", [])
seen = {}

for upd in updates:
    for key in ("message", "channel_post", "edited_message", "edited_channel_post"):
        msg = upd.get(key)
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "n/a"
        chat_type = chat.get("type", "unknown")
        seen[str(chat_id)] = (chat_type, title)

if not seen:
    print("[tg-chat-id] No chats found in recent updates.")
    print("[tg-chat-id] Send a message to the bot (or add bot to channel and post), then rerun.")
    raise SystemExit(0)

print("[tg-chat-id] Discovered chat IDs:")
for chat_id, (chat_type, title) in sorted(seen.items()):
    print(f"  {chat_id}  type={chat_type}  title={title}")
PY
