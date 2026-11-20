# /// script
# requires-python = ">=3.11"
# dependencies = ["openai>=3.51,<4", "python-dotenv>=1.2.4", "linode_api4>=5.30", "requests", "boto3", "psycopg[binary]>=3.3"]
# ///
"""Autonomous Cloud Architect Agent.

Ask for a "secure data environment" and the model builds it across Akamai Cloud:
Compute (Linode), Cloud Firewall, VPC, Object Storage, and Managed PostgreSQL.

    uv run 06-architect-agent/agent.py
"""
import json
import os
import secrets
import sys
import time

import boto3
import psycopg
import requests
from botocore.config import Config
from dotenv import load_dotenv
from linode_api4 import VPC, Firewall, Instance, LinodeClient, ObjectStorageBucket, ObjectStorageEndpointType, ObjectStorageKeys
from openai import OpenAI

load_dotenv(override=True)
for name in ("AKAMAI_BASE_URL", "AKAMAI_API_KEY", "CHAT_MODEL", "LINODE_TOKEN", "PGHOST", "PGPASSWORD"):
    if not os.environ.get(name):
        sys.exit(f"{name} is not set. Add it to .env.")

llm = OpenAI(base_url=os.environ["AKAMAI_BASE_URL"], api_key=os.environ["AKAMAI_API_KEY"])
MODEL = os.environ["CHAT_MODEL"]
linode = LinodeClient(os.environ["LINODE_TOKEN"])

PREFIX = os.environ.get("RESOURCE_PREFIX", "sea-hackit-")  # scripts/cleanup.sh matches this prefix
STORAGE_REGION, STORAGE_CLUSTER = "us-ord", "us-ord-10"  # Chicago offers the E3 endpoint type
STORAGE_ENDPOINT = f"https://{STORAGE_CLUSTER}.linodeobjects.com"
MY_IP = requests.get("https://api.ipify.org", timeout=10).text.strip()


def db():
    return psycopg.connect(host=os.environ["PGHOST"], port=os.environ["PGPORT"], user=os.environ["PGUSER"],
                           password=os.environ["PGPASSWORD"], dbname=os.environ["PGDATABASE"],
                           sslmode=os.environ["PGSSLMODE"], connect_timeout=10, autocommit=True)


