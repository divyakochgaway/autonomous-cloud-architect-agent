// The page is https, so it cannot call the http backend directly. This worker does it for the page.
importScripts("config.js");

async function call(path, options = {}) {
  const res = await fetch(CONFIG.BACKEND_URL + path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-API-Key": CONFIG.API_KEY },
  });
  if (!res.ok) throw new Error(`backend ${res.status}`);
  return res.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  let request;
  if (msg.type === "chat") {
    request = call("/chat", { method: "POST", body: JSON.stringify({ session: msg.session, message: msg.message }) });
  } else if (msg.type === "job") {
    request = call(`/jobs/${msg.id}`);
  } else if (msg.type === "events") {
    request = call("/events");
  } else {
    return false;
  }
  request.then((data) => reply({ ok: true, data })).catch((e) => reply({ ok: false, error: String(e) }));
  return true; // keeps the channel open for the async reply
});
