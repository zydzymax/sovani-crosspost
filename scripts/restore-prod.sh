#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env"
ARCHIVE_PATH=""
VERIFY_CHECKSUM=1
WITH_POSTGRES=1
WITH_REDIS=1
WITH_MINIO=1
MAKE_PRE_BACKUP=1
PRE_BACKUP_DIR="${PROJECT_DIR}/backups/pre-restore"
CONFIRM_RESTORE=0
DRY_RUN=0

COMPOSE_CMD=()
TMP_DIR=""
RESTORE_DIR=""

usage() {
  cat <<'EOF'
Usage: scripts/restore-prod.sh --archive PATH --yes-really-restore [options]

Options:
  --archive PATH            Backup archive (*.tar.gz) created by backup-prod.sh
  --compose-file PATH       Path to docker compose file (default: docker-compose.prod.yml)
  --skip-checksum           Skip SHA256SUMS validation
  --no-postgres             Skip PostgreSQL restore
  --no-redis                Skip Redis restore
  --no-minio                Skip MinIO restore
  --no-pre-backup           Do not create safety backup before restore
  --pre-backup-dir PATH     Where to store safety backup (default: ./backups/pre-restore)
  --dry-run                 Show planned actions without changing data
  --yes-really-restore      Mandatory confirmation flag for destructive restore
  -h, --help                Show help
EOF
}

log() {
  printf '[restore] %s\n' "$*"
}

fail() {
  printf '[restore][ERROR] %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT INT

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
    --archive)
      [ "$#" -ge 2 ] || fail "--archive requires a value"
      ARCHIVE_PATH="$2"
      shift 2
      ;;
    --compose-file)
      [ "$#" -ge 2 ] || fail "--compose-file requires a value"
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --skip-checksum)
      VERIFY_CHECKSUM=0
      shift
      ;;
    --no-postgres)
      WITH_POSTGRES=0
      shift
      ;;
    --no-redis)
      WITH_REDIS=0
      shift
      ;;
    --no-minio)
      WITH_MINIO=0
      shift
      ;;
    --no-pre-backup)
      MAKE_PRE_BACKUP=0
      shift
      ;;
    --pre-backup-dir)
      [ "$#" -ge 2 ] || fail "--pre-backup-dir requires a value"
      PRE_BACKUP_DIR="$2"
      shift 2
      ;;
    --yes-really-restore)
      CONFIRM_RESTORE=1
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

[ -n "${ARCHIVE_PATH}" ] || fail "You must pass --archive PATH"
[ -f "${ARCHIVE_PATH}" ] || fail "Archive not found: ${ARCHIVE_PATH}"
[ -f "${COMPOSE_FILE}" ] || fail "Compose file not found: ${COMPOSE_FILE}"
[ "${CONFIRM_RESTORE}" -eq 1 ] || fail "Restore is destructive. Pass --yes-really-restore to continue."

require_cmd docker
require_cmd tar
require_cmd sha256sum
require_cmd awk

init_compose_cmd

TMP_DIR="$(mktemp -d /tmp/saleswhisper_restore.XXXXXX)"
tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"

RESTORE_DIR="$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
if [ -z "${RESTORE_DIR}" ]; then
  RESTORE_DIR="${TMP_DIR}"
fi

if [ "${VERIFY_CHECKSUM}" -eq 1 ] && [ -f "${RESTORE_DIR}/SHA256SUMS" ]; then
  log "Verifying SHA256 checksums"
  (
    cd "${RESTORE_DIR}"
    sha256sum -c SHA256SUMS
  )
else
  log "Checksum validation skipped"
fi

pg_user="$(read_env_var POSTGRES_USER saleswhisper)"
pg_db="$(read_env_var POSTGRES_DB saleswhisper_crosspost)"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "archive: ${ARCHIVE_PATH}"
  log "compose file: ${COMPOSE_FILE}"
  log "postgres restore: ${WITH_POSTGRES}"
  log "redis restore: ${WITH_REDIS}"
  log "minio restore: ${WITH_MINIO}"
  log "pre-restore backup: ${MAKE_PRE_BACKUP}"
  exit 0
fi

log "Stopping write-heavy services (api/worker/beat)"
run_compose stop api worker beat >/dev/null 2>&1 || true

if [ "${MAKE_PRE_BACKUP}" -eq 1 ]; then
  log "Creating pre-restore safety backup"
  mkdir -p "${PRE_BACKUP_DIR}"
  bash "${SCRIPT_DIR}/backup-prod.sh" \
    --compose-file "${COMPOSE_FILE}" \
    --backup-dir "${PRE_BACKUP_DIR}" \
    --retention-days 30
fi

if [ "${WITH_POSTGRES}" -eq 1 ]; then
  [ -f "${RESTORE_DIR}/postgres.dump" ] || fail "postgres.dump not found in archive"
  log "Restoring PostgreSQL database ${pg_db}"
  cat "${RESTORE_DIR}/postgres.dump" | run_compose exec -T postgres sh -lc "cat > /tmp/restore_postgres.dump"
  run_compose exec -T postgres sh -lc "dropdb -U \"${pg_user}\" --if-exists \"${pg_db}\" && createdb -U \"${pg_user}\" \"${pg_db}\""
  run_compose exec -T postgres sh -lc "pg_restore -U \"${pg_user}\" -d \"${pg_db}\" --no-owner --no-privileges /tmp/restore_postgres.dump"
  run_compose exec -T postgres sh -lc "rm -f /tmp/restore_postgres.dump"
fi

if [ "${WITH_MINIO}" -eq 1 ] && [ -f "${RESTORE_DIR}/minio_data.tar" ]; then
  log "Restoring MinIO /data volume"
  cat "${RESTORE_DIR}/minio_data.tar" | run_compose exec -T minio sh -lc "mkdir -p /data && find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} + && tar -C /data -xf -"
elif [ "${WITH_MINIO}" -eq 1 ]; then
  log "minio_data.tar not found, skipping MinIO restore"
fi

if [ "${WITH_REDIS}" -eq 1 ] && [ -f "${RESTORE_DIR}/redis_data.tar" ]; then
  log "Restoring Redis /data volume"
  run_compose exec -T redis sh -lc "redis-cli FLUSHALL >/dev/null 2>&1 || true"
  cat "${RESTORE_DIR}/redis_data.tar" | run_compose exec -T redis sh -lc "mkdir -p /data && find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} + && tar -C /data -xf -"
  run_compose restart redis >/dev/null 2>&1 || true
elif [ "${WITH_REDIS}" -eq 1 ]; then
  log "redis_data.tar not found, skipping Redis restore"
fi

log "Starting services"
run_compose up -d

if [ "${WITH_POSTGRES}" -eq 1 ]; then
  tables_count="$(run_compose exec -T postgres sh -lc "psql -U \"${pg_user}\" -d \"${pg_db}\" -Atqc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" | tr -d '\r' || true)"
  log "PostgreSQL sanity check: public tables=${tables_count:-unknown}"
fi

run_compose ps
log "Restore completed from ${ARCHIVE_PATH}"