with db() as conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS environments (
        id SERIAL PRIMARY KEY, label TEXT, linode_id BIGINT, linode_ip TEXT, firewall_id BIGINT,
        vpc_id BIGINT, bucket TEXT, key_id BIGINT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT now())""")


# ---------- Tools. Each returns a string, so the model sees failures too. ----------

def create_vpc(region, label):
    """Compute networking: a private VPC with one subnet."""
    try:
        vpc = linode.vpcs.create(PREFIX + label + "-vpc", region, subnets=[{"label": "default", "ipv4": "10.0.0.0/24"}])
        return json.dumps({"vpc_id": vpc.id, "subnet_id": vpc.subnets[0].id, "subnet": "10.0.0.0/24"})
    except Exception as e:
        return f"create_vpc failed: {e}"


def create_linode(region, label, subnet_id=None):
    """1 GB Nanode, Ubuntu 24.04, random root password, optionally joined to a VPC subnet."""
    try:
        args = dict(label=PREFIX + label, root_pass=secrets.token_urlsafe(24))
        if subnet_id:
            args["interfaces"] = [{"purpose": "public"},
                                  {"purpose": "vpc", "subnet_id": int(subnet_id), "ipv4": {"vpc": "10.0.0.10"}}]
        inst = linode.linode.instance_create("g6-nanode-1", region, "linode/ubuntu24.04", **args)
        return json.dumps({"linode_id": inst.id, "public_ip": inst.ipv4[0], "region": region,
                           "vpc_ip": "10.0.0.10" if subnet_id else None})
    except Exception as e:
        return f"create_linode failed: {e}"


def create_and_attach_firewall(linode_id, my_ip, label="fw"):
    """Cloud Firewall: drop all inbound except SSH from my_ip/32 and HTTPS from anywhere."""
    try:
        rules = {
            "inbound_policy": "DROP", "outbound_policy": "ACCEPT", "outbound": [],
            "inbound": [
                {"action": "ACCEPT", "protocol": "TCP", "ports": "22", "label": "ssh-my-ip",
                 "addresses": {"ipv4": [f"{my_ip}/32"]}},
                {"action": "ACCEPT", "protocol": "TCP", "ports": "443", "label": "https-all",
                 "addresses": {"ipv4": ["0.0.0.0/0"], "ipv6": ["::/0"]}},
            ],
        }
        fw = linode.networking.firewall_create(PREFIX + label + "-fw", rules=rules, devices={"linodes": [int(linode_id)]})
        return json.dumps({"firewall_id": fw.id, "attached_to": int(linode_id),
                           "rules": f"SSH(22) only from {my_ip}/32, HTTPS(443) from anywhere, all else dropped"})
    except Exception as e:
        return f"create_and_attach_firewall failed: {e}"


def provision_object_storage(bucket_label):
    """Object Storage: a private bucket plus an access key limited to that bucket."""
    try:
        bucket = PREFIX + bucket_label
        linode.object_storage.bucket_create(STORAGE_REGION, bucket, endpoint_type=ObjectStorageEndpointType.E3)
        keys = linode.object_storage.keys_create(
            bucket + "-key",
            bucket_access=[{"region": STORAGE_REGION, "bucket_name": bucket, "permissions": "read_write"}])
        os.environ["_NEW_KEY"], os.environ["_NEW_SECRET"] = keys.access_key, keys.secret_key
        return json.dumps({"bucket": bucket, "bucket_url": f"https://{bucket}.{STORAGE_CLUSTER}.linodeobjects.com",
                           "key_id": keys.id, "access_key": keys.access_key,
                           "secret_key": "(held by the agent, not shown)"})
    except Exception as e:
        return f"provision_object_storage failed: {e}"


def upload_manifest(bucket, linode_id, linode_ip, firewall_id, vpc_id):
    """Writes manifest.json describing the stack into the new bucket, using the new bucket-scoped key."""
    try:
        s3 = boto3.client("s3", endpoint_url=STORAGE_ENDPOINT, aws_access_key_id=os.environ["_NEW_KEY"],
                          aws_secret_access_key=os.environ["_NEW_SECRET"], region_name=STORAGE_REGION,
                          config=Config(signature_version="s3v4", request_checksum_calculation="when_required"))
        body = json.dumps({"linode_id": linode_id, "linode_ip": linode_ip, "firewall_id": firewall_id,
                           "vpc_id": vpc_id, "bucket": bucket, "created": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2)
        for _ in range(6):  # a new key takes a few seconds to work
            try:
                s3.put_object(Bucket=bucket, Key="manifest.json", Body=body.encode())
                return f"uploaded manifest.json to {bucket}"
            except Exception:
                time.sleep(5)
        return "upload_manifest failed: key did not become active in time"
    except Exception as e:
        return f"upload_manifest failed: {e}"


def record_environment(label, linode_id, linode_ip, firewall_id, vpc_id, bucket, key_id):
    """Managed PostgreSQL: remember what was built."""
    try:
        with db() as conn:
            row = conn.execute(
                "INSERT INTO environments (label, linode_id, linode_ip, firewall_id, vpc_id, bucket, key_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (label, linode_id, linode_ip, firewall_id, vpc_id, bucket, key_id)).fetchone()
        return f"saved to PostgreSQL as environment #{row[0]}"
    except Exception as e:
        return f"record_environment failed: {e}"


def list_environments():
    """Managed PostgreSQL: everything built so far."""
    try:
        with db() as conn:
            rows = conn.execute("SELECT id, label, linode_id, linode_ip, firewall_id, vpc_id, bucket, status, "
                                "created_at::text FROM environments ORDER BY id").fetchall()
        keys = ["id", "label", "linode_id", "linode_ip", "firewall_id", "vpc_id", "bucket", "status", "created_at"]
        return json.dumps([dict(zip(keys, r)) for r in rows])
    except Exception as e:
        return f"list_environments failed: {e}"


def teardown(environment_id):
    """Deletes the Linode, firewall, VPC, bucket and key of an environment recorded in PostgreSQL."""
    try:
        with db() as conn:
            r = conn.execute("SELECT linode_id, firewall_id, vpc_id, bucket, key_id FROM environments WHERE id=%s",
                             (int(environment_id),)).fetchone()
        linode_id, firewall_id, vpc_id, bucket, key_id = r
        done = []
        for what, fn in (
            ("linode", lambda: linode.load(Instance, linode_id).delete()),
            ("firewall", lambda: linode.load(Firewall, firewall_id).delete()),
            ("bucket", lambda: delete_bucket(bucket)),
            ("key", lambda: linode.load(ObjectStorageKeys, key_id).delete()),
        ):
            try:
                fn(); done.append(what)
            except Exception as e:
                done.append(f"{what} (error: {e})")
        time.sleep(3)
        try:
            linode.load(VPC, vpc_id).delete(); done.append("vpc")
        except Exception as e:
            done.append(f"vpc (error: {e})")
        with db() as conn:
            conn.execute("UPDATE environments SET status='deleted' WHERE id=%s", (int(environment_id),))
        return "deleted: " + ", ".join(done)
    except Exception as e:
        return f"teardown failed: {e}"


def list_linodes():
    """Read-only: every Linode on the account."""
    try:
        return json.dumps([{"id": l.id, "label": l.label, "status": l.status, "region": l.region.id, "ip": l.ipv4[0]}
                           for l in linode.linode.instances()])
    except Exception as e:
        return f"list_linodes failed: {e}"


def list_firewalls():
    """Read-only: every Cloud Firewall on the account."""
    try:
        return json.dumps([{"id": f.id, "label": f.label, "status": f.status,
                            "inbound_rules": [f"{r.action} {r.protocol} {r.ports}" for r in f.rules.inbound],
                            "inbound_policy": f.rules.inbound_policy} for f in linode.networking.firewalls()])
    except Exception as e:
        return f"list_firewalls failed: {e}"


def list_buckets():
    """Read-only: every Object Storage bucket on the account."""
    try:
        return json.dumps([{"bucket": b.label, "region": b.region, "size_bytes": b.size}
                           for b in linode.object_storage.buckets()])
    except Exception as e:
        return f"list_buckets failed: {e}"


def delete_bucket(bucket):
    """A bucket must be empty to delete, so empty it with a temporary key limited to it."""
    tmp = linode.object_storage.keys_create(
        bucket + "-tmp", bucket_access=[{"region": STORAGE_REGION, "bucket_name": bucket, "permissions": "read_write"}])
    try:
        s3 = boto3.client("s3", endpoint_url=STORAGE_ENDPOINT, aws_access_key_id=tmp.access_key,
                          aws_secret_access_key=tmp.secret_key, region_name=STORAGE_REGION,
                          config=Config(signature_version="s3v4", request_checksum_calculation="when_required"))
        for attempt in range(6):  # the temporary key takes a few seconds to work
            try:
                for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=obj["Key"])
                break
            except Exception:
                time.sleep(5)
        linode.load(ObjectStorageBucket, bucket, STORAGE_REGION).delete()
    finally:
        tmp.delete()


FUNCS = {f.__name__: f for f in (create_vpc, create_linode, create_and_attach_firewall, provision_object_storage,
                                 upload_manifest, record_environment, list_environments, teardown,
                                 list_linodes, list_firewalls, list_buckets)}


def tool(name, description, props, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}}


S, I = {"type": "string"}, {"type": "integer"}
TOOLS = [
    tool("create_vpc", "Create a private VPC network with one subnet. Returns vpc_id and subnet_id.",
         {"region": S, "label": S}, ["region", "label"]),
    tool("create_linode", "Create a 1GB Ubuntu 24.04 Linode in a region, optionally in a VPC subnet. Returns linode_id and public_ip.",
         {"region": {"type": "string", "description": "Region id, Seattle is us-sea"}, "label": S, "subnet_id": I},
         ["region", "label"]),
    tool("create_and_attach_firewall", "Create a Cloud Firewall (SSH only from my_ip, HTTPS open) and attach it to a Linode.",
         {"linode_id": I, "my_ip": S, "label": S}, ["linode_id", "my_ip"]),
    tool("provision_object_storage", "Create a private Object Storage bucket in Chicago and an access key limited to it.",
         {"bucket_label": {"type": "string", "description": "lowercase letters, digits, dashes"}}, ["bucket_label"]),
    tool("upload_manifest", "Upload manifest.json describing the stack to the new bucket.",
         {"bucket": S, "linode_id": I, "linode_ip": S, "firewall_id": I, "vpc_id": I},
         ["bucket", "linode_id", "linode_ip", "firewall_id", "vpc_id"]),
    tool("record_environment", "Save the finished environment to the PostgreSQL database.",
         {"label": S, "linode_id": I, "linode_ip": S, "firewall_id": I, "vpc_id": I, "bucket": S, "key_id": I},
         ["label", "linode_id", "linode_ip", "firewall_id", "vpc_id", "bucket", "key_id"]),
    tool("list_environments", "List every environment built so far, from PostgreSQL.", {}, []),
    tool("list_linodes", "List all Linodes on the Akamai account (id, label, status, region, IP).", {}, []),
    tool("list_firewalls", "List all Cloud Firewalls on the account with their inbound rules.", {}, []),
    tool("list_buckets", "List all Object Storage buckets on the account.", {}, []),
    tool("teardown", "Delete all resources of a recorded environment by its environment id.",
         {"environment_id": I}, ["environment_id"]),
]

def make_system(ip):
    return f"""You are an Autonomous Cloud Architect for Akamai Cloud. You act through tools.
