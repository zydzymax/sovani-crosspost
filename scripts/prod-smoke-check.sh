#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
API_BASE_URL="http://127.0.0.1:8003"
MINIO_HEALTH_URL="http://127.0.0.1:9000/minio/health/live"
TIMEOUT_SEC=10
RETRY_ATTEMPTS=10
RETRY_DELAY_SEC=3
SKIP_CELERY=0
DRY_RUN=0
ALERT_SCRIPT="${SCRIPT_DIR}/send_ops_alert.py"
OPS_ALERT_NOTIFY_SUCCESS="${OPS_ALERT_NOTIFY_SUCCESS:-0}"

COMPOSE_CMD=()
FAILED_CHECKS=0
PASSED_CHECKS=0
TOTAL_CHECKS=0
SKIPPED_CHECKS=0
LAST_CHECK_OK=0
ALERT_SENT=0

usage() {
  cat <<'EOF'
Usage: scripts/prod-smoke-check.sh [options]

Options:
  --compose-file PATH       Path to docker compose file (default: docker-compose.prod.yml)
  --api-base-url URL        API base URL (default: http://127.0.0.1:8003)
  --minio-health-url URL    MinIO health URL (default: http://127.0.0.1:9000/minio/health/live)
  --timeout-sec N           Timeout for network checks in seconds (default: 10)
  --retry-attempts N        Retry attempts for network checks (default: 10)
  --retry-delay-sec N       Delay between retries in seconds (default: 3)
  --skip-celery             Skip Celery inspect ping check
  --dry-run                 Print planned checks only
  -h, --help                Show help
EOF
}

log() {
  printf '[smoke] %s\n' "$*"
}

fail() {
  printf '[smoke][ERROR] %s\n' "$*" >&2
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
    --event "smoke-check" \
    --status "${status}" \
    --message "${message}" \
    --detail "host=${host_value}" \
    --detail "compose_file=$(basename "${COMPOSE_FILE}")" \
    --detail "api_base_url=${API_BASE_URL}" \
    --detail "passed=${PASSED_CHECKS}" \
    --detail "failed=${FAILED_CHECKS}" \
    --detail "skipped=${SKIPPED_CHECKS}" \
    --detail "total=${TOTAL_CHECKS}" >/dev/null 2>&1
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
    send_ops_alert "failure" "Production smoke-check script failed unexpectedly (exit code ${exit_code})"
    ALERT_SENT=1
  fi
  exit "${exit_code}"
}

on_interrupt() {
  trap - INT
  if [ "${ALERT_SENT}" -eq 0 ] && [ "${DRY_RUN}" -eq 0 ]; then
    send_ops_alert "failure" "Production smoke-check interrupted by signal"
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

retry_func() {
  local function_name="$1"
  local attempts="$2"
  local delay_sec="$3"
  local i=1

  while [ "${i}" -le "${attempts}" ]; do
    if "${function_name}"; then
      return 0
    fi
    if [ "${i}" -lt "${attempts}" ]; then
      sleep "${delay_sec}"
    fi
    i=$((i + 1))
  done

  return 1
}

check() {
  local name="$1"
  local function_name="$2"
  local with_retry="${3:-0}"

  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  log "Check ${TOTAL_CHECKS}: ${name}"

  if [ "${with_retry}" -eq 1 ]; then
    if retry_func "${function_name}" "${RETRY_ATTEMPTS}" "${RETRY_DELAY_SEC}"; then
      PASSED_CHECKS=$((PASSED_CHECKS + 1))
      log "OK: ${name}"
      LAST_CHECK_OK=1
      return 0
    else
      FAILED_CHECKS=$((FAILED_CHECKS + 1))
      log "FAIL: ${name}"
      LAST_CHECK_OK=0
      return 0
    fi
  fi

  if "${function_name}"; then
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
    log "OK: ${name}"
    LAST_CHECK_OK=1
    return 0
  else
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
    log "FAIL: ${name}"
    LAST_CHECK_OK=0
    return 0
  fi
}

check_compose_connectivity() {
  run_compose ps >/dev/null 2>&1
}

check_required_services_running() {
  local required_services=("postgres" "redis" "minio" "api" "worker" "beat")
  local running_services
  local service

  running_services="$(run_compose ps --services --filter status=running 2>/dev/null | tr '\n' ' ')" || return 1

  for service in "${required_services[@]}"; do
    if ! grep -Eq "(^|[[:space:]])${service}($|[[:space:]])" <<<"${running_services}"; then
      log "Service is not running: ${service}"
      return 1
    fi
  done

  return 0
}

check_api_health() {
  local body
  body="$(curl -fsS --max-time "${TIMEOUT_SEC}" "${API_BASE_URL}/api/v1/health" 2>/dev/null)" || return 1
  grep -Eq '"status"[[:space:]]*:[[:space:]]*"(healthy|degraded)"' <<<"${body}"
}

check_api_ready() {
  local body
  body="$(curl -fsS --max-time "${TIMEOUT_SEC}" "${API_BASE_URL}/api/v1/ready" 2>/dev/null)" || return 1
  grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' <<<"${body}"
}

check_postgres() {
  local output
  output="$(
    run_compose exec -T postgres sh -lc \
      "psql -U \"\${POSTGRES_USER:-saleswhisper}\" -d \"\${POSTGRES_DB:-saleswhisper_crosspost}\" -Atqc 'SELECT 1'" 2>/dev/null
  )" || return 1

  [ "$(echo "${output}" | tr -d '\r\n ')" = "1" ]
}

check_redis() {
  local output
  output="$(run_compose exec -T redis redis-cli ping 2>/dev/null)" || return 1
  [ "$(echo "${output}" | tr -d '\r\n ')" = "PONG" ]
}

check_minio() {
  curl -fsS --max-time "${TIMEOUT_SEC}" "${MINIO_HEALTH_URL}" >/dev/null 2>&1
}

check_celery_ping() {
  local output
  output="$(run_compose exec -T worker celery -A app.workers.celery_app inspect ping --timeout=5 2>/dev/null)" || return 1
  grep -qi "pong" <<<"${output}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --compose-file)
      [ "$#" -ge 2 ] || fail "--compose-file requires a value"
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --api-base-url)
      [ "$#" -ge 2 ] || fail "--api-base-url requires a value"
      API_BASE_URL="$2"
      shift 2
      ;;
    --minio-health-url)
      [ "$#" -ge 2 ] || fail "--minio-health-url requires a value"
      MINIO_HEALTH_URL="$2"
      shift 2
      ;;
    --timeout-sec)
      [ "$#" -ge 2 ] || fail "--timeout-sec requires a value"
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --retry-attempts)
      [ "$#" -ge 2 ] || fail "--retry-attempts requires a value"
      RETRY_ATTEMPTS="$2"
      shift 2
      ;;
    --retry-delay-sec)
      [ "$#" -ge 2 ] || fail "--retry-delay-sec requires a value"
      RETRY_DELAY_SEC="$2"
      shift 2
      ;;
    --skip-celery)
      SKIP_CELERY=1
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

