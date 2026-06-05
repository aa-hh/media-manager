#!/usr/bin/env bash
# Trigger built-in application update for Sonarr or Radarr.
# Usage: bin/trigger-update.sh sonarr|radarr
set -euo pipefail

ENV_FILE="$HOME/homepage/config/homepage.env"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

SERVICE="${1:-}"
case "$SERVICE" in
  sonarr)
    URL="${SONARR_URL:-https://sonarr.box.ahamilton.online}"
    KEY="$HOMEPAGE_VAR_SONARR_API_KEY"
    ;;
  radarr)
    URL="${RADARR_URL:-https://radarr.box.ahamilton.online}"
    KEY="$HOMEPAGE_VAR_RADARR_API_KEY"
    ;;
  *)
    echo "Usage: $0 sonarr|radarr"
    echo "Note: Overseerr and Tautulli require manual updates."
    exit 1
    ;;
esac

echo "Triggering $SERVICE update..."
RESPONSE=$(curl -sf -X POST "$URL/api/v3/command" \
  -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"ApplicationUpdate"}')

echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
echo "Update command sent. Check $SERVICE UI for progress."
