#!/bin/sh
# Container entrypoint: runs the worker and the API in one process group.
#
# Two things this exists to solve:
#
# 1. Investigations never run without a worker polling the queue - the API
#    only enqueues jobs. Every prior deploy target (local dev, Cloud Run)
#    ran api.server and app.worker as two separate processes; a
#    single-container host (Hugging Face Spaces, Render, Fly.io) gives you
#    one process to start, so both have to live under it. The worker is
#    backgrounded and the API is exec'd as PID 1, so the platform's
#    stop/restart signals still reach the right process.
#
# 2. Some hosts have no mechanism for a file-shaped secret - only an
#    environment variable. GOOGLE_APPLICATION_CREDENTIALS_JSON carries the
#    service account key's *content*; if it's set and no credentials file
#    already exists at the target path, it's written out before either
#    process starts. Nothing downstream needs to know this happened -
#    app/config.py's normal GOOGLE_APPLICATION_CREDENTIALS handling takes
#    over from here.
set -eu

CREDS_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-/app/gcp-key.json}"
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS_JSON:-}" ] && [ ! -f "$CREDS_PATH" ]; then
    printf '%s' "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > "$CREDS_PATH"
    export GOOGLE_APPLICATION_CREDENTIALS="$CREDS_PATH"
fi

python -m app.worker &
WORKER_PID=$!
trap 'kill "$WORKER_PID" 2>/dev/null || true' TERM INT

exec uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-8080}"
