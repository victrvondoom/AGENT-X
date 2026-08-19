# Deploy Agent X on AWS EC2 (free-tier, stays live for the judging month)

One `t3.micro` (or `t2.micro`) running the app as an auto-restarting `systemd` service. Cost is
covered by your account credits / free tier. The database (CockroachDB Cloud) and certificate store
(S3) are already provisioned — EC2 just hosts the app.

## 0. Prerequisites (once)
- Repo is **public** (Settings → General → Danger Zone → Change visibility → Public).
- You have your working local `.env` and the CockroachDB CA cert at `~/.postgresql/root.crt`.

## 1. Launch the instance
AWS Console → EC2 → **Launch instance**:
- **Region:** `ap-south-1` (Mumbai) — same region as the CockroachDB cluster, lowest latency.
  (Any region works; cross-region just adds a little DB latency.)
- **AMI:** Ubuntu Server 24.04 LTS (64-bit x86).
- **Type:** `t3.micro` (or `t2.micro`) — free-tier eligible.
- **Key pair:** create one (e.g. `agent-x-key`) and download `agent-x-key.pem`.
- **Security group** — add inbound rules:
  - SSH — TCP **22** — Source: *My IP*
  - Custom TCP — **8080** — Source: *Anywhere (0.0.0.0/0)*  ← so judges can reach the app
- Launch. Note the **Public IPv4 DNS** (e.g. `ec2-13-233-x-x.ap-south-1.compute.amazonaws.com`).

## 2. Copy your secrets up (from your laptop)
```bash
cd /path/to/your/agent-x          # your local clone (where .env already works)
chmod 400 ~/Downloads/agent-x-key.pem
DNS=ec2-XX-XX-XX-XX.ap-south-1.compute.amazonaws.com     # your Public IPv4 DNS
scp -i ~/Downloads/agent-x-key.pem .env                ubuntu@$DNS:~/.env
scp -i ~/Downloads/agent-x-key.pem ~/.postgresql/root.crt ubuntu@$DNS:~/root.crt
```

## 3. Provision (on the server)
```bash
ssh -i ~/Downloads/agent-x-key.pem ubuntu@$DNS
git clone <YOUR-REPO-URL> ~/agent-x      # your own clone URL (personal, org, or fork)
cd ~/agent-x && bash deploy/setup.sh
```
`setup.sh` is self-locating — no repo URL is baked in, so it runs against whatever checkout you cloned.
It installs deps with `uv`, adds swap, wires `~/.env` + `~/root.crt`, initializes the DB, and starts the
`agent-x` systemd service on port 8080 (auto-restart + boot-persistent). It prints the live URL.

## 4. Verify
Open **`http://<PUBLIC_IP>:8080/app`** — you should see the console with the seeded graph.
Also seed the demo data if the DB is empty:
```bash
cd ~/agent-x && uv run scripts/seed_demo.py
```

## Operations
```bash
sudo systemctl status agent-x      # health
sudo journalctl -u agent-x -f      # live logs
sudo systemctl restart agent-x     # restart
cd ~/agent-x && git pull && sudo systemctl restart agent-x   # deploy an update
```

## Notes
- The service is `Restart=always` and `enable`d, so it comes back after a crash or reboot — it will
  stay up for the whole judging window with no babysitting.
- Optional (nicer demo URL): put Caddy or Nginx in front for `:80`/HTTPS, or an Elastic IP so the
  address never changes. Not required — the `:8080` URL is judge-reachable as-is.
- First page load is slightly slow while `fastembed` downloads its 384-d model (~130 MB), then it's fast.
