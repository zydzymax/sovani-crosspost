#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
MIGRATIONS_DIR="${PROJECT_DIR}/migrations"
RUN_UP=1
RUN_MIGRATIONS=1
RUN_SMOKE=1
SMOKE_SKIP_CELERY=0
DRY_RUN=0
ALERT_SCRIPT="${SCRIPT_DIR}/send_ops_alert.py"
OPS_ALERT_NOTIFY_SUCCESS="${OPS_ALERT_NOTIFY_SUCCESS:-0}"
ALERT_SENT=0

COMPOSE_CMD=()

usage() {
  cat <<'EOF'
Usage: scripts/prod-post-deploy.sh [options]

Runs standard post-deploy flow:
  1) docker compose up -d
  2) apply SQL migrations
  3) run smoke checks

Options:
  --compose-file PATH       Path to docker compose file (default: docker-compose.prod.yml)
  --migrations-dir PATH     Path to SQL migrations folder (default: ./migrations)
  --no-up                   Skip docker compose up -d
  --no-migrations           Skip SQL migrations
  --no-smoke                Skip smoke checks
  --smoke-skip-celery       Pass --skip-celery to smoke checks
  --dry-run                 Print plan only
  -h, --help                Show help
EOF
}

log() {
  printf '[post-deploy] %s\n' "$*"
}

fail() {
  printf '[post-deploy][ERROR] %s\n' "$*" >&2
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
    --event "post-deploy" \
    --status "${status}" \
    --message "${message}" \
    --detail "host=${host_value}" \
    --detail "compose_file=$(basename "${COMPOSE_FILE}")" \
    --detail "migrations_dir=${MIGRATIONS_DIR}" \
    --detail "run_up=${RUN_UP}" \
    --detail "run_migrations=${RUN_MIGRATIONS}" \
    --detail "run_smoke=${RUN_SMOKE}" \
    --detail "smoke_skip_celery=${SMOKE_SKIP_CELERY}" >/dev/null 2>&1
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
    send_ops_alert "failure" "Post-deploy flow failed with exit code ${exit_code}"
    ALERT_SENT=1
  fi
  exit "${exit_code}"
}

on_interrupt() {
  trap - INT
  if [ "${ALERT_SENT}" -eq 0 ] && [ "${DRY_RUN}" -eq 0 ]; then
    send_ops_alert "failure" "Post-deploy flow interrupted by signal"
    ALERT_SENT=1
  fi
  exit 130
}

trap on_error ERR
trap on_interrupt INT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Command not found: $1"
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

run_sql_migrations() {
  local migration_file=""
  local count=0
  local has_files=0

  while IFS= read -r migration_file; do
    has_files=1
    count=$((count + 1))
    log "Applying migration #${count}: $(basename "${migration_file}")"
    cat "${migration_file}" | run_compose exec -T postgres sh -lc \
      "psql -v ON_ERROR_STOP=1 -U \"\${POSTGRES_USER:-saleswhisper}\" -d \"\${POSTGRES_DB:-saleswhisper_crosspost}\""
  done < <(find "${MIGRATIONS_DIR}" -maxdepth 1 -type f -name '*.sql' | sort)

  if [ "${has_files}" -eq 0 ]; then
    log "No SQL migration files found in ${MIGRATIONS_DIR}"
  else
    log "Applied ${count} migration file(s)"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compose-file)
      [ "$#" -ge 2 ] || fail "--compose-file requires a value"
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --migrations-dir)
      [ "$#" -ge 2 ] || fail "--migrations-dir requires a value"
      MIGRATIONS_DIR="$2"
      shift 2
      ;;
    --no-up)
      RUN_UP=0
      shift
      ;;
    --no-migrations)
      RUN_MIGRATIONS=0
      shift
      ;;
    --no-smoke)
      RUN_SMOKE=0
      shift
      ;;
    --smoke-skip-celery)
      SMOKE_SKIP_CELERY=1
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

[ -f "${COMPOSE_FILE}" ] || fail "Compose file not found: ${COMPOSE_FILE}"
[ -d "${MIGRATIONS_DIR}" ] || fail "Migrations directory not found: ${MIGRATIONS_DIR}"

require_cmd docker
require_cmd find
require_cmd sort

init_compose_cmd

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "compose file: ${COMPOSE_FILE}"
  log "migrations dir: ${MIGRATIONS_DIR}"
  log "run up: ${RUN_UP}"
  log "run migrations: ${RUN_MIGRATIONS}"
  log "run smoke: ${RUN_SMOKE}"
  log "smoke skip celery: ${SMOKE_SKIP_CELERY}"
  exit 0
fi

if [ "${RUN_UP}" -eq 1 ]; then
  log "Starting/updating services"
  run_compose up -d
fi

if [ "${RUN_MIGRATIONS}" -eq 1 ]; then
  log "Running SQL migrations"
  run_sql_migrations
fi

if [ "${RUN_SMOKE}" -eq 1 ]; then
  log "Running production smoke checks"
  smoke_cmd=(bash "${SCRIPT_DIR}/prod-smoke-check.sh" --compose-file "${COMPOSE_FILE}")
  if [ "${SMOKE_SKIP_CELERY}" -eq 1 ]; then
    smoke_cmd+=(--skip-celery)
  fi
  "${smoke_cmd[@]}"
fi

log "Post-deploy flow completed successfully"
if [ "${ALERT_SENT}" -eq 0 ]; then
  send_ops_alert "success" "Post-deploy flow completed successfully"
  ALERT_SENT=1
fi
