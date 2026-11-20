# Autonomous Cloud Architect Agent

Ask for an environment in plain English. The model calls tools that build it across Akamai Cloud.

| Service | What the agent does with it |
|---|---|
| Inference (`CHAT_MODEL`) | Plans the steps and chains tool calls |
| Compute | Creates a 1 GB Ubuntu 24.04 Linode in Seattle |
| VPC | Creates a private network and puts the Linode in it |
| Cloud Firewall | SSH only from your IP, HTTPS open, everything else dropped |
| Object Storage | Creates a private bucket and a key limited to it, then uploads `manifest.json` |
| Managed PostgreSQL | Records each environment, so "what have I built?" is answered from the database |

## Setup

1. Create a token in Cloud Manager (**API Tokens**): Read/Write for Linodes, Firewalls, VPCs, Databases, Object Storage, and Read Only for Account. Put it in `.env` as `LINODE_TOKEN`.
2. Do [Lab 03](../03-database/README.md) so the `PG` lines are filled in. Add the IP of the machine that runs the agent to the database's allow list.
3. Resources are labeled `sea-hackit-`. Set `RESOURCE_PREFIX` in `.env` to change it.

## Run

```bash
uv run 06-architect-agent/agent.py
```

Demo prompts, with Cloud Manager open beside the terminal:

1. `I need a secure data scraping environment in Seattle.`
2. `What have I built so far?`
3. `Tear down environment 1.`

## Chrome extension on cloud.linode.com

A chat panel pops up on every cloud.linode.com page. The **Chat** tab runs the agent and shows each tool call as running, done or failed. The **Activity** tab shows the account's event history, refreshed every few seconds, with each item marked done, in progress (with percent) or failed. Questions like "what linodes and firewalls do I have?" are answered from the account, read-only.

```text
Chrome extension  ->  FastAPI backend on the Linode (server.py, port 8000)  ->  Inference, Linode API, Object Storage, PostgreSQL
```

Secrets stay on the backend. The extension only holds `AGENT_API_KEY`.

1. Set `AGENT_API_KEY` in `.env` to any long random string.
2. Deploy the backend. On the Linode, install `uv`, then copy `agent.py`, `server.py` and `.env` to `/root/app` and install `architect.service`:
   ```bash
   ssh root@LINODE_IP 'mkdir -p /root/app/06-architect-agent'
   scp 06-architect-agent/agent.py 06-architect-agent/server.py root@LINODE_IP:/root/app/06-architect-agent/
   scp .env root@LINODE_IP:/root/app/.env
   scp 06-architect-agent/architect.service root@LINODE_IP:/etc/systemd/system/
   ssh root@LINODE_IP 'systemctl daemon-reload && systemctl enable --now architect'
   ```
   Port 8000 must be reachable, and the Linode's IP must be on the PostgreSQL allow list.
3. Copy `extension/config.example.js` to `extension/config.js` and fill in the backend URL and key.
4. In Chrome open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose the `extension` folder. Open https://cloud.linode.com.

The backend uses plain HTTP and a shared key, which is fine for a demo. Put it behind HTTPS before real use.

## Audit trail

Every chat turn is recorded by `server.py`, for reference and compliance:

- **PostgreSQL** table `chat_log`: session, role, message, tool calls with arguments and results (`steps`), client IP, model and timestamp. The backend also rebuilds a session's context from it after a restart.
- **Object Storage** bucket `OBJ_BUCKET` (`sea-hackit-audit`, Chicago): one JSON object per message at `chat/<date>/<session>/<time>-<id>-<role>.json`. The key in `.env` is limited to that bucket.
- `GET /audit/<session>` (with the `X-API-Key` header) returns one session's full record.

Secret keys are never shown to the model or logged, but anything a user types is stored as typed. Set a retention rule on the bucket and the table to suit your policy. The audit bucket is separate from the environment buckets, so teardown does not delete it, and it still bills with Object Storage.

## Cost and cleanup

Linode $0.18 a day, Object Storage $5 a month for the account. Firewalls and VPCs are free. Run the teardown prompt, or `bash scripts/cleanup.sh sea-hackit- --dry-run` and then without `--dry-run`. See [cleanup](../docs/cleanup.md).
