# SENTINEL agent fleet - the one image, run on Cloud Run, Hugging Face
# Spaces, or any other single-container host.
#
# Three things about this build are load-bearing and easy to get wrong:
#
# 1. Hunter is not a pure-Python agent. It shells out to the real `git` and
#    `npm audit --json` binaries to produce findings, so a slim Python image
#    alone would let the container start and then fail at the first scan.
#    Node and git are therefore runtime dependencies, not build-time ones.
#
# 2. The platform injects $PORT and terminates TLS in front of us. Binding
#    to a hardcoded port is the single most common reason a working local
#    image never passes a hosting platform's health check.
#
# 3. Investigations only run because a worker is polling the queue; the API
#    only enqueues jobs. A single-container host has one process to start,
#    so docker/start.sh runs both under it rather than assuming a second
#    container exists somewhere to run app.worker.

FROM python:3.12-slim AS builder

# Build-only toolchain: several deps (chromadb's ONNX runtime, grpc for
# Pub/Sub) ship sdists that need a compiler. Kept in a discarded stage so
# none of it reaches the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

# git + node are Hunter's actual scanning tools (see note 1 above).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY app ./app
COPY pytest.ini ./
COPY docker/start.sh ./docker/start.sh
RUN chmod +x ./docker/start.sh

# Cloud Run runs containers as an arbitrary non-root UID in some configs and
# the agent writes clones/evidence under workdir, so ownership has to be set
# before dropping privileges rather than relying on root.
RUN useradd --create-home --uid 1001 sentinel \
    && mkdir -p /app/workdir \
    && chown -R sentinel:sentinel /app
USER sentinel

ENV PORT=8080
EXPOSE 8080

# start.sh runs the worker in the background and execs uvicorn as PID 1.
# Investigations only ever run because a worker is polling the queue - the
# API just enqueues jobs - so on any single-container host (this image,
# wherever it's deployed) both have to live in the one process this
# container gets. It also materialises GOOGLE_APPLICATION_CREDENTIALS_JSON
# to a file when a host has no other way to deliver a file-shaped secret.
CMD ["./docker/start.sh"]
