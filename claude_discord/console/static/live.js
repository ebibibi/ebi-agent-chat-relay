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

  // The panel lives in its own slot under the conversation, so it never covers the
  // messages it is reporting on. Open (the latest tool calls) unless folded to one line.
  let open = localStorage.getItem("liveOpen") !== "0";

  function draw(id, data) {
    last = { id, data };
    const box = document.getElementById("live-slot");
    if (!box) return;
    const msgs = document.getElementById("msgs");
    const pinned = msgs && msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 80;
    const live = data.live || {};
    const acts = live.activities || [];
    const latest = acts.length ? acts[acts.length - 1] : null;
    const head = el("div", "live-head");
    const toggle = el("button", "live-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.append(
      el("span", "live-caret", open ? "▾" : "▸"),
      el("span", "live-status", `${STATUS[live.status] || "⚙️"} ${L.working}`),
      el("span", "live-latest", !open && latest ? `${latest.title}${latest.detail ? " — " + latest.detail : ""}` : ""),
    );
    toggle.addEventListener("click", () => {
      open = !open;
      localStorage.setItem("liveOpen", open ? "1" : "0");
      if (last) draw(last.id, last.data);
    });
    head.append(toggle);
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
    const parts = [head];
    if (open) {
      const list = el("ul", "live-acts");
      for (const a of acts.slice(-6)) {
        const mark = a.done ? (a.ok ? "✓" : "✗") : "…";
        list.append(el("li", a.done ? "done" : "", `${mark} ${a.title}${a.detail ? " — " + a.detail : ""}`));
      }
      parts.push(list);
      if (live.draft) parts.push(el("div", "live-draft", live.draft));
    }
    const wrap = el("div", "live");
    wrap.id = "live";
    wrap.setAttribute("aria-live", "polite");
    wrap.replaceChildren(...parts);
    box.replaceChildren(wrap);
    // The panel changed height; a reader following the conversation stays at its end.
    if (pinned) msgs.scrollTop = msgs.scrollHeight;
  }

  function clear() {
    last = null;
    const box = document.getElementById("live-slot");
    if (box) box.replaceChildren();
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
  // Images get an inline thumbnail that opens a preview; everything else a download button.
  const IMAGE_TYPES = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
    webp: "image/webp", bmp: "image/bmp", avif: "image/avif", svg: "image/svg+xml" };
  const previews = new Map(); // href -> Promise<data: URL>, so a re-render does not refetch
  const fileName = (href) => decodeURIComponent(href.split("/").pop());
  const imageType = (href) => IMAGE_TYPES[(fileName(href).split(".").pop() || "").toLowerCase()];

  // Anything added below the messages after they rendered (file rows, a thumbnail that
  // finished loading) must not pull a reader who was following away from the end.
  function keepPinned(change) {
    const msgs = document.getElementById("msgs");
    const pinned = msgs && msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 80;
    change();
    if (pinned) msgs.scrollTop = msgs.scrollHeight;
  }

  function linkFiles() { keepPinned(addFileRows); }
  function addFileRows() {
    for (const body of document.querySelectorAll(".msg .body:not([data-files])")) {
      body.setAttribute("data-files", "");
      const found = body.textContent.match(/\/console\/api\/files\/\d+\/[0-9a-f]{32}\/[^\s)\]]+/g)
        || [...body.querySelectorAll(`a[href^="${FILES}"]`)].map((a) => a.getAttribute("href"));
      if (!found.length) continue;
      const row = el("div", "live-files");
      for (const href of new Set(found)) {
        if (imageType(href)) row.append(thumbnail(href));
        const P = window.ConsolePreview;
        if (P && P.kind(fileName(href))) {
          const look = el("button", "btn", `👁 ${P.label} ${fileName(href)}`);
          look.type = "button";
          look.addEventListener("click", () => P.open(fileName(href), () => fetchFile(href), () => download(href)));
          row.append(look);
        }
        const btn = el("button", "btn", `📎 ${L.download} ${fileName(href)}`);
        btn.type = "button";
        btn.addEventListener("click", () => download(href));
        row.append(btn);
      }
      body.after(row);
    }
  }

  async function fetchFile(href) {
    const headers = {};
    const token = localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(href, { headers, credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }

  // A data: URL, not a blob: one: the console's CSP allows img-src 'self' data: only.
  function previewUrl(href) {
    if (!previews.has(href)) {
      previews.set(href, fetchFile(href).then((blob) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        // The server sends octet-stream; the extension says what the image is.
        reader.readAsDataURL(new Blob([blob], { type: imageType(href) }));
      })));
      previews.get(href).catch(() => previews.delete(href));
    }
    return previews.get(href);
  }

  function thumbnail(href) {
    const btn = el("button", "file-thumb");
    btn.type = "button";
    btn.title = fileName(href);
    btn.append(el("span", "file-thumb-wait", "🖼️"));
    previewUrl(href).then((url) => {
      const img = el("img");
      img.alt = fileName(href);
      img.src = url;
      // Decoded first, so the height is known when the end is re-pinned.
      img.decode().catch(() => {}).then(() => keepPinned(() => btn.replaceChildren(img)));
    }).catch(() => btn.replaceChildren(el("span", "file-thumb-wait", "⚠️")));
    btn.addEventListener("click", () => preview(href));
    return btn;
  }

  function preview(href) {
    lightbox(fileName(href), previewUrl(href), () => download(href));
  }

  // Full-window image preview. *src* is a URL or a promise of one; Esc or a click closes it.
  async function lightbox(name, src, onDownload) {
    const shade = el("div", "lightbox");
    const close = () => { shade.remove(); document.removeEventListener("keydown", onKey, true); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } };
    const bar = el("div", "lightbox-bar");
    bar.append(el("span", "lightbox-name", name));
    if (onDownload) {
      const save = el("button", "btn", `📎 ${L.download}`);
      save.type = "button";
      save.addEventListener("click", (e) => { e.stopPropagation(); onDownload(); });
      bar.append(save);
    }
    const x = el("button", "btn", "✕");
    x.type = "button";
    x.addEventListener("click", close);
    bar.append(x);
    const img = el("img", "lightbox-img");
    img.alt = name;
    img.addEventListener("click", (e) => e.stopPropagation());
    img.addEventListener("error", () => { close(); toast(L.failed); });
    shade.append(bar, img);
    shade.addEventListener("click", close);
    document.addEventListener("keydown", onKey, true);
    document.body.append(shade);
    try { img.src = await src; }
    catch (err) { close(); toast(`${L.failed}: ${err.message}`); }
  }
  window.ConsoleLightbox = lightbox;

  async function download(href) {
    try {
      const url = URL.createObjectURL(await fetchFile(href));
      const a = el("a");
      a.href = url;
      a.download = fileName(href);
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
    if (href && href.startsWith(FILES)) { e.preventDefault(); if (imageType(href)) preview(href); else download(href); }
  });

  // The board refresh rebuilds the detail pane; put the panel back at once rather than
  // letting it blink out until the next tick.
  const detail = document.getElementById("detail");
  if (detail) {
    new MutationObserver(() => {
      if (last && state.open === last.id && !document.getElementById("live")) draw(last.id, last.data);
      else if (last && state.open !== last.id) clear();
      linkFiles();
    }).observe(detail, { childList: true });
  }

  setInterval(tick, TICK_MS);
})();
