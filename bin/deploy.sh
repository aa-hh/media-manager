#!/usr/bin/env bash
# Build and redeploy the media-manager app container.
# Handles stuck/broken container states by killing via ID before removal.
#
# Flags:
#   --fresh    Force --no-cache --pull (full rebuild, re-downloads everything)
#   --pull     Pull the latest base image before building

set -euo pipefail

PULL=0; FRESH=0
for arg in "$@"; do
    [[ "$arg" == "--pull"  ]] && PULL=1
    [[ "$arg" == "--fresh" ]] && FRESH=1 && PULL=1
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Load PORT from config/.env so compose port mapping resolves correctly ────
if [[ -f "$ROOT/config/.env" ]]; then
    export $(grep -E '^PORT=' "$ROOT/config/.env" | xargs) 2>/dev/null || true
fi
PORT="${PORT:-10400}"

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
        echo; cat "$tmp"; rm -f "$tmp"
        exit 1
    fi
}

# ── Progress bar (for steps with known N/M progress) ─────────────────────────
_bar() {
    local n="$1" m="$2" width=24
    local filled=$(( n * width / m ))
    local empty=$(( width - filled ))
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done
    local pct=$(( n * 100 / m ))
    printf "[%s] %3d%%" "$bar" "$pct"
}

# run_build: runs a build command, shows a real progress bar using STEP N/M output
run_build() {
    local label="$1"; shift
    local tmp; tmp=$(mktemp)

    printf "  ${CYAN}…${RESET}  %s" "$label"

    set +e
    "$@" >"$tmp" 2>&1 &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        local last
        last=$(grep -oiE 'step [0-9]+/[0-9]+' "$tmp" 2>/dev/null | tail -1)
        if [[ -n "$last" ]]; then
            local n m
            n=$(echo "$last" | grep -oE '[0-9]+' | head -1)
            m=$(echo "$last" | grep -oE '[0-9]+' | tail -1)
            local bar; bar=$(_bar "$n" "$m")
            printf "\r  ${CYAN}%s${RESET}  %s   " "$bar" "$label"
        fi
        sleep 0.3
    done
    wait "$pid"; local rc=$?
    set -e

    if [[ $rc -eq 0 ]]; then
        local last; last=$(grep -oiE 'step [0-9]+/[0-9]+' "$tmp" 2>/dev/null | tail -1)
        local m=1
        [[ -n "$last" ]] && m=$(echo "$last" | grep -oE '[0-9]+' | tail -1)
        local bar; bar=$(_bar "$m" "$m")
        printf "\r  ${GREEN}%s${RESET}  %s  ${GREEN}✓${RESET}\n" "$bar" "$label"
        rm -f "$tmp"
    else
        printf "\r\033[K  ${RED}✗${RESET}  %s\n" "$label"
        echo; cat "$tmp"; rm -f "$tmp"
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

mapfile -t ids < <(podman ps -a --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)

spin_stop ok "found ${#ids[@]} container(s)"

if [[ ${#ids[@]} -gt 0 ]]; then
    spin_start "killing and removing"

    # Remove pods first — cascades to infra + member containers
    mapfile -t pod_ids < <(podman pod ls --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
    for pod_id in "${pod_ids[@]:-}"; do
        [[ -z "$pod_id" ]] && continue
        timeout 10 podman pod rm -f "$pod_id" >/dev/null 2>&1 || true
    done

    # SIGKILL + rm containers not in a pod (parallel, with timeouts)
    printf '%s\n' "${ids[@]}" | xargs -r -P8 -I{} timeout 5  podman kill -s KILL {} >/dev/null 2>&1 || true
    printf '%s\n' "${ids[@]}" | xargs -r -P8 -I{} timeout 10 podman rm -f      {} >/dev/null 2>&1 || true

    # Escalate: find host PIDs for any survivors and SIGKILL directly
    mapfile -t stuck_ids < <(podman ps -a --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
    if [[ ${#stuck_ids[@]} -gt 0 ]]; then
        for id in "${stuck_ids[@]}"; do
            pid=$(timeout 5 podman inspect "$id" --format '{{.State.Pid}}' 2>/dev/null || true)
            [[ -n "$pid" && "$pid" != "0" ]] && kill -9 "$pid" 2>/dev/null || true
        done

        mapfile -t pod_ids < <(podman pod ls --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
        for pod_id in "${pod_ids[@]:-}"; do
            [[ -z "$pod_id" ]] && continue
            timeout 10 podman pod rm -f "$pod_id" >/dev/null 2>&1 || true
        done
        printf '%s\n' "${stuck_ids[@]}" | xargs -r -P8 -I{} timeout 10 podman rm -f {} >/dev/null 2>&1 || true
    fi

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

# ── Phase 2.5: Regenerate static site ────────────────────────────────────────
step "Regenerating site"
run_quiet "rendering templates → public/" bash -c "cd '$ROOT' && . .venv/bin/activate && python scripts/run.py generate"

# ── Phase 3: Build ────────────────────────────────────────────────────────────
step "Building image"
if [[ $FRESH -eq 1 ]]; then
    run_build "building image (fresh)" $COMPOSE build --no-cache --pull
elif [[ $PULL -eq 1 ]]; then
    run_build "building image" $COMPOSE build --pull
else
    run_build "building image" $COMPOSE build
fi

# ── Phase 4: Start ────────────────────────────────────────────────────────────
step "Starting container"
run_quiet "starting container" $COMPOSE up -d

# ── Phase 5: Verify container is actually running ────────────────────────────
step "Verifying startup"
spin_start "waiting for container to come up"

ok_flag=0
for i in $(seq 1 20); do
    if podman ps --filter "name=^media-manager$" --filter "status=running" --format '{{.ID}}' 2>/dev/null | grep -q .; then
        ok_flag=1; break
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
echo -e "\n  ${GREEN}${BOLD}All done.${RESET}  Listening on port ${PORT}\n"
