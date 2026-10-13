// Attach files to a reply: paste them, drop them on the reply box, or pick them with the 📎
// button. Each file uploads at once and waits above the box — an image as a thumbnail, any
// other file as a named chip with its size; the reply then names the uploads, and the agent
// receives a local path it can open. Loaded before app.js, which calls attach() for the reply
// box and button() for the 📎 next to Send.
"use strict";

window.ConsolePaste = (() => {
  const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  const MAX_IMAGE = 10 * 1024 * 1024;
  const MAX_FILE = 50 * 1024 * 1024;
  const MAX_COUNT = 10;
  const pending = new Map(); // item id -> [{ url, name, size, preview }]
  const trays = new Map(); // item id -> its tray element

  const list = (id) => pending.get(id) || [];
  const isImage = (file) => IMAGE_TYPES.includes(file.type);

  function sizeText(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  async function upload(file) {
    const headers = { "X-Console-Request": "1", "Content-Type": file.type || "application/octet-stream" };
    // A pasted screenshot is always called "image.png"; let the server name it by time instead.
    if (file.name && !(isImage(file) && /^image\.\w+$/.test(file.name))) headers["X-Filename"] = encodeURIComponent(file.name);
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
      if (list(id).length >= MAX_COUNT) { toast(T.attach_many(MAX_COUNT)); break; }
      const limit = isImage(file) ? MAX_IMAGE : MAX_FILE;
      if (!file.size) { toast(`${T.attach_failed}: ${file.name || ""} (0 B)`); continue; }
      if (file.size > limit) { toast(T.attach_big(file.name, sizeText(limit))); continue; }
      const entry = { url: null, name: file.name || "file", size: file.size,
        preview: isImage(file) ? await readPreview(file) : null };
      pending.set(id, [...list(id), entry]);
      draw(id);
      try { entry.url = await upload(file); }
      catch (err) {
        pending.set(id, list(id).filter((e) => e !== entry));
        toast(`${T.attach_failed}: ${err.message}`);
      }
      draw(id);
    }
  }

  function draw(id) {
    const tray = trays.get(id);
    if (!tray || !tray.isConnected) return;
    const items = list(id);
    tray.hidden = !items.length;
    tray.replaceChildren(...items.map((entry) => {
      const remove = h("button", { class: "paste-x", type: "button", "aria-label": "remove", text: "✕",
        onclick: () => { pending.set(id, list(id).filter((e) => e !== entry)); draw(id); } });
      if (entry.preview) {
        return h("div", { class: "paste-item" + (entry.url ? "" : " busy"), title: entry.name },
          h("img", { src: entry.preview, alt: entry.name }), remove);
      }
      return h("div", { class: "paste-file" + (entry.url ? "" : " busy"), title: entry.name },
        h("span", { class: "paste-name", text: `📄 ${entry.name}` }),
        h("span", { class: "dim", text: sizeText(entry.size) }), remove);
    }));
  }

  const filesOf = (items) => [...(items || [])]
    .filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);

  // Wire the reply box of item *id*; *trayEl* shows what is waiting to be sent.
  function attach(box, id, trayEl) {
    trays.set(id, trayEl);
    draw(id);
    box.addEventListener("paste", (e) => {
      const files = filesOf(e.clipboardData && e.clipboardData.items);
      if (!files.length) return; // plain text pastes as usual
      e.preventDefault();
      add(id, files);
    });
    const form = box.form || box;
    form.addEventListener("dragover", (e) => {
      if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); form.classList.add("dropping"); }
    });
    form.addEventListener("dragleave", () => form.classList.remove("dropping"));
    form.addEventListener("drop", (e) => {
      form.classList.remove("dropping");
      const files = [...((e.dataTransfer && e.dataTransfer.files) || [])];
      if (!files.length) return;
      e.preventDefault();
      add(id, files);
    });
  }

  // The 📎 button: opens the file picker (several files at once) for item *id*.
  function button(id) {
    const input = h("input", { type: "file", multiple: true, hidden: true,
      onchange: (e) => { add(id, [...e.target.files]); e.target.value = ""; } });
    return h("span", { class: "attach-wrap" }, input,
      h("button", { class: "btn attach", type: "button", title: T.attach_hint, "aria-label": T.attach_hint,
        onclick: () => input.click(), text: "📎" }));
  }

  // Uploaded URLs ready to send; null while one is still uploading.
  function ready(id) {
    const items = list(id);
    return items.some((e) => !e.url) ? null : items.map((e) => e.url);
  }
  function clear(id) { pending.delete(id); draw(id); }

  return { attach, button, ready, clear };
})();
