# Deploying AlphaDesk to Oracle Cloud Always Free

Target: one always-free ARM VM runs the whole engine; Neo4j Aura Free holds the
graph; your laptop is just a browser tab. Total standing cost: $0 beyond the
Claude Max subscription.

## 1. Accounts (one-time, ~45 min)

**Neo4j Aura Free** — console.neo4j.io → New instance → Free.
Save the connection URI (`neo4j+s://xxxx.databases.neo4j.io`) and the generated
password. Caveat: free instances pause after ~3 days idle — the engine's writes
keep it warm; if paused, resume in the console (the app no-ops harmlessly).

**Claude token** — on your Mac (where you're logged into Claude):
```
claude setup-token
```
Browser OAuth → copy the long-lived token (valid ~1 year — calendar a renewal).
This is `CLAUDE_CODE_OAUTH_TOKEN`; it bills the VM's LLM calls to your Max plan.

**Oracle Cloud** — signup at oracle.com/cloud/free (card required; stays $0).
Create instance:
- Shape: **VM.Standard.A1.Flex — 4 OCPU / 24 GB** (the full free allotment)
- Image: **Ubuntu 24.04 (aarch64)**
- Add your SSH public key
- Known friction: A1 free capacity is often exhausted — retry at off-peak
  hours or another availability domain.

Networking: VCN → default Security List → Add Ingress Rule:
source `0.0.0.0/0`, protocol TCP, destination port `8000`.

## 2. Deploy (~15 min)

```bash
ssh ubuntu@<VM_PUBLIC_IP>
git clone <your repo url> Stock-trading-agent
cd Stock-trading-agent
bash alphadesk/deploy/setup.sh     # venv, deps, CLI smoke-check, systemd, firewall
nano .env                          # paste ALL secrets (template pre-copied)
sudo systemctl start alphadesk
journalctl -u alphadesk -f         # watch: "scheduler up — session=..."
```

Optional warm start (recommended): backfill the graph before/while it runs:
```bash
PYTHONPATH=. ./.venv/bin/python -m alphadesk.main backfill --hours 72
```

## 3. Verify

- `curl -i http://<IP>:8000/api/stats` → **401** (auth enforced)
- with `-u ADMIN_USERNAME:ADMIN_PASSWORD` → JSON
- Dashboard in browser → decisions table; `/api/graph` counts rising within
  ~15 min (Aura console shows the same nodes)
- `journalctl -u alphadesk -f` → triage windows firing; no CRITICAL lines
- Reboot test: `sudo reboot`, wait 2 min → service auto-returns (systemd)

## 4. Operations

| Task | Command |
|---|---|
| Logs | `journalctl -u alphadesk -f` |
| Restart | `sudo systemctl restart alphadesk` (cooldowns reseed from ledger) |
| Stop | `sudo systemctl stop alphadesk` |
| Update code | `git pull && sudo systemctl restart alphadesk` |
| Ad-hoc desk run | `PYTHONPATH=. ./.venv/bin/python -m alphadesk.main desk` |
| Status | `PYTHONPATH=. ./.venv/bin/python -m alphadesk.main status` |

State lives in `~/.alphadesk/` (ledger.db, universe cache) — back it up if you
ever rebuild the VM. The graph is safe in Aura regardless.

## Notes

- **Do NOT set `ANTHROPIC_API_KEY`** on the VM — it would override subscription
  billing.
- The dashboard is Basic Auth over plain HTTP — fine for a personal dashboard;
  add Caddy/TLS later if it bothers you.
- The token expires in ~1 year; the engine's LLM calls will start failing safe
  (blocked decisions) when it does — regenerate with `claude setup-token`.
