// Paste or drop images into the reply box. Each image uploads at once and shows as a
// thumbnail above the box; the reply then names the uploads, and the agent receives a
// local path it can open. Loaded before app.js, which calls attach() for the reply box.
"use strict";

window.ConsolePaste = (() => {
  const TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  const pending = new Map(); // item id -> [{ url, preview }]
  let tray = null;
  let current = null;

  const list = (id) => pending.get(id) || [];

  async function upload(file) {
    const headers = { "X-Console-Request": "1", "Content-Type": file.type };
    const token = localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch("/console/api/uploads", { method: "POST", headers, body: file, credentials: "same-origin" });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data.url;
  }

  // A data: URL for the thumbnail: the CSP allows img-src 'self' data: (not blob:).
  const readPreview = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

  async function add(id, files) {
    for (const file of files) {
      if (!TYPES.includes(file.type)) { toast(T.paste_type); continue; }
      const entry = { url: null, preview: await readPreview(file) };
      pending.set(id, [...list(id), entry]);
      draw();
      try { entry.url = await upload(file); }
      catch (err) {
        pending.set(id, list(id).filter((e) => e !== entry));
        toast(`${T.paste_failed}: ${err.message}`);
      }
      draw();
    }
  }

  function draw() {
    if (!tray || !tray.isConnected) return;
    const items = list(current);
    tray.hidden = !items.length;
    tray.replaceChildren(...items.map((entry) => {
      const remove = h("button", { class: "paste-x", type: "button", "aria-label": "remove", text: "✕",
        onclick: () => { pending.set(current, list(current).filter((e) => e !== entry)); draw(); } });
      return h("div", { class: "paste-item" + (entry.url ? "" : " busy") },
        h("img", { src: entry.preview, alt: "" }), remove);
    }));
  }

  const imagesOf = (items) => [...(items || [])]
    .filter((i) => i.kind === "file" && i.type.startsWith("image/"))
    .map((i) => i.getAsFile()).filter(Boolean);

  // Wire the reply box of item *id*; *trayEl* shows what is waiting to be sent.
  function attach(box, id, trayEl) {
    tray = trayEl;
    current = id;
    draw();
    box.addEventListener("paste", (e) => {
      const files = imagesOf(e.clipboardData && e.clipboardData.items);
      if (!files.length) return; // plain text pastes as usual
      e.preventDefault();
      add(id, files);
    });
    const form = box.form || box;
    form.addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
    form.addEventListener("drop", (e) => {
      const files = [...((e.dataTransfer && e.dataTransfer.files) || [])].filter((f) => f.type.startsWith("image/"));
      if (!files.length) return;
      e.preventDefault();
      add(id, files);
    });
  }

  // Uploaded URLs ready to send; null while one is still uploading.
  function ready(id) {
    const items = list(id);
    return items.some((e) => !e.url) ? null : items.map((e) => e.url);
  }
  function clear(id) { pending.delete(id); if (id === current) draw(); }

  return { attach, ready, clear };
})();
