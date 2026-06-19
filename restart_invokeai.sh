#!/usr/bin/env bash

set -euo pipefail

INVOKEAI_EXECUTABLE="${INVOKEAI_EXECUTABLE:-/usr/local/bin/invokeai-web}"
STATE_DIR="${INVOKEAI_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/invokeai-web}"
PID_FILE="${INVOKEAI_PID_FILE:-$STATE_DIR/invokeai.pid}"
LOG_DIR="${INVOKEAI_LOG_DIR:-$STATE_DIR/logs}"
LOG_FILE="${INVOKEAI_LOG_FILE:-$LOG_DIR/invokeai.log}"
STOP_TIMEOUT="${INVOKEAI_STOP_TIMEOUT:-60}"
POLL_INTERVAL="${INVOKEAI_POLL_INTERVAL:-1}"

usage() {
  cat <<'EOF'
Usage:
  bash restart_invokeai.sh -- <invokeai start command...>

Behavior:
  - If InvokeAI is already running, the script sends SIGINT, waits for a clean exit,
    and restarts it using the same command line from /proc/<pid>/cmdline.
  - If nothing is running, pass the start command after --, or set
    INVOKEAI_START_CMD to a shell command string.
  - If neither is provided, the script starts /usr/local/bin/invokeai-web and lets
    InvokeAI resolve its own root unless INVOKEAI_ROOT is explicitly set.

Examples:
  bash restart_invokeai.sh
  INVOKEAI_ROOT=/path/to/invokeai-data bash restart_invokeai.sh
  bash restart_invokeai.sh -- /usr/local/bin/invokeai-web --host 0.0.0.0 --port 9090
  INVOKEAI_START_CMD='/usr/local/bin/invokeai-web --host 0.0.0.0 --port 9090' bash restart_invokeai.sh

Environment:
  INVOKEAI_EXECUTABLE    Default: /usr/local/bin/invokeai-web
  INVOKEAI_ROOT          Optional. If set, added as --root <path> on cold start.
  INVOKEAI_STATE_DIR     Default: ${XDG_RUNTIME_DIR:-/tmp}/invokeai-web
  INVOKEAI_PID_FILE      Default: <state_dir>/invokeai.pid
  INVOKEAI_LOG_DIR       Default: <state_dir>/logs
  INVOKEAI_LOG_FILE      Default: <log_dir>/invokeai.log
  INVOKEAI_STOP_TIMEOUT  Default: 60 seconds
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

pid_is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

read_pid_file() {
  if [[ -f "$PID_FILE" ]]; then
    tr -dc '0-9' < "$PID_FILE"
  fi
}

find_invokeai_pids() {
  local pid
  local -a pids=()
  local -A seen=()

  pid="$(read_pid_file || true)"
  if pid_is_running "$pid"; then
    pids+=("$pid")
    seen["$pid"]=1
  fi

  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    if [[ -z "${seen[$pid]:-}" ]] && pid_is_running "$pid"; then
      pids+=("$pid")
      seen["$pid"]=1
    fi
  done < <(pgrep -f '/usr/local/bin/invokeai-web|(^| )invokeai-web( |$)' || true)

  [[ "${#pids[@]}" -gt 0 ]] || return 1
  printf '%s\n' "${pids[@]}"
}

read_cmdline() {
  local pid="$1"
  local -n out_ref="$2"

  [[ -r "/proc/$pid/cmdline" ]] || return 1

  mapfile -d '' -t out_ref < "/proc/$pid/cmdline"
  [[ "${#out_ref[@]}" -gt 0 ]]
}

parse_start_args() {
  local -n out_ref="$1"
  shift

  if [[ $# -gt 0 ]]; then
    if [[ "$1" != "--" ]]; then
      usage >&2
      fail "pass the start command after --"
    fi
    shift
    out_ref=("$@")
    return 0
  fi

  if [[ -n "${INVOKEAI_START_CMD:-}" ]]; then
    read -r -a out_ref <<< "$INVOKEAI_START_CMD"
    return 0
  fi

  out_ref=()
}

stop_process() {
  local pid="$1"
  local deadline

  log "Stopping InvokeAI process $pid with SIGINT"
  kill -INT "$pid"

  deadline=$((SECONDS + STOP_TIMEOUT))
  while pid_is_running "$pid"; do
    if (( SECONDS >= deadline )); then
      log "Process $pid did not stop within ${STOP_TIMEOUT}s, sending SIGTERM"
      kill -TERM "$pid" 2>/dev/null || true
      deadline=$((SECONDS + 10))
      while pid_is_running "$pid"; do
        if (( SECONDS >= deadline )); then
          fail "process $pid did not exit after SIGTERM"
        fi
        sleep "$POLL_INTERVAL"
      done
      break
    fi
    sleep "$POLL_INTERVAL"
  done

  rm -f "$PID_FILE"
  log "InvokeAI process $pid stopped"
}

stop_processes() {
  local -a pids=("$@")
  local pid

  [[ "${#pids[@]}" -gt 0 ]] || return 0
  log "Stopping ${#pids[@]} InvokeAI instance(s): ${pids[*]}"
  for pid in "${pids[@]}"; do
    stop_process "$pid"
  done
}

commands_match() {
  local -n first_ref="$1"
  local -n second_ref="$2"
  local index

  [[ "${#first_ref[@]}" -eq "${#second_ref[@]}" ]] || return 1
  for ((index = 0; index < ${#first_ref[@]}; index++)); do
    [[ "${first_ref[$index]}" == "${second_ref[$index]}" ]] || return 1
  done
}

log_running_process() {
  local pid="$1"
  local -a command=()

  if read_cmdline "$pid" command; then
    log "Matched InvokeAI PID $pid: ${command[*]}"
  else
    log "Matched InvokeAI PID $pid: <unable to read cmdline>"
  fi
}

start_process() {
  local -a command=("$@")
  local new_pid

  [[ "${#command[@]}" -gt 0 ]] || fail "no start command available"
  if [[ "$INVOKEAI_EXECUTABLE" == */* && ! -x "$INVOKEAI_EXECUTABLE" ]]; then
    fail "InvokeAI executable not found or not executable: $INVOKEAI_EXECUTABLE"
  fi
  if [[ "${command[0]}" != */* ]] && ! command -v "${command[0]}" >/dev/null 2>&1; then
    fail "InvokeAI command not found in PATH: ${command[0]}"
  fi

  mkdir -p "$LOG_DIR"

  log "Starting InvokeAI: ${command[*]}"
  if command -v setsid >/dev/null 2>&1; then
    setsid "${command[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  else
    nohup "${command[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  fi
  new_pid=$!

  echo "$new_pid" > "$PID_FILE"
  log "InvokeAI started with PID $new_pid"
  log "Logs: $LOG_FILE"
}

main() {
  local pid
  local -a running_pids=()
  local -a running_command=()
  local -a current_command=()
  local -a requested_command=()
  local -a start_command=()
  local -a default_command=("$INVOKEAI_EXECUTABLE")

  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  parse_start_args requested_command "$@"

  mapfile -t running_pids < <(find_invokeai_pids)

  if [[ "${#running_pids[@]}" -gt 0 ]]; then
    for pid in "${running_pids[@]}"; do
      log_running_process "$pid"
      if ! read_cmdline "$pid" current_command; then
        fail "could not read /proc/$pid/cmdline; pass an explicit start command after --"
      fi
      if [[ "${#running_command[@]}" -eq 0 ]]; then
        running_command=("${current_command[@]}")
      elif ! commands_match running_command current_command; then
        fail "multiple InvokeAI instances are running with different commands; pass an explicit start command after --"
      fi
    done

    stop_processes "${running_pids[@]}"
    if [[ "${#requested_command[@]}" -gt 0 ]]; then
      start_command=("${requested_command[@]}")
    else
      start_command=("${running_command[@]}")
    fi
  else
    if [[ "${#requested_command[@]}" -gt 0 ]]; then
      start_command=("${requested_command[@]}")
    else
      if [[ -n "${INVOKEAI_ROOT:-}" ]]; then
        default_command+=("--root" "$INVOKEAI_ROOT")
      fi
      start_command=("${default_command[@]}")
    fi
  fi

  if [[ "${#start_command[@]}" -eq 0 ]]; then
    fail "InvokeAI is not running and no start command was provided"
  fi

  start_process "${start_command[@]}"
}

main "$@"