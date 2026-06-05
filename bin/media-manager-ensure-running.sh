#!/usr/bin/env bash
#
# Ensure the media-manager container is running.
# Safe to run at @reboot and on a regular cron interval.
#
# Crontab entries (adjust path to wherever you cloned the repo):
#   @reboot  /path/to/media-manager/bin/media-manager-ensure-running.sh >>/path/to/media-manager/logs/ensure.log 2>&1
#   */5 * * * * /path/to/media-manager/bin/media-manager-ensure-running.sh >>/path/to/media-manager/logs/ensure.log 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/compose.yaml"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"
elif command -v docker &>/dev/null; then
    COMPOSE="docker compose"
else
    log "ERROR: neither podman-compose nor docker compose found"
    exit 1
fi

cd "$(dirname "$COMPOSE_FILE")"

if podman ps --filter "name=^media-manager$" --filter "status=running" --quiet 2>/dev/null | grep -q .; then
    : # running — nothing to do
else
    log "media-manager not running — removing stale containers and restarting"

    # Remove pods first (infra containers can only be removed via pod rm)
    mapfile -t pod_ids < <(podman pod ls --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
    for pod_id in "${pod_ids[@]:-}"; do
        [[ -z "$pod_id" ]] && continue
        podman pod rm -f "$pod_id" >/dev/null 2>&1 || true
    done

    # SIGKILL any remaining containers then remove by ID
    mapfile -t ids < <(podman ps -a --filter "name=media-manager" --format '{{.ID}}' 2>/dev/null || true)
    for id in "${ids[@]:-}"; do
        [[ -z "$id" ]] && continue
        podman kill -s KILL "$id" >/dev/null 2>&1 || true
    done
    sleep 2
    for id in "${ids[@]:-}"; do
        [[ -z "$id" ]] && continue
        podman rm -f "$id" >/dev/null 2>&1 || true
    done

    $COMPOSE up -d 2>&1 | tail -5
    log "media-manager started"
fi
