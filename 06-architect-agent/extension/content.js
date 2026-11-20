// Floating chat + activity panel injected into cloud.linode.com.
(() => {
  if (document.getElementById("akamai-architect-host")) return;

  const session = sessionStorage.getItem("architect-session") || Math.random().toString(36).slice(2);
  sessionStorage.setItem("architect-session", session);

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
  <div class="panel">
    <header><span>Cloud Architect <small>Akamai Inference + Cloud</small></span><span class="close" style="cursor:pointer">&#10005;</span></header>
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

  $(".fab").onclick = () => panel.classList.toggle("open");
  $(".close").onclick = () => panel.classList.remove("open");
  root.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
    root.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    chat.classList.toggle("on", b.dataset.tab === "chat");
    activity.classList.toggle("on", b.dataset.tab === "activity");
    if (b.dataset.tab === "activity") loadEvents();
  }));
  root.querySelectorAll(".chip").forEach((c) => (c.onclick = () => { input.value = c.textContent; ask(); }));
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

  async function ask() {
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true; input.value = "";
    add("msg user", esc(text));
    const started = await send({ type: "chat", session, message: text });
    if (!started || !started.ok) { add("msg bot", "Could not reach the backend: " + esc(started ? started.error : "no response")); busy = false; return; }
    const stepsBox = add("", ""), thinking = add("hint", "Thinking...");
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const r = await send({ type: "job", id: started.data.job });
      if (!r || !r.ok) continue;
      const job = r.data;
      stepsBox.innerHTML = job.steps.map((s) => `<div class="step ${s.status}">${stepLabel(s)}</div>`).join("");
      chat.scrollTop = chat.scrollHeight;
      if (job.done) {
        thinking.remove();
        add("msg bot", job.error ? "Error: " + esc(job.error) : md(job.reply || ""));
        break;
      }
    }
    busy = false;
    loadEvents();
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
  panel.classList.add("open"); // pops up when cloud.linode.com loads
  loadEvents();
})();
