# Autonomous Cloud Architect Agent

> Type what you need in plain English on cloud.linode.com. An AI agent builds it across Akamai Cloud, and you watch each step happen.

**Example:** "I need a secure data scraping environment in Seattle." The agent creates a private network, a Linode, a locked-down firewall, a storage bucket with its own key, and a database record, then reports back.

## How it works

```text
 cloud.linode.com
 +---------------------+       +-----------------------------+
 | Chrome extension    | ----> | FastAPI backend (port 8000) |
 | chat + activity     |       | runs on your Linode         |
 +---------------------+       +--------------+--------------+
                                              |
        +-------------------+-----------------+------------------+
        |                   |                 |                  |
   Akamai Inference     Linode API      Object Storage     PostgreSQL
   (plans the steps)   (compute, VPC,   (audit archive,    (environments,
                         firewall)       manifests)         chat log)
```

The backend code is in [server.py](06-architect-agent/server.py) and the agent's tools are in [agent.py](06-architect-agent/agent.py).

## Services

| Service | What it does here |
|---|---|
| **Inference** (`qwen3-8-27b`) | Plans the steps and chains eight tool calls, passing real IDs from one step to the next |
| **Compute** | Runs the backend 24/7 as a systemd service, and creates a 1 GB Ubuntu 24.04 Linode in Seattle for each environment |
| **VPC** | Each new Linode joins a private subnet created for it |
| **Cloud Firewall** | Default-drop firewall per environment: SSH only from the requester's own IP, HTTPS from anywhere |
| **Object Storage** | Private bucket and a bucket-limited key per environment with a `manifest.json`, plus an append-only archive of every chat turn |
| **Managed PostgreSQL** | The environment registry that teardown and "what have I built?" depend on, plus the chat audit log |

## Demo

1. Open cloud.linode.com. A small ☁ icon with a hint appears. Click it.
2. Type "I need a secure data scraping environment in Seattle."
3. Watch the tool steps in **Chat** and Akamai's own events in **Activity**, while the Linode, firewall, VPC and bucket appear in Cloud Manager.
4. Ask "What have I built so far?" It is answered from PostgreSQL.
5. Open a JSON object in the audit bucket.
6. Say "Tear down environment N" and the resources disappear.

## Submission summary

**User and task.** A developer or small team that needs a locked-down environment (server, network rules, storage, record keeping) and does not want to click through five Cloud Manager pages.

**Benefit.** One sentence builds a firewalled, private-network environment in under a minute. Every step is recorded for audit, and "what have I built?" and teardown work from the same record, so nothing is left billing.

**Inference.** `qwen3-8-27b`, the chat and tool-calling model the event key allows. Checked by running a full build and teardown through the extension backend, asking for something the agent has no tool for (a Kubernetes cluster: it said so and offered alternatives), and asking to delete an environment that does not exist (it looked it up and reported none).

**Technical choice.** Access and reliability. API keys stay on the backend, so the extension holds only a shared key. Tools return errors as text so the model can recover. Teardown reads the registry in PostgreSQL instead of trusting the model to remember IDs.

**Our idea.** The agent lives inside the console where the work happens, and the Activity tab shows Akamai's own event history (done, in progress, failed) next to the chat.

**Known limits.**
- The backend uses plain HTTP and one shared key.
- Regions are fixed: Linode in Seattle, buckets in Chicago.
- The VPC address is hardcoded, so there is one environment per VPC.
- The small model can pick odd labels.
- There is no login or per-user permission.
- Retention rules for the audit logs are not set.

## Setup

1. Create a token in Cloud Manager (**API Tokens**): Read/Write for Linodes, Firewalls, VPCs, Databases, Object Storage, and Read Only for Account. Put it in `.env` as `LINODE_TOKEN`.
2. Do [Lab 03](03-database/README.md) so the `PG` lines are filled in. Add the IP of the machine that runs the agent to the database's allow list.
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

A small hint bubble sits next to a floating ☁ icon on every cloud.linode.com page. Click it to open the chat panel. The **Chat** tab runs the agent and shows each tool call as running, done or failed. The **Activity** tab shows the account's event history, refreshed every few seconds, with each item marked done, in progress (with percent) or failed. Questions like "what linodes and firewalls do I have?" are answered from the account, read-only.

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
3. Copy `06-architect-agent/extension/config.example.js` to `06-architect-agent/extension/config.js` and fill in the backend URL and key.
4. In Chrome open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose the `06-architect-agent/extension` folder. Open https://cloud.linode.com.

The backend uses plain HTTP and a shared key, which is fine for a demo. Put it behind HTTPS before real use.

## Audit trail

Every chat turn is recorded by [server.py](06-architect-agent/server.py), for reference and compliance:

- **PostgreSQL** table `chat_log`: session, role, message, tool calls with arguments and results (`steps`), client IP, model and timestamp. The backend also rebuilds a session's context from it after a restart.
- **Object Storage** bucket `OBJ_BUCKET` (`sea-hackit-audit`, Chicago): one JSON object per message at `chat/<date>/<session>/<time>-<id>-<role>.json`. The key in `.env` is limited to that bucket.
- `GET /audit/<session>` (with the `X-API-Key` header) returns one session's full record.

Secret keys are never shown to the model or logged, but anything a user types is stored as typed. Set a retention rule on the bucket and the table to suit your policy. The audit bucket is separate from the environment buckets, so teardown does not delete it, and it still bills with Object Storage.

## Protected resources

At startup the agent works out what it depends on: the Linode it runs on (matched by its public IP, or `PROTECTED_LINODE_IDS` in `.env`), that Linode's firewalls, the PostgreSQL cluster it connects to, and the audit bucket `OBJ_BUCKET`. For those it will:

- **warn and delete nothing** when a teardown includes any of them. Deleting is only allowed if the user types exactly `CONFIRM DELETE PROTECTED`, and the model's own say-so is not enough;
- refuse to attach a new firewall to the host Linode, to create a bucket with the audit bucket's name, or to register protected resources as an environment, which would let a later teardown delete them.

Other Linodes, firewalls and buckets, such as the ones the agent builds, are deleted without extra confirmation. The guard covers the agent's own tools only. Cloud Manager and `scripts/cleanup.sh` are not affected.

## Cost and cleanup

Linode $0.18 a day, Object Storage $5 a month for the account. Firewalls and VPCs are free. Run the teardown prompt, or `bash scripts/cleanup.sh sea-hackit- --dry-run` and then without `--dry-run`. See [cleanup](docs/cleanup.md).

## Repository layout

| Path | What it is |
|---|---|
| [06-architect-agent/](06-architect-agent/) | The agent ([agent.py](06-architect-agent/agent.py)), the HTTP backend ([server.py](06-architect-agent/server.py)), the systemd unit and the Chrome extension |
| [WORKSHOP.md](WORKSHOP.md), `00-setup` to `05-lke`, `demo/`, `docs/` | The Akamai Seattle workshop material this project started from: setup, labs, the demo app and the [judging rules](docs/judging.md) |
