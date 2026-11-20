# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "openai>=3.51,<4", "python-dotenv>=1.2.4", "linode_api4>=5.30",
#                 "requests", "boto3", "psycopg[binary]>=3.3"]
# ///
"""HTTP backend for the Chrome extension. It wraps agent.py, so the keys never leave the server.

    AGENT_API_KEY=... uv run 06-architect-agent/server.py      then open http://localhost:8000/health
"""
import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402  (loads .env and connects to PostgreSQL)
import boto3  # noqa: E402
import uvicorn  # noqa: E402
from botocore.config import Config  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

API_KEY = os.environ.get("AGENT_API_KEY")
if not API_KEY:
    sys.exit("AGENT_API_KEY is not set. Add it to .env.")

app = FastAPI(title="Cloud Architect Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

sessions: dict[str, list] = {}  # chat history per browser session
jobs: dict[str, dict] = {}  # one job per message, polled by the extension


# ---------- Audit trail: PostgreSQL for queries, Object Storage for an append-only archive ----------
with agent.db() as conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_log (
        id SERIAL PRIMARY KEY, session TEXT, role TEXT, content TEXT, steps JSONB, client_ip TEXT,
        model TEXT, created_at TIMESTAMPTZ DEFAULT now())""")

archive = None
if os.environ.get("OBJ_BUCKET") and os.environ.get("OBJ_ACCESS_KEY"):
    archive = boto3.client("s3", endpoint_url=os.environ["OBJ_ENDPOINT"], aws_access_key_id=os.environ["OBJ_ACCESS_KEY"],
                           aws_secret_access_key=os.environ["OBJ_SECRET_KEY"], region_name="us-ord",
                           config=Config(signature_version="s3v4", request_checksum_calculation="when_required"))


def record(session, role, content, ip, steps=None):
    """Writes one chat turn to PostgreSQL and archives it as its own JSON object in the bucket."""
    try:
        with agent.db() as conn:
            conn.execute("INSERT INTO chat_log (session, role, content, steps, client_ip, model) VALUES (%s,%s,%s,%s,%s,%s)",
                         (session, role, content, json.dumps(steps) if steps is not None else None, ip, agent.MODEL))
        if archive:
            now = time.gmtime()
            key = f"chat/{time.strftime('%Y-%m-%d', now)}/{session}/{time.strftime('%H%M%S', now)}-{uuid.uuid4().hex[:6]}-{role}.json"
            body = {"session": session, "role": role, "content": content, "steps": steps, "client_ip": ip,
                    "model": agent.MODEL, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", now)}
            archive.put_object(Bucket=os.environ["OBJ_BUCKET"], Key=key, Body=json.dumps(body, indent=2).encode(),
                               ContentType="application/json")
    except Exception as e:  # logging must never break the chat
        print("audit log failed:", e)


def restore(session, ip):
    """After a backend restart, rebuild the conversation the model sees from PostgreSQL."""
    messages = [{"role": "system", "content": agent.make_system(ip)}]
    with agent.db() as conn:
        rows = conn.execute("SELECT role, content FROM chat_log WHERE session=%s ORDER BY id", (session,)).fetchall()
    for role, content in rows:
        if content:
            messages.append({"role": role, "content": content})
    return messages


def auth(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(401, "bad API key")


class ChatIn(BaseModel):
    session: str
    message: str


def client_ip(request: Request):
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host


@app.get("/health")
def health():
    return {"ok": True, "model": agent.MODEL}


@app.post("/chat", dependencies=[Depends(auth)])
def chat(body: ChatIn, request: Request):
    """Starts the agent in a thread and returns a job id. The extension polls /jobs/{id}."""
    ip = client_ip(request)
    if body.session not in sessions:
        sessions[body.session] = restore(body.session, ip)
    messages = sessions[body.session]
    messages.append({"role": "user", "content": body.message})
    record(body.session, "user", body.message, ip)
    job = {"steps": [], "reply": None, "done": False, "error": None}
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = job

    def on_step(step):
        if step["status"] == "running":
            job["steps"].append(step)
        else:  # replace the running entry of the same tool call with its outcome
            for i in range(len(job["steps"]) - 1, -1, -1):
                if job["steps"][i]["tool"] == step["tool"] and job["steps"][i]["status"] == "running":
                    job["steps"][i] = step
                    break

    def work():
        try:
            job["reply"] = agent.run_agent(messages, on_step)
        except Exception as e:
            job["error"] = str(e)
        record(body.session, "assistant", job["reply"] or f"Error: {job['error']}", ip, job["steps"])
        job["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    return jobs[job_id]


@app.get("/audit/{session_id}", dependencies=[Depends(auth)])
def audit(session_id: str):
    """Full recorded conversation of one session, including tool calls and results."""
    with agent.db() as conn:
        rows = conn.execute("SELECT created_at::text, role, content, steps, client_ip FROM chat_log WHERE session=%s ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(zip(["time", "role", "content", "steps", "client_ip"], r)) for r in rows]


@app.get("/events", dependencies=[Depends(auth)])
def events():
    """Akamai account event history, newest first. status: scheduled/started = in progress, finished = done, failed."""
    data = agent.linode.get("/account/events?page_size=25", filters={"+order_by": "created", "+order": "desc"})
    out = []
    for e in data.get("data", []):
        status = {"finished": "done", "failed": "failed", "started": "in progress", "scheduled": "in progress",
        "notification": "done"}.get(
            e.get("status"), e.get("status") or "info")
        out.append({"id": e["id"], "action": e["action"], "status": status, "percent": e.get("percent_complete"),
                    "entity": (e.get("entity") or {}).get("label"), "created": e["created"],
                    "message": e.get("message"), "user": e.get("username")})
    return out


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
