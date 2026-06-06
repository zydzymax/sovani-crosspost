#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env"
BACKUP_DIR="${PROJECT_DIR}/backups"
RETENTION_DAYS=14
WITH_REDIS=1
WITH_MINIO=1
INCLUDE_ENV=0
DRY_RUN=0
ALERT_SCRIPT="${SCRIPT_DIR}/send_ops_alert.py"
OPS_ALERT_NOTIFY_SUCCESS="${OPS_ALERT_NOTIFY_SUCCESS:-0}"
ALERT_SENT=0

COMPOSE_CMD=()
WORK_DIR=""

usage() {
  cat <<'EOF'
Usage: scripts/backup-prod.sh [options]

Options:
  --compose-file PATH       Path to docker compose file (default: docker-compose.prod.yml)
  --backup-dir PATH         Where to store backups (default: ./backups)
  --retention-days N        Delete backup archives older than N days (default: 14)
  --no-redis                Skip Redis data backup
  --no-minio                Skip MinIO data backup
  --include-env             Include .env file in archive (sensitive)
  --dry-run                 Show what will happen without writing files
  -h, --help                Show help
EOF
}

log() {
  printf '[backup] %s\n' "$*"
}

fail() {
  printf '[backup][ERROR] %s\n' "$*" >&2
  exit 1
}

cleanup_on_error() {
  if [ -n "${WORK_DIR}" ] && [ -d "${WORK_DIR}" ]; then
    rm -rf "${WORK_DIR}"
  fi
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
    --event "backup" \
    --status "${status}" \
    --message "${message}" \
    --detail "archive=${archive_path:-n/a}" \
    --detail "backup_dir=${BACKUP_DIR}" \
    --detail "compose_file=$(basename "${COMPOSE_FILE}")" \
    --detail "with_redis=${WITH_REDIS}" \
    --detail "with_minio=${WITH_MINIO}" \
    --detail "host=${host_value}" >/dev/null 2>&1
  local rc=$?
  set -e

  if [ "${rc}" -ne 0 ]; then
    log "Alert dispatch failed (non-blocking)"
  fi
}

on_error() {
  local exit_code=$?
  trap - ERR
  cleanup_on_error
  if [ "${ALERT_SENT}" -eq 0 ]; then
    send_ops_alert "failure" "Production backup failed with exit code ${exit_code}"
    ALERT_SENT=1
  fi
  exit "${exit_code}"
}

on_interrupt() {
  trap - INT
  cleanup_on_error
  if [ "${ALERT_SENT}" -eq 0 ]; then
    send_ops_alert "failure" "Production backup interrupted by signal"
    ALERT_SENT=1
  fi
  exit 130
}

trap on_error ERR
trap on_interrupt INT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Command not found: $1"
}

read_env_var() {
  local key="$1"
  local default_value="$2"
  local value=""

  if [ -f "${ENV_FILE}" ]; then
    value="$(awk -F= -v k="${key}" '$1 == k {print substr($0, index($0, "=") + 1)}' "${ENV_FILE}" | tail -n1)"
  fi

  if [ -z "${value}" ]; then
    value="${default_value}"
  fi

  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "${value}"
}

init_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose -f "${COMPOSE_FILE}")
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose -f "${COMPOSE_FILE}")
    return
  fi

  fail "Neither 'docker compose' nor 'docker-compose' is available"
}

run_compose() {
  "${COMPOSE_CMD[@]}" "$@"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compose-file)
      [ "$#" -ge 2 ] || fail "--compose-file requires a value"
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --backup-dir)
      [ "$#" -ge 2 ] || fail "--backup-dir requires a value"
      BACKUP_DIR="$2"
      shift 2
      ;;
    --retention-days)
      [ "$#" -ge 2 ] || fail "--retention-days requires a value"
      RETENTION_DAYS="$2"
      shift 2
      ;;
    --no-redis)
      WITH_REDIS=0
      shift
      ;;
    --no-minio)
      WITH_MINIO=0
      shift
      ;;
    --include-env)
      INCLUDE_ENV=1
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
[ -f "${COMPOSE_FILE}" ] || fail "Compose file not found: ${COMPOSE_FILE}"

