#!/usr/bin/env bash
set -euo pipefail

PORTS_STR="8080 8081 8765 50051 50053 50054 50055 50056 50057 50058 50059 50060"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() {
  printf '[apme-cleanup] %s\n' "$*"
}

_listener_cmd=""
_detect_listener_tool() {
  if [[ -n "${_listener_cmd}" ]]; then
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    _listener_cmd="ss"
  elif command -v lsof >/dev/null 2>&1; then
    _listener_cmd="lsof"
  else
    log "ERROR: neither 'ss' nor 'lsof' found — cannot inspect listening ports"
    exit 1
  fi
}

list_apme_listeners() {
  _detect_listener_tool
  case "${_listener_cmd}" in
    ss)
      ss -ltnpH 2>/dev/null | awk -v ports="${PORTS_STR}" '
        BEGIN {
          count = split(ports, items, " ")
          for (i = 1; i <= count; i++) wanted[items[i]] = 1
        }
        {
          port = $4; sub(/^.*:/, "", port)
          if (port in wanted) print
        }
      '
      ;;
    lsof)
      lsof -iTCP -sTCP:LISTEN -nP 2>/dev/null | awk -v ports="${PORTS_STR}" '
        BEGIN {
          count = split(ports, items, " ")
          for (i = 1; i <= count; i++) wanted[items[i]] = 1
        }
        NR > 1 {
          port = $9; sub(/^.*:/, "", port)
          if (port in wanted) print
        }
      '
      ;;
  esac
}

collect_apme_pids() {
  _detect_listener_tool
  list_apme_listeners | awk -v tool="${_listener_cmd}" '
    tool == "ss" {
      line = $0
      while (match(line, /pid=[0-9]+/)) {
        print substr(line, RSTART + 4, RLENGTH - 4)
        line = substr(line, RSTART + RLENGTH)
      }
    }
    tool == "lsof" && NR >= 1 {
      print $2
    }
  ' | sort -u
}

log "Stopping APME pod via tox if possible..."
if command -v tox >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/tox.ini" ]]; then
  (
    cd "${REPO_ROOT}"
    tox -e down >/dev/null 2>&1 || true
  )
fi

log "Removing podman pod apme-pod if it still exists..."
if command -v podman >/dev/null 2>&1; then
  podman pod rm -f apme-pod >/dev/null 2>&1 || true
fi

mapfile -t pids < <(collect_apme_pids || true)

if ((${#pids[@]} > 0)); then
  log "Sending SIGTERM to lingering listener PIDs: ${pids[*]}"
  kill "${pids[@]}" 2>/dev/null || true
  sleep 1
fi

mapfile -t remaining < <(collect_apme_pids || true)

if ((${#remaining[@]} > 0)); then
  log "Escalating to SIGKILL for stubborn PIDs: ${remaining[*]}"
  kill -9 "${remaining[@]}" 2>/dev/null || true
  sleep 1
fi

leftovers="$(list_apme_listeners || true)"
if [[ -n "${leftovers}" ]]; then
  log "Some APME ports are still busy:"
  printf '%s\n' "${leftovers}"
  exit 1
fi

log "APME ports are free: ${PORTS_STR}"
