#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOG_DIR="${PROJECT_DIR}/logs/ops"
RETENTION_DAYS=14
MAX_SIZE_MB=50
COMPRESS=1
FORCE_ROTATE=0
DRY_RUN=0
ALERT_SCRIPT="${SCRIPT_DIR}/send_ops_alert.py"
OPS_ALERT_NOTIFY_SUCCESS="${OPS_ALERT_NOTIFY_SUCCESS:-0}"
ALERT_SENT=0

usage() {
  cat <<'EOF'
Usage: scripts/rotate-ops-logs.sh [options]

Rotate *.log files in log directory and clean old rotated files.

Options:
  --log-dir PATH            Log directory (default: ./logs/ops)
  --retention-days N        Delete rotated files older than N days (default: 14)
  --max-size-mb N           Rotate file when size >= N MB (default: 50)
  --no-compress             Do not gzip rotated logs
  --force                   Rotate even if file is below size threshold
  --dry-run                 Print planned actions only
  -h, --help                Show help
EOF
}

log() {
  printf '[log-rotate] %s\n' "$*"
}

fail() {
  printf '[log-rotate][ERROR] %s\n' "$*" >&2
  exit 1
}

send_ops_alert() {
  local status="$1"
  local message="$2"
  local host_value
  host_value="$(hostname -f 2>/dev/null || hostname || echo unknown)"

  if [ ! -f "${ALERT_SCRIPT}" ]; then
    return 0
  fi

  if [ "${status}" = "success" ] && [ "${OPS_ALERT_NOTIFY_SUCCESS}" != "1" ]; then
    return 0
  fi

  set +e
  python3 "${ALERT_SCRIPT}" \
    --event "log-rotate" \
    --status "${status}" \
    --message "${message}" \
    --detail "host=${host_value}" \
    --detail "log_dir=${LOG_DIR}" \
    --detail "retention_days=${RETENTION_DAYS}" \
    --detail "max_size_mb=${MAX_SIZE_MB}" >/dev/null 2>&1
  local rc=$?
  set -e

  if [ "${rc}" -ne 0 ]; then
    log "Alert dispatch failed (non-blocking)"
  fi
}

on_error() {
  local exit_code=$?
  trap - ERR
  if [ "${ALERT_SENT}" -eq 0 ] && [ "${DRY_RUN}" -eq 0 ]; then
    send_ops_alert "failure" "Log rotation failed with exit code ${exit_code}"
    ALERT_SENT=1
  fi
  exit "${exit_code}"
}

on_interrupt() {
  trap - INT
  if [ "${ALERT_SENT}" -eq 0 ] && [ "${DRY_RUN}" -eq 0 ]; then
    send_ops_alert "failure" "Log rotation interrupted by signal"
    ALERT_SENT=1
  fi
  exit 130
}

trap on_error ERR
trap on_interrupt INT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --log-dir)
      [ "$#" -ge 2 ] || fail "--log-dir requires a value"
      LOG_DIR="$2"
      shift 2
      ;;
    --retention-days)
      [ "$#" -ge 2 ] || fail "--retention-days requires a value"
      RETENTION_DAYS="$2"
      shift 2
      ;;
    --max-size-mb)
      [ "$#" -ge 2 ] || fail "--max-size-mb requires a value"
      MAX_SIZE_MB="$2"
      shift 2
      ;;
    --no-compress)
      COMPRESS=0
      shift
      ;;
    --force)
      FORCE_ROTATE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
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

[[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] || fail "--retention-days must be a non-negative integer"
[[ "${MAX_SIZE_MB}" =~ ^[0-9]+$ ]] || fail "--max-size-mb must be a non-negative integer"

mkdir -p "${LOG_DIR}"

threshold_bytes=$((MAX_SIZE_MB * 1024 * 1024))
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
rotated_count=0

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "log dir: ${LOG_DIR}"
  log "retention days: ${RETENTION_DAYS}"
  log "max size mb: ${MAX_SIZE_MB}"
  log "compress: ${COMPRESS}"
  log "force rotate: ${FORCE_ROTATE}"
  exit 0
fi

while IFS= read -r log_file; do
  [ -f "${log_file}" ] || continue

  file_size="$(stat -c%s "${log_file}")"
  if [ "${FORCE_ROTATE}" -eq 1 ] || [ "${file_size}" -ge "${threshold_bytes}" ]; then
    rotated_path="${log_file}.${timestamp}"
    mv "${log_file}" "${rotated_path}"
    : > "${log_file}"
    rotated_count=$((rotated_count + 1))
    log "Rotated: $(basename "${log_file}") -> $(basename "${rotated_path}")"

    if [ "${COMPRESS}" -eq 1 ]; then
      gzip -f "${rotated_path}"
      log "Compressed: $(basename "${rotated_path}").gz"
    fi
  fi
done < <(find "${LOG_DIR}" -maxdepth 1 -type f -name '*.log' | sort)

find "${LOG_DIR}" -maxdepth 1 -type f \
  \( -name '*.log.*' -o -name '*.log.*.gz' \) \
  -mtime "+${RETENTION_DAYS}" -delete

log "Rotation completed, rotated files: ${rotated_count}"

if [ "${ALERT_SENT}" -eq 0 ]; then
  send_ops_alert "success" "Log rotation completed (rotated=${rotated_count})"
  ALERT_SENT=1
fi

exit 0
