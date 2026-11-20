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

## Cost and cleanup

Linode $0.18 a day, Object Storage $5 a month for the account. Firewalls and VPCs are free. Run the teardown prompt, or `bash scripts/cleanup.sh sea-hackit- --dry-run` and then without `--dry-run`. See [cleanup](../docs/cleanup.md).