require_cmd docker
require_cmd tar
require_cmd sha256sum
require_cmd find
require_cmd awk

mkdir -p "${BACKUP_DIR}"

init_compose_cmd

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="saleswhisper_backup_${timestamp}"
WORK_DIR="${BACKUP_DIR}/${backup_name}"
archive_path="${BACKUP_DIR}/${backup_name}.tar.gz"

pg_user="$(read_env_var POSTGRES_USER saleswhisper)"
pg_db="$(read_env_var POSTGRES_DB saleswhisper_crosspost)"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "compose file: ${COMPOSE_FILE}"
  log "backup dir: ${BACKUP_DIR}"
  log "archive: ${archive_path}"
  log "postgres db/user: ${pg_db}/${pg_user}"
  log "include redis: ${WITH_REDIS}"
  log "include minio: ${WITH_MINIO}"
  log "include .env: ${INCLUDE_ENV}"
  exit 0
fi

log "Checking docker compose connectivity"
run_compose ps >/dev/null

mkdir -p "${WORK_DIR}"

cat > "${WORK_DIR}/manifest.txt" <<EOF
created_at_utc=${timestamp}
host=$(hostname -f 2>/dev/null || hostname)
compose_file=$(basename "${COMPOSE_FILE}")
postgres_db=${pg_db}
postgres_user=${pg_user}
with_redis=${WITH_REDIS}
with_minio=${WITH_MINIO}
include_env=${INCLUDE_ENV}
EOF

log "Backing up PostgreSQL database ${pg_db}"
run_compose exec -T postgres sh -lc "pg_dump -U \"${pg_user}\" -d \"${pg_db}\" -Fc -Z 9 --no-owner --no-privileges" > "${WORK_DIR}/postgres.dump"
run_compose exec -T postgres sh -lc "pg_dump -U \"${pg_user}\" -d \"${pg_db}\" --schema-only --no-owner --no-privileges" > "${WORK_DIR}/postgres_schema.sql"

if [ "${WITH_REDIS}" -eq 1 ]; then
  log "Backing up Redis /data volume"
  run_compose exec -T redis sh -lc "redis-cli SAVE >/dev/null && tar -C /data -cf - ." > "${WORK_DIR}/redis_data.tar"
fi

if [ "${WITH_MINIO}" -eq 1 ]; then
  log "Backing up MinIO /data volume"
  docker cp saleswhisper_minio:/data -  > "${WORK_DIR}/minio_data.tar"
fi

cp "${COMPOSE_FILE}" "${WORK_DIR}/$(basename "${COMPOSE_FILE}")"
if run_compose config >/dev/null 2>&1; then
  run_compose config > "${WORK_DIR}/docker-compose.resolved.yml"
fi

if [ "${INCLUDE_ENV}" -eq 1 ] && [ -f "${ENV_FILE}" ]; then
  cp "${ENV_FILE}" "${WORK_DIR}/.env"
  chmod 600 "${WORK_DIR}/.env"
fi

if git -C "${PROJECT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "${PROJECT_DIR}" rev-parse HEAD > "${WORK_DIR}/git_commit.txt"
fi

(
  cd "${WORK_DIR}"
  : > SHA256SUMS
  while IFS= read -r file_name; do
    sha256sum "${file_name}" >> SHA256SUMS
  done < <(find . -mindepth 1 -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\n' | sort)
)

tar -C "${BACKUP_DIR}" -czf "${archive_path}" "${backup_name}"
rm -rf "${WORK_DIR}"
WORK_DIR=""

ln -sfn "$(basename "${archive_path}")" "${BACKUP_DIR}/latest.tar.gz"

find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'saleswhisper_backup_*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete

log "Backup created: ${archive_path}"
if [ "${ALERT_SENT}" -eq 0 ]; then
  send_ops_alert "success" "Production backup completed"
  ALERT_SENT=1
fi
