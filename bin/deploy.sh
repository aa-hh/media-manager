#!/usr/bin/env bash
# Build and redeploy the media-manager app container.
# Handles stuck/broken container states by killing via ID before removal.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    BOLD='\033[1m'; DIM='\033[2m'; GREEN='\033[32m'
    RED='\033[31m'; CYAN='\033[36m'; RESET='\033[0m'
else
    BOLD=''; DIM=''; GREEN=''; RED=''; CYAN=''; RESET=''
fi

# ── Spinner ───────────────────────────────────────────────────────────────────
_spin_pid=''
spin_start() {
    [[ -t 1 ]] || return
    local msg="$1"
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    ( i=0
      while true; do
          printf "\r  ${CYAN}${frames[$i]}${RESET}  %s" "$msg" >&2
          i=$(( (i+1) % ${#frames[@]} ))
          sleep 0.1
      done
    ) &
    _spin_pid=$!
}
spin_stop() {
    if [[ -n "$_spin_pid" ]]; then
        kill "$_spin_pid" 2>/dev/null; wait "$_spin_pid" 2>/dev/null || true
        _spin_pid=''; printf "\r\033[K" >&2
    fi
    if [[ "$1" == ok ]]; then
        echo -e "  ${GREEN}✓${RESET}  $2"
    else
        echo -e "  ${RED}✗${RESET}  $2"
    fi
}
trap '[[ -n "$_spin_pid" ]] && { kill "$_spin_pid" 2>/dev/null; printf "\r\033[K"; }' EXIT

step() { echo -e "\n${BOLD}${CYAN}▸ $*${RESET}"; }

run_quiet() {
    local label="$1"; shift
    local tmp; tmp=$(mktemp)
    spin_start "$label"
    if "$@" >"$tmp" 2>&1; then
        spin_stop ok "$label"
        rm -f "$tmp"
    else
        spin_stop fail "$label"
        echo
        cat "$tmp"
        rm -f "$tmp"
        exit 1
    fi
}

# ── Detect compose command ────────────────────────────────────────────────────
if command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"
elif command -v docker &>/dev/null; then
    COMPOSE="docker compose"
else
    echo -e "  ${RED}✗${RESET}  neither podman-compose nor docker compose found" >&2
    exit 1
fi

echo -e "\n${BOLD}  media-manager deploy${RESET}  ${DIM}$(date '+%H:%M:%S')${RESET}"
echo -e "  ${DIM}────────────────────────────────${RESET}"

# ── Phase 1: Kill & remove all related containers ────────────────────────────
step "Removing stale containers"
spin_start "finding containers"

# Find every container whose name contains "media-manager" (catches any variant)
mapfile -t ids < <(podman ps -a --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)

spin_stop ok "found ${#ids[@]} container(s)"

if [[ ${#ids[@]} -gt 0 ]]; then
    spin_start "killing and removing"

    # Step 1: remove pods first — this cascades to infra + member containers
    # and handles the "infra container cannot be removed without removing the pod" case.
    # Must happen BEFORE any individual container rm attempts.
    mapfile -t pod_ids < <(podman pod ls --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
    for pod_id in "${pod_ids[@]:-}"; do
        [[ -z "$pod_id" ]] && continue
        podman pod rm -f "$pod_id" >/dev/null 2>&1 || true
    done

    # Step 2: SIGKILL + rm any containers that weren't in a pod
    for id in "${ids[@]}"; do
        podman kill -s KILL "$id" >/dev/null 2>&1 || true
    done
    sleep 1
    for id in "${ids[@]}"; do
        podman rm -f "$id" >/dev/null 2>&1 || true
    done

    spin_stop ok "removed containers and pods"
fi

# ── Phase 2: Guard — verify no conflicts remain ───────────────────────────────
step "Verifying clean state"
spin_start "checking for conflicts"

remaining=$(podman ps -a --filter "name=media-manager" --format '{{.Names}}' 2>/dev/null || true)
if [[ -n "$remaining" ]]; then
    spin_stop fail "stale containers still present"
    echo
    echo -e "  ${RED}Cannot proceed — these containers could not be removed:${RESET}"
    echo "$remaining" | sed 's/^/    /'
    echo
    echo "  Try: podman rm -f \$(podman ps -a --filter name=media-manager --format '{{.ID}}')"
    exit 1
fi

spin_stop ok "no conflicts"

# ── Phase 3: Build ────────────────────────────────────────────────────────────
step "Building image"
run_quiet "building image" $COMPOSE build

# ── Phase 4: Start ────────────────────────────────────────────────────────────
step "Starting container"
run_quiet "starting container" $COMPOSE up -d

# ── Phase 5: Verify container is actually running ────────────────────────────
step "Verifying startup"
spin_start "waiting for container to come up"

ok_flag=0
for i in $(seq 1 20); do
    if podman ps --filter "name=^media-manager$" --filter "status=running" --format '{{.ID}}' 2>/dev/null | grep -q .; then
        ok_flag=1
        break
    fi
    sleep 0.5
done

if [[ $ok_flag -eq 1 ]]; then
    cid=$(podman ps --filter "name=^media-manager$" --format '{{.ID}}' | head -1)
    spin_stop ok "container is up (${cid:0:12})"
else
    spin_stop fail "container did not start in time"
    echo
    echo -e "  ${RED}Last 30 log lines:${RESET}"
    podman logs media-manager --tail 30 2>&1 | sed 's/^/    /' || true
    exit 1
fi

# ── Done ──────────────────────────────────────────────────────────────────────
PORT="${PORT:-10400}"
echo -e "\n  ${GREEN}${BOLD}All done.${RESET}  Listening on port ${PORT}\n"
