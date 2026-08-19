FROM python:3.12-slim

WORKDIR /app

# System deps + CockroachDB Cloud CA cert (for sslmode=verify-full)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Download the cluster CA cert at build (public endpoint); path matches DATABASE_URL sslrootcert.
ARG CRDB_CLUSTER_ID
RUN mkdir -p /root/.postgresql && \
    if [ -n "$CRDB_CLUSTER_ID" ]; then \
      curl -s -o /root/.postgresql/root.crt \
      "https://cockroachlabs.cloud/clusters/${CRDB_CLUSTER_ID}/cert" || true ; \
    fi

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