[[ "${TIMEOUT_SEC}" =~ ^[0-9]+$ ]] || fail "--timeout-sec must be a non-negative integer"
[[ "${RETRY_ATTEMPTS}" =~ ^[0-9]+$ ]] || fail "--retry-attempts must be a non-negative integer"
[[ "${RETRY_DELAY_SEC}" =~ ^[0-9]+$ ]] || fail "--retry-delay-sec must be a non-negative integer"

[ -f "${COMPOSE_FILE}" ] || fail "Compose file not found: ${COMPOSE_FILE}"

require_cmd docker
require_cmd curl
require_cmd grep
require_cmd tr

init_compose_cmd

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run mode"
  log "compose file: ${COMPOSE_FILE}"
  log "api base url: ${API_BASE_URL}"
  log "minio health url: ${MINIO_HEALTH_URL}"
  log "timeout sec: ${TIMEOUT_SEC}"
  log "retry attempts: ${RETRY_ATTEMPTS}"
  log "retry delay sec: ${RETRY_DELAY_SEC}"
  log "skip celery: ${SKIP_CELERY}"
  exit 0
fi

compose_ready=0
check "Docker Compose connectivity" check_compose_connectivity
if [ "${LAST_CHECK_OK}" -eq 1 ]; then
  compose_ready=1
fi

check "API /api/v1/health" check_api_health 1
check "API /api/v1/ready" check_api_ready 1

if [ "${compose_ready}" -eq 1 ]; then
  check "Required services are running" check_required_services_running
  check "PostgreSQL connectivity" check_postgres
  check "Redis ping" check_redis
  check "MinIO health endpoint" check_minio 1

  if [ "${SKIP_CELERY}" -eq 0 ]; then
    check "Celery inspect ping" check_celery_ping
  else
    SKIPPED_CHECKS=$((SKIPPED_CHECKS + 1))
    log "Skip: Celery inspect ping"
  fi
else
  SKIPPED_CHECKS=$((SKIPPED_CHECKS + 4))
  log "Skip: compose-dependent checks (services, postgres, redis, minio)"
  if [ "${SKIP_CELERY}" -eq 0 ]; then
    SKIPPED_CHECKS=$((SKIPPED_CHECKS + 1))
    log "Skip: Celery inspect ping (compose unavailable)"
  else
    SKIPPED_CHECKS=$((SKIPPED_CHECKS + 1))
    log "Skip: Celery inspect ping"
  fi
fi

log "Summary: passed=${PASSED_CHECKS} failed=${FAILED_CHECKS} skipped=${SKIPPED_CHECKS} total=${TOTAL_CHECKS}"

if [ "${FAILED_CHECKS}" -gt 0 ]; then
  if [ "${ALERT_SENT}" -eq 0 ]; then
    send_ops_alert "failure" "Production smoke-check completed with failures"
    ALERT_SENT=1
  fi
  exit 1
fi

if [ "${ALERT_SENT}" -eq 0 ]; then
  send_ops_alert "success" "Production smoke-check passed"
  ALERT_SENT=1
fi

exit 0
