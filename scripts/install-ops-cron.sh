#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOG_DIR="${PROJECT_DIR}/logs/ops"
BACKUP_SCHEDULE="0 3 * * *"
SMOKE_SCHEDULE="*/10 * * * *"
ROTATE_SCHEDULE="20 3 * * *"
ENABLE_BACKUP=1
ENABLE_SMOKE=1
ENABLE_ROTATE=1
SMOKE_SKIP_CELERY=0
PRINT_ONLY=0

BLOCK_START="# >>> saleswhisper-ops >>>"
BLOCK_END="# <<< saleswhisper-ops <<<"

usage() {
  cat <<'EOF'
Usage: scripts/install-ops-cron.sh [options]

Install/update cron jobs for operations:
- backup-prod.sh
- prod-smoke-check.sh
- rotate-ops-logs.sh

Options:
  --log-dir PATH            Log directory (default: ./logs/ops)
  --backup-schedule CRON    Cron expression for backup (default: "0 3 * * *")
  --smoke-schedule CRON     Cron expression for smoke-check (default: "*/10 * * * *")
  --rotate-schedule CRON    Cron expression for log rotate (default: "20 3 * * *")
  --disable-backup          Do not install backup job
  --disable-smoke           Do not install smoke-check job
  --disable-rotate          Do not install log rotate job
  --smoke-skip-celery       Run smoke with --skip-celery
  --print-only              Print resulting cron block, do not install
  -h, --help                Show help
EOF
}

log() {
  printf '[ops-cron] %s\n' "$*"
}

fail() {
  printf '[ops-cron][ERROR] %s\n' "$*" >&2
  exit 1
}

escape_for_cron() {
  sed 's/%/\\%/g'
}

build_cron_block() {
  local smoke_extra=""
  if [ "${SMOKE_SKIP_CELERY}" -eq 1 ]; then
    smoke_extra=" --skip-celery"
  fi

  echo "${BLOCK_START}"
  echo "# managed by scripts/install-ops-cron.sh"
  echo "SHELL=/bin/bash"
  echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

  if [ "${ENABLE_BACKUP}" -eq 1 ]; then
    echo "${BACKUP_SCHEDULE} cd ${PROJECT_DIR} && bash scripts/backup-prod.sh >> ${LOG_DIR}/backup.log 2>&1"
  fi

  if [ "${ENABLE_SMOKE}" -eq 1 ]; then
    echo "${SMOKE_SCHEDULE} cd ${PROJECT_DIR} && bash scripts/prod-smoke-check.sh${smoke_extra} >> ${LOG_DIR}/smoke.log 2>&1"
  fi

  if [ "${ENABLE_ROTATE}" -eq 1 ]; then
    echo "${ROTATE_SCHEDULE} cd ${PROJECT_DIR} && bash scripts/rotate-ops-logs.sh --log-dir ${LOG_DIR} >> ${LOG_DIR}/log-rotate.log 2>&1"
  fi

  echo "${BLOCK_END}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --log-dir)
      [ "$#" -ge 2 ] || fail "--log-dir requires a value"
      LOG_DIR="$2"
      shift 2
      ;;
    --backup-schedule)
      [ "$#" -ge 2 ] || fail "--backup-schedule requires a value"
      BACKUP_SCHEDULE="$2"
      shift 2
      ;;
    --smoke-schedule)
      [ "$#" -ge 2 ] || fail "--smoke-schedule requires a value"
      SMOKE_SCHEDULE="$2"
      shift 2
      ;;
    --rotate-schedule)
      [ "$#" -ge 2 ] || fail "--rotate-schedule requires a value"
      ROTATE_SCHEDULE="$2"
      shift 2
      ;;
    --disable-backup)
      ENABLE_BACKUP=0
      shift
      ;;
    --disable-smoke)
      ENABLE_SMOKE=0
      shift
      ;;
    --disable-rotate)
      ENABLE_ROTATE=0
      shift
      ;;
    --smoke-skip-celery)
      SMOKE_SKIP_CELERY=1
      shift
      ;;
    --print-only)
      PRINT_ONLY=1
      shift
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

mkdir -p "${LOG_DIR}"

if [ "${ENABLE_BACKUP}" -eq 0 ] && [ "${ENABLE_SMOKE}" -eq 0 ] && [ "${ENABLE_ROTATE}" -eq 0 ]; then
  fail "All jobs are disabled. Enable at least one job."
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Command not found: $1"
}

require_cmd crontab

new_block="$(build_cron_block | escape_for_cron)"

if [ "${PRINT_ONLY}" -eq 1 ]; then
  log "Print only mode"
  printf '%s\n' "${new_block}"
  exit 0
fi

existing_cron="$(crontab -l 2>/dev/null || true)"
cleaned_cron="$(printf '%s\n' "${existing_cron}" | awk -v start="${BLOCK_START}" -v end="${BLOCK_END}" '
  $0==start {skip=1; next}
  $0==end {skip=0; next}
  !skip {print}
')"

{
  if [ -n "${cleaned_cron}" ]; then
    printf '%s\n' "${cleaned_cron}"
  fi
  printf '%s\n' "${new_block}"
} | crontab -

log "Ops cron block installed successfully"
log "Use 'crontab -l' to verify"

exit 0