The user's public IP is {ip}. Use it as my_ip for firewalls without asking.
When asked for a "secure environment" (or data/scraping environment), call tools ONE AT A TIME, in this order,
passing real IDs from earlier results:
1. create_vpc (default region us-sea)  2. create_linode with the subnet_id (default region us-sea)
3. create_and_attach_firewall  4. provision_object_storage  5. upload_manifest
6. record_environment (use key_id from step 4)
Finally summarize what was built: Linode ID and IP, firewall rules, bucket URL, VPC, and the PostgreSQL record id.
For questions about the account use list_linodes, list_firewalls, list_buckets or list_environments.
Never print secret keys. Use a short lowercase label such as 'scraper'. Do not ask questions the defaults answer."""


SYSTEM = make_system(MY_IP)


def run_agent(messages, on_step=None):
    """Keep calling the model until it stops asking for tools."""
    while True:
        reply = llm.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, max_tokens=800,
                                            extra_body={"chat_template_kwargs": {"enable_thinking": False}}).choices[0].message
        messages.append(reply)
        if not reply.tool_calls:
            return reply.content
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            print(f"  -> {call.function.name}({args})")
            if on_step:
                on_step({"tool": call.function.name, "args": args, "status": "running"})
            result = FUNCS[call.function.name](**args)
            print(f"     {result[:200]}")
            if on_step:
                on_step({"tool": call.function.name, "args": args, "status": "failed" if "failed" in result[:60] else "done",
                         "result": result[:300]})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


if __name__ == "__main__":
    messages = [{"role": "system", "content": SYSTEM}]
    print(f"Cloud Architect ready. Your IP: {MY_IP}. Try: I need a secure data scraping environment in Seattle.")
    while True:
        text = input("\nyou> ").strip()
        if text in ("exit", "quit"):
            break
        messages.append({"role": "user", "content": text})
        print("\nagent>", run_agent(messages))
