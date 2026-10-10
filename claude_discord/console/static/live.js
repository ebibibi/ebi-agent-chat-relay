// Live view of a console conversation's running turn: tool activity, the answer as it
// streams, and a Stop button. Also turns delivered-file links into downloads that carry
// the console token. Server data reaches the page through textContent only.
"use strict";

(() => {
  const TICK_MS = 2000;
  const FILES = "/console/api/files/";
  const lang = document.documentElement.lang === "ja" ? "ja" : "en";
  const L = {
    en: { working: "Working…", stop: "Stop", stopping: "Stopping…", download: "Download",
          failed: "Download failed" },
    ja: { working: "作業中…", stop: "停止", stopping: "停止しています…", download: "ダウンロード",
          failed: "ダウンロードできませんでした" },
  }[lang];
  const STATUS = { thinking: "💭", tool_read: "📖", tool_edit: "✏️", tool_command: "⌨️",
    tool_web: "🌐", tool_other: "🔧", hook: "🪝", compacting: "🗜️", stalled_soft: "⏳",
    stalled_hard: "⚠️" };

  const notConsole = new Set(); // items whose /live said 404: chat threads
  const wasRunning = new Set();
  let ticks = 0;
  let last = null; // { id, data } of the panel on screen, to redraw after a re-render

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  function panel() {
    const msgs = document.getElementById("msgs");
    if (!msgs) return null;
    let box = document.getElementById("live");
    if (!box || box.previousElementSibling !== msgs) {
      if (box) box.remove();
      box = el("div", "live");
      box.id = "live";
      box.setAttribute("aria-live", "polite");
      msgs.after(box);
    }
    return box;
  }

  function draw(id, data) {
    last = { id, data };
    const box = panel();
    if (!box) return;
    const live = data.live || {};
    const head = el("div", "live-head");
    head.append(el("span", "live-status", `${STATUS[live.status] || "⚙️"} ${L.working}`));
    if (live.can_stop) {
      const stop = el("button", "btn live-stop", L.stop);
      stop.type = "button";
      stop.addEventListener("click", async () => {
        stop.disabled = true;
        stop.textContent = L.stopping;
        try { await api(`/items/${encodeURIComponent(id)}/stop`, { method: "POST", body: {} }); }
        catch (err) { toast(err.message); }
      });
      head.append(stop);
    }
    const acts = el("ul", "live-acts");
    for (const a of (live.activities || []).slice(-6)) {
      const mark = a.done ? (a.ok ? "✓" : "✗") : "…";
      acts.append(el("li", a.done ? "done" : "", `${mark} ${a.title}${a.detail ? " — " + a.detail : ""}`));
    }
    const parts = [head, acts];
    if (live.draft) parts.push(el("div", "live-draft", live.draft));
    box.replaceChildren(...parts);
  }

  function clear() {
    last = null;
    const box = document.getElementById("live");
    if (box) box.remove();
  }

  async function tick() {
    ticks += 1;
    linkFiles();
    const id = state.open;
    const item = id ? state.byId.get(id) : null;
    if (!item || !item.thread_id || notConsole.has(id)) { clear(); return; }
    let data;
    try { data = await api(`/items/${encodeURIComponent(id)}/live`); }
    catch (err) {
      if (String(err.message).includes("console conversation")) notConsole.add(id);
      return;
    }
    if (state.open !== id) return;
    if (data.running || data.live) {
      wasRunning.add(id);
      draw(id, data);
      // Answers are stored as they are written; show them without waiting for the end.
      if (ticks % 2 === 0) loadMessages(id);
    } else {
      clear();
      const last = state.messagesFor === id ? state.messages[state.messages.length - 1] : null;
      // A turn too short for the board to have shown it running: the answer is
      // still due if the human spoke last.
      if (wasRunning.delete(id)) { await refresh(); loadMessages(id); }
      else if (last && !last.is_bot) loadMessages(id);
    }
  }

  // Delivered files: the link needs the console token, which a plain <a> cannot send.
  function linkFiles() {
    for (const body of document.querySelectorAll(".msg .body:not([data-files])")) {
      body.setAttribute("data-files", "");
      const found = body.textContent.match(/\/console\/api\/files\/\d+\/[0-9a-f]{32}\/[^\s)\]]+/g);
      if (!found) continue;
      const row = el("div", "live-files");
      for (const href of new Set(found)) {
        const btn = el("button", "btn", `📎 ${L.download} ${decodeURIComponent(href.split("/").pop())}`);
        btn.type = "button";
        btn.addEventListener("click", () => download(href));
        row.append(btn);
      }
      body.after(row);
    }
  }

  async function download(href) {
    const headers = {};
    const token = localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const res = await fetch(href, { headers, credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = el("a");
      a.href = url;
      a.download = decodeURIComponent(href.split("/").pop());
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) { toast(`${L.failed}: ${err.message}`); }
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    if (href && href.startsWith(FILES)) { e.preventDefault(); download(href); }
  });

  // The board refresh rebuilds the detail pane; put the panel back at once rather than
  // letting it blink out until the next tick.
  const detail = document.getElementById("detail");
  if (detail) {
    new MutationObserver(() => {
      if (last && state.open === last.id && !document.getElementById("live")) draw(last.id, last.data);
      linkFiles();
    }).observe(detail, { childList: true });
  }

  setInterval(tick, TICK_MS);
})();
