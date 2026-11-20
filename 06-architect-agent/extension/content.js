// Floating chat + activity panel injected into cloud.linode.com.
(() => {
  if (document.getElementById("akamai-architect-host")) return;

  // Saved in the extension's storage, so a reload or a new tab gets the same conversation back.
  const newSession = () => Math.random().toString(36).slice(2);
  let state = { session: newSession(), history: [], pending: null }; // pending: id of a job still running
  const save = () => chrome.storage.local.set({ architect: state });

  const send = (msg) => new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const host = document.createElement("div");
  host.id = "akamai-architect-host";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });

  root.innerHTML = `
  <style>
    * { box-sizing: border-box; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
    .fab { position: fixed; right: 20px; bottom: 20px; width: 56px; height: 56px; border-radius: 50%; border: 0;
      background: #0b6bcb; color: #fff; font-size: 26px; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.3); z-index: 2147483647; }
    .tip { position: fixed; right: 88px; bottom: 26px; max-width: 230px; padding: 10px 28px 10px 12px; background: #fff; color: #1a1f27;
      border-radius: 12px; font-size: 13px; line-height: 1.4; box-shadow: 0 4px 16px rgba(0,0,0,.25); z-index: 2147483647; cursor: pointer; }
    .tip::after { content: ""; position: absolute; right: -7px; bottom: 16px; border: 7px solid transparent; border-left-color: #fff; border-right: 0; }
    .tip .x { position: absolute; top: 4px; right: 9px; color: #7a8594; font-size: 14px; }
    .tip.hide { display: none; }
    .panel { position: fixed; right: 20px; bottom: 88px; width: 400px; height: 580px; background: #fff; color: #1a1f27;
      border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,.35); display: none; flex-direction: column; overflow: hidden; z-index: 2147483647; }
    .panel.open { display: flex; }
    header { background: #0b6bcb; color: #fff; padding: 12px 14px; font-weight: 600; display: flex; justify-content: space-between; }
    header small { font-weight: 400; opacity: .85; }
    .tabs { display: flex; border-bottom: 1px solid #dde3ea; }
    .tabs button { flex: 1; padding: 10px; border: 0; background: none; cursor: pointer; font-size: 14px; color: #5b6673; }
    .tabs button.on { color: #0b6bcb; border-bottom: 2px solid #0b6bcb; font-weight: 600; }
    .view { flex: 1; overflow-y: auto; padding: 12px; display: none; }
    .view.on { display: block; }
    .msg { margin: 8px 0; padding: 9px 12px; border-radius: 10px; font-size: 14px; line-height: 1.45; max-width: 92%; word-wrap: break-word; }
    .user { background: #0b6bcb; color: #fff; margin-left: auto; }
    .bot { background: #eef2f7; }
    .bot table { border-collapse: collapse; font-size: 12px; margin: 6px 0; }
    .bot th, .bot td { border: 1px solid #ccd4de; padding: 3px 6px; }
    .step { font-size: 12.5px; margin: 4px 0; padding: 6px 10px; border-radius: 8px; background: #f7f9fb; border: 1px solid #e3e8ee; }
    .step.running { border-color: #f5b301; } .step.done { border-color: #2e9e5b; } .step.failed { border-color: #d64545; }
    .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
    .running .dot, .inprogress .dot { background: #f5b301; animation: pulse 1s infinite; }
    .done .dot { background: #2e9e5b; } .failed .dot { background: #d64545; }
    @keyframes pulse { 50% { opacity: .3; } }
    .ev { font-size: 13px; padding: 8px 4px; border-bottom: 1px solid #edf0f4; }
    .ev small { color: #7a8594; display: block; margin-top: 2px; }
    form { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #dde3ea; }
    input { flex: 1; padding: 9px 11px; border: 1px solid #ccd4de; border-radius: 8px; font-size: 14px; }
    form button { padding: 0 14px; border: 0; border-radius: 8px; background: #0b6bcb; color: #fff; cursor: pointer; }
    .hint { color: #7a8594; font-size: 12.5px; }
    .chip { display: inline-block; margin: 3px 3px 0 0; padding: 4px 9px; border-radius: 12px; background: #e4effc; color: #0b5cad; cursor: pointer; font-size: 12px; }
  </style>
  <button class="fab" title="Cloud Architect">&#9729;</button>
  <div class="tip"><b>AI Cloud Architect</b><br>Ask me in plain English to build, check or tear down your Akamai infrastructure.<span class="x">&#10005;</span></div>
  <div class="panel">
    <header><span>Cloud Architect <small>Akamai Inference + Cloud</small></span><span><span class="new" title="Start a new chat" style="cursor:pointer;margin-right:12px;font-weight:400">New chat</span><span class="close" style="cursor:pointer">&#10005;</span></span></header>
    <div class="tabs"><button class="on" data-tab="chat">Chat</button><button data-tab="activity">Activity <span class="badge"></span></button></div>
    <div class="view on" id="chat">
      <div class="hint">Describe what you need. Try:<br>
        <span class="chip">I need a secure data scraping environment in Seattle</span>
        <span class="chip">What linodes and firewalls do I have?</span>
        <span class="chip">What have I built so far?</span></div>
    </div>
    <div class="view" id="activity"><div class="hint">Loading account events...</div></div>
    <form><input placeholder="Ask in plain English..." autocomplete="off"><button>Send</button></form>
  </div>`;

  const $ = (s) => root.querySelector(s);
  const panel = $(".panel"), chat = $("#chat"), activity = $("#activity"), input = $("input");
  let busy = false;

  const tip = $(".tip");
  const setOpen = (open) => {
    panel.classList.toggle("open", open);
    if (open) tip.classList.add("hide"); // the hint is only for people who have not opened the panel
  };
  $(".fab").onclick = () => setOpen(!panel.classList.contains("open"));
  $(".close").onclick = () => setOpen(false);
  tip.onclick = (e) => {
    if (e.target.classList.contains("x")) tip.classList.add("hide");
    else setOpen(true);
  };
  root.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
    root.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    chat.classList.toggle("on", b.dataset.tab === "chat");
    activity.classList.toggle("on", b.dataset.tab === "activity");
    if (b.dataset.tab === "activity") loadEvents();
  }));
  const hintHTML = chat.innerHTML;
  chat.addEventListener("click", (e) => {
    if (e.target.classList.contains("chip")) { input.value = e.target.textContent; ask(); }
  });
  $(".new").onclick = () => {
    if (busy) return;
    state = { session: newSession(), history: [], pending: null };
    save();
    chat.innerHTML = hintHTML;
  };
  $("form").onsubmit = (e) => { e.preventDefault(); ask(); };

  // Small markdown renderer: tables, bold, headings, lists.
  function md(text) {
    const lines = esc(text).split("\n");
    let html = "", table = [];
    const flush = () => {
      if (!table.length) return;
      const rows = table.filter((r) => !/^\|[\s|:-]+\|$/.test(r)).map((r) => r.slice(1, -1).split("|").map((c) => c.trim()));
      html += "<table>" + rows.map((r, i) => "<tr>" + r.map((c) => `<${i ? "td" : "th"}>${c}</${i ? "td" : "th"}>`).join("") + "</tr>").join("") + "</table>";
      table = [];
    };
    for (const l of lines) {
      if (l.trim().startsWith("|")) { table.push(l.trim()); continue; }
      flush();
      html += l.replace(/^#+\s*(.*)$/, "<b>$1</b>").replace(/^[-*]\s+/, "• ") + "<br>";
    }
    flush();
    return html.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
  }

  const add = (cls, html) => {
    const d = document.createElement("div");
    d.className = cls; d.innerHTML = html;
    chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
    return d;
  };

  const stepLabel = (s) => `<span class="dot"></span><b>${s.tool}</b> ${Object.keys(s.args || {}).length ? esc(JSON.stringify(s.args)) : ""}` +
    (s.status === "failed" ? `<br>${esc(s.result || "")}` : "");
  const stepsHTML = (steps) => steps.map((s) => `<div class="step ${s.status}">${stepLabel(s)}</div>`).join("");

  // Draws one saved history item. Returns its element so a running job can keep updating it.
  function render(item) {
    if (item.type === "user") return add("msg user", esc(item.text));
    if (item.type === "bot") return add("msg bot", item.error ? "Error: " + esc(item.text) : md(item.text));
    return add("", stepsHTML(item.steps));
  }

  function push(item) {
    state.history.push(item);
    save();
    return render(item);
  }

  // Polls a job until it finishes, updating the steps item as it goes. Also used to resume after a reload.
  async function follow(jobId, stepsItem, stepsBox) {
    const thinking = add("hint", "Thinking...");
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const r = await send({ type: "job", id: jobId });
      if (r && !r.ok && /404/.test(r.error)) { // the backend restarted and forgot this job
        thinking.remove();
        push({ type: "bot", text: "I lost track of that request because the backend restarted. Ask again.", error: true });
        break;
      }
      if (!r || !r.ok) continue;
      const job = r.data;
      stepsItem.steps = job.steps;
      stepsBox.innerHTML = stepsHTML(job.steps);
      chat.scrollTop = chat.scrollHeight;
      if (job.done) {
        thinking.remove();
        push({ type: "bot", text: job.error || job.reply || "", error: !!job.error });
        break;
      }
    }
    state.pending = null;
    save();
    busy = false;
    loadEvents();
  }

  async function ask() {
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true; input.value = "";
    push({ type: "user", text });
    const started = await send({ type: "chat", session: state.session, message: text });
    if (!started || !started.ok) {
      push({ type: "bot", text: "Could not reach the backend: " + (started ? started.error : "no response"), error: true });
      busy = false;
      return;
    }
    state.pending = started.data.job;
    const stepsItem = { type: "steps", steps: [] };
    const stepsBox = push(stepsItem);
    follow(state.pending, stepsItem, stepsBox);
  }

  const ago = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
    return s < 60 ? Math.floor(s) + "s ago" : s < 3600 ? Math.floor(s / 60) + "m ago" : Math.floor(s / 3600) + "h ago";
  };

  async function loadEvents() {
    const r = await send({ type: "events" });
    if (!r || !r.ok) { activity.innerHTML = `<div class="hint">Could not load events: ${esc(r ? r.error : "no response")}</div>`; return; }
    const running = r.data.filter((e) => e.status === "in progress").length;
    $(".badge").textContent = running ? `(${running} running)` : "";
    activity.innerHTML = r.data.map((e) => {
      const cls = e.status === "in progress" ? "inprogress" : e.status;
      const pct = e.percent != null && e.status === "in progress" ? ` ${e.percent}%` : "";
      return `<div class="ev ${cls}"><span class="dot"></span><b>${esc(e.action)}</b> ${esc(e.entity || "")}` +
        `<small>${e.status}${pct} · ${ago(e.created)} · ${esc(e.user || "")}</small></div>`;
    }).join("") || '<div class="hint">No events yet.</div>';
  }

  setInterval(() => { if (panel.classList.contains("open")) loadEvents(); }, 4000);
  loadEvents(); // the panel stays minimized until the icon is clicked

  chrome.storage.local.get("architect", ({ architect }) => {
    if (!architect) return;
    state = architect;
    if (state.history.length) chat.innerHTML = "";
    let lastSteps, lastBox;
    for (const item of state.history) {
      const box = render(item);
      if (item.type === "steps") { lastSteps = item; lastBox = box; }
    }
    if (state.pending && lastSteps) { busy = true; follow(state.pending, lastSteps, lastBox); } // the request was still running
  });
})();
