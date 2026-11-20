# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "openai>=3.51,<4", "python-dotenv>=1.2.4", "linode_api4>=5.30",
#                 "requests", "boto3", "psycopg[binary]>=3.3"]
# ///
"""HTTP backend for the Chrome extension. It wraps agent.py, so the keys never leave the server.

    AGENT_API_KEY=... uv run 06-architect-agent/server.py      then open http://localhost:8000/health
"""
import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402  (loads .env and connects to PostgreSQL)
import uvicorn  # noqa: E402
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
    messages = sessions.setdefault(body.session, [{"role": "system", "content": agent.make_system(client_ip(request))}])
    messages.append({"role": "user", "content": body.message})
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
        job["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    return jobs[job_id]


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
