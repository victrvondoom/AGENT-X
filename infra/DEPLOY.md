# Deploying Agent X on AWS

Agent X runs as a single container. The demo stack: **EC2** (app) + **S3** (object-locked erasure
certificates) + **Lambda** (optional certificate signer). CockroachDB Basic is external.

## 1. IAM
Create an IAM user with programmatic access and attach [`iam-policy.json`](iam-policy.json)
(least-privilege: the certificate bucket only). Put the keys in the app's environment.

## 2. S3 bucket (Object Lock / WORM)
Object Lock must be enabled at creation:
```bash
aws s3api create-bucket --bucket agent-x-erasure-certs --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1 --object-lock-enabled-for-bucket
aws s3api put-object-lock-configuration --bucket agent-x-erasure-certs \
  --object-lock-configuration '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE","Days":3650}}}'
```

## 3. Build + run the container
```bash
docker build --build-arg CRDB_CLUSTER_ID=<cluster-id> -t agent-x .
docker run -p 8080:8080 --env-file .env agent-x
```

## 4. Host it (choose one)
- **EC2** (t3.small within free credits): install Docker, run the container, open port 8080/443.
- Any container host that reads the same `.env`.

Set a **billing alarm** regardless of host. In production the LLM should be a hosted provider
(Cerebras/Gemini) via `.env`, not local Ollama.

## Environment
See [`.env.example`](../.env.example). Required for deploy: `DATABASE_URL`, an LLM provider +
key, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`, `S3_CERT_BUCKET`,
`AGENT_X_ROOT_KEY`, `AGENT_X_SIGNING_KEY`.
