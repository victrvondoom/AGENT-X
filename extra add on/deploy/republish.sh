#!/usr/bin/env bash
#
# Re-establish the public link after a restart.
#
#   ./deploy/republish.sh
#
# Why this exists: the dashboard is permanently hosted on Firebase Hosting,
# but the agent engine runs locally and is exposed through a Cloudflare
# quick tunnel. Quick tunnels get a new random hostname every time they
# start, and the frontend has the API URL baked into its bundle at build
# time - so after a reboot the hosted site is still up but pointing at a
# hostname that no longer exists.
#
# This starts a fresh tunnel, rebuilds the frontend against it, updates the
# backend's allowed CORS origins, and redeploys. One command instead of
# four manual steps that are easy to do in the wrong order.
#
# This is only needed while the backend has nowhere permanent to live.
# Once billing is enabled on the project, deploy/deploy.sh puts the engine
# on Cloud Run with a stable URL and this script becomes unnecessary.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GCP_PROJECT_ID:-algebraic-pier-465415-a6}"
BACKEND_PORT=8000

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- 1. Backend must actually be up before we expose it -------------------
say "Checking the agent engine on :$BACKEND_PORT"
if ! curl -sf --max-time 10 "http://localhost:$BACKEND_PORT/api/system-info" >/dev/null; then
  cat >&2 <<'NOBACKEND'
ERROR: nothing is answering on localhost:8000.

Start the engine first, from the backend directory:
    python -m uvicorn app.server:app --port 8000
and the worker, in another terminal:
    python -m app.worker
NOBACKEND
  exit 1
fi

# --- 2. Fresh tunnel ------------------------------------------------------
say "Starting a Cloudflare tunnel"
LOG="$(mktemp -t cf_tunnel.XXXXXX.log)"
cloudflared tunnel --url "http://localhost:$BACKEND_PORT" --no-autoupdate >"$LOG" 2>&1 &
TUNNEL_PID=$!

# Give it up to a minute to publish a hostname.
for _ in $(seq 1 30); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [[ -n "$URL" ]] && break
  sleep 2
done

if [[ -z "${URL:-}" ]]; then
  echo "ERROR: the tunnel never published a hostname. Log: $LOG" >&2
  kill "$TUNNEL_PID" 2>/dev/null || true
  exit 1
fi
echo "    $URL  (pid $TUNNEL_PID)"

# --- 3. Let the hosted origin talk to it ----------------------------------
say "Updating allowed CORS origins"
ENV_FILE="$ROOT/backend/.env"
ORIGINS="http://localhost:3000,https://${PROJECT}.web.app,https://${PROJECT}.firebaseapp.com"
if grep -q '^SENTINEL_CORS_ORIGINS=' "$ENV_FILE" 2>/dev/null; then
  # Portable in-place edit: GNU and BSD sed disagree about -i.
  tmp="$(mktemp)"
  sed "s|^SENTINEL_CORS_ORIGINS=.*|SENTINEL_CORS_ORIGINS=${ORIGINS}|" "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
else
  printf '\nSENTINEL_CORS_ORIGINS=%s\n' "$ORIGINS" >>"$ENV_FILE"
fi
echo "    restart the engine if these changed since it booted"

# --- 4. Rebuild the frontend against the new hostname ---------------------
say "Rebuilding the dashboard against $URL"
cd "$ROOT"
rm -rf .next out
NEXT_PUBLIC_SENTINEL_API_URL="$URL" npm run build

if ! grep -rq "$URL" out/_next/static/chunks/*.js 2>/dev/null; then
  echo "ERROR: the API URL did not make it into the bundle - refusing to deploy" >&2
  echo "A deploy now would publish a dashboard pointing at the old, dead tunnel." >&2
  kill "$TUNNEL_PID" 2>/dev/null || true
  exit 1
fi

# --- 5. Publish -----------------------------------------------------------
say "Deploying to Firebase Hosting"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$ROOT/backend/gcp-key.json}"
npx firebase deploy --only hosting --project "$PROJECT" --non-interactive

say "Published"
echo "  Dashboard : https://${PROJECT}.web.app"
echo "  Engine    : $URL"
echo
echo "Leave this terminal open - closing it kills the tunnel (pid $TUNNEL_PID)"
echo "and the dashboard will load but fail to reach the engine."
wait "$TUNNEL_PID"
