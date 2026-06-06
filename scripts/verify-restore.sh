#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env"
ARCHIVE_PATH=""
VERIFY_CHECKSUM=1
RUN_DB_RESTORE_TEST=1
DRY_RUN=0

COMPOSE_CMD=()
TMP_DIR=""
RESTORE_DIR=""
VERIFY_DB=""

usage() {
  cat <<'EOF'
Usage: scripts/verify-restore.sh --archive PATH [options]

Options:
  --archive PATH            Backup archive (*.tar.gz) created by backup-prod.sh
  --compose-file PATH       Path to docker compose file (default: docker-compose.prod.yml)
  --skip-checksum           Skip SHA256SUMS validation
  --no-db-test              Skip PostgreSQL test restore into temporary DB
  --dry-run                 Print verification plan only
  -h, --help                Show help
EOF
}

log() {
  printf '[verify] %s\n' "$*"
}

fail() {
  printf '[verify][ERROR] %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [ -n "${VERIFY_DB}" ]; then
    run_compose exec -T postgres sh -lc "dropdb -U \"${pg_user:-saleswhisper}\" --if-exists \"${VERIFY_DB}\"" >/dev/null 2>&1 || true
  fi

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
    --no-db-test)
      RUN_DB_RESTORE_TEST=0
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

require_cmd docker
require_cmd tar
require_cmd sha256sum
require_cmd awk

init_compose_cmd

TMP_DIR="$(mktemp -d /tmp/saleswhisper_verify.XXXXXX)"
tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"
RESTORE_DIR="$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
if [ -z "${RESTORE_DIR}" ]; then
  RESTORE_DIR="${TMP_DIR}"
fi

[ -f "${RESTORE_DIR}/postgres.dump" ] || fail "Archive does not contain postgres.dump"
[ -s "${RESTORE_DIR}/postgres.dump" ] || fail "postgres.dump is empty"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "archive: ${ARCHIVE_PATH}"
  log "checksum check: ${VERIFY_CHECKSUM}"
  log "db restore test: ${RUN_DB_RESTORE_TEST}"
  exit 0
fi

if [ "${VERIFY_CHECKSUM}" -eq 1 ] && [ -f "${RESTORE_DIR}/SHA256SUMS" ]; then
  log "Checking SHA256SUMS"
  (
    cd "${RESTORE_DIR}"
    sha256sum -c SHA256SUMS
  )
else
  log "Checksum validation skipped"
fi

if [ -f "${RESTORE_DIR}/minio_data.tar" ]; then
  log "Validating minio_data.tar integrity"
  tar -tf "${RESTORE_DIR}/minio_data.tar" >/dev/null
fi

if [ -f "${RESTORE_DIR}/redis_data.tar" ]; then
  log "Validating redis_data.tar integrity"
  tar -tf "${RESTORE_DIR}/redis_data.tar" >/dev/null
fi

if [ "${RUN_DB_RESTORE_TEST}" -eq 1 ]; then
  pg_user="$(read_env_var POSTGRES_USER saleswhisper)"
  verify_ts="$(date -u +%Y%m%d%H%M%S)"
  VERIFY_DB="restore_verify_${verify_ts}"

  log "Running PostgreSQL test restore into temporary DB: ${VERIFY_DB}"
  cat "${RESTORE_DIR}/postgres.dump" | run_compose exec -T postgres sh -lc "cat > /tmp/verify_postgres.dump"
  run_compose exec -T postgres sh -lc "createdb -U \"${pg_user}\" \"${VERIFY_DB}\""
  run_compose exec -T postgres sh -lc "pg_restore -U \"${pg_user}\" -d \"${VERIFY_DB}\" --no-owner --no-privileges /tmp/verify_postgres.dump"
  tables_count="$(run_compose exec -T postgres sh -lc "psql -U \"${pg_user}\" -d \"${VERIFY_DB}\" -Atqc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" | tr -d '\r')"
  run_compose exec -T postgres sh -lc "dropdb -U \"${pg_user}\" --if-exists \"${VERIFY_DB}\""
  VERIFY_DB=""
  run_compose exec -T postgres sh -lc "rm -f /tmp/verify_postgres.dump"
  log "PostgreSQL restore test passed, public tables=${tables_count}"
fi

log "Verification passed for ${ARCHIVE_PATH}"
