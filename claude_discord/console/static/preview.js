// In-place preview for typical files: Markdown, HTML, CSV/TSV, JSON and other text open in a
// full-window viewer instead of downloading. Markdown uses the vendored Markdown parser
// (HTML off) and DOMPurify. Images use only declared attachments, safe image URLs or the
// document directory; native attachments are fetched with Console authentication.
// HTML is rendered in an iframe with an empty
// sandbox: no scripts, no forms, no same-origin access, so a delivered page cannot reach
// the console. "Source" shows the raw text; Download is always there. A large file shows
// its first part. Loaded before live.js and app.js, which call kind() and open().
"use strict";

window.ConsolePreview = (() => {
  const MAX_BYTES = 2 * 1024 * 1024;
  const MAX_ROWS = 1000;
  // Read at use: this file loads before app.js picks the language.
  const STRINGS = {
    en: { preview: "Preview", source: "Source", rendered: "Rendered", download: "Download",
          cut: (n) => `Showing the first ${n} — download for the whole file`, failed: "Could not open the file",
          fullscreen: "Fullscreen", restore: "Restore size", imageFailed: "Image unavailable",
          copy: "Copy contents to clipboard", copying: "Copying…", copied: "Copied", copyFailed: "Could not copy contents", rows: (n) => `first ${n} rows` },
    ja: { preview: "プレビュー", source: "ソース", rendered: "表示", download: "ダウンロード",
          cut: (n) => `先頭 ${n} だけ表示しています。全体はダウンロードしてください`, failed: "ファイルを開けませんでした",
          fullscreen: "全画面", restore: "元のサイズ", imageFailed: "画像を読み込めません",
          copy: "内容をクリップボードにコピー", copying: "コピー中…", copied: "コピーしました", copyFailed: "コピーできませんでした", rows: (n) => `先頭 ${n} 行` },
  };
  const L = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  const EXT = {
    markdown: ["md", "markdown", "mdx"],
    html: ["html", "htm"],
    csv: ["csv", "tsv"],
    json: ["json", "jsonl", "ndjson", "geojson"],
    text: ["txt", "log", "text", "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "xml", "svg",
      "py", "js", "mjs", "ts", "tsx", "jsx", "css", "scss", "sh", "bash", "zsh", "ps1", "psm1", "bat",
      "cmd", "sql", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "cs", "rb", "php", "swift",
      "lua", "r", "pl", "diff", "patch", "dockerfile", "makefile", "tf", "bicep", "properties", "rst",
      "tex", "srt", "vtt"],
  };

  // What kind of preview a file name gets, or null when it is not a text file we render.
  function kind(name) {
    const base = String(name || "").toLowerCase().split(/[?#]/)[0].split("/").pop();
    const ext = base.includes(".") ? base.split(".").pop() : base;
    for (const [k, list] of Object.entries(EXT)) if (list.includes(ext)) return k;
    return null;
  }

  function sizeText(n) {
    if (n < 1024) return `${n} B`;
    return n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  // Text of a blob, cut at MAX_BYTES; UTF-8 first, Shift_JIS if UTF-8 is clearly wrong.
  async function readText(blob, limited = true) {
    const cut = limited && blob.size > MAX_BYTES;
    const bytes = new Uint8Array(await (cut ? blob.slice(0, MAX_BYTES) : blob).arrayBuffer());
    let text = new TextDecoder("utf-8").decode(bytes);
    if ((text.match(/�/g) || []).length > 8) {
      try { text = new TextDecoder("shift_jis").decode(bytes); } catch { /* keep UTF-8 */ }
    }
    return { text: text.replace(/^﻿/, ""), cut };
  }

  function copyButton(load) {
    const button = h("button", { class: "btn file-copy", type: "button", text: L().copy });
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = L().copying;
      try {
        const text = load().then(blob => readText(blob, false)).then(data => data.text);
        // Start the clipboard operation in the user gesture, including Safari's async fetch case.
        if (navigator.clipboard?.write && window.ClipboardItem) {
          await navigator.clipboard.write([new ClipboardItem({ "text/plain": text.then(value => new Blob([value], {type: "text/plain"})) })]);
        } else if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(await text);
        } else {
          throw new Error("Clipboard API unavailable");
        }
        toast(L().copied);
      } catch (err) { toast(`${L().copyFailed}: ${err.message}`); }
      finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = L().copy;
      }
    });
    return button;
  }

  function parseDelimited(text, sep) {
    const rows = [];
    let row = [], cell = "", quoted = false;
    for (let i = 0; i < text.length && rows.length < MAX_ROWS; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (c === '"') quoted = false;
        else cell += c;
      } else if (c === '"' && cell === "") quoted = true;
      else if (c === sep) { row.push(cell); cell = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); rows.push(row); row = []; cell = "";
      } else cell += c;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  function table(text, name) {
    const rows = parseDelimited(text, /\.tsv$/i.test(name) ? "\t" : ",");
    const t = h("table", { class: "pv-table" });
    rows.forEach((r, i) => t.append(h("tr", {}, r.map((c) => h(i ? "td" : "th", { text: c })))));
    const wrap = h("div", { class: "pv-scroll" }, t);
    if (rows.length >= MAX_ROWS) wrap.append(h("p", { class: "dim", text: L().rows(MAX_ROWS) }));
    return wrap;
  }

  function pre(text) { return h("pre", { class: "pv-pre" }, h("code", { text })); }

  let libraries = null;
  function ensureMarkdown() {
    const load = (src) => new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src; script.onload = resolve; script.onerror = reject;
      document.head.append(script);
    });
    if (!libraries) libraries = Promise.all([
      window.markdownit ? null : load("/static/vendor-markdown-it.min.js"),
      window.DOMPurify ? null : load("/static/vendor-purify.min.js"),
    ]).catch(err => { libraries = null; throw err; });
    return libraries;
  }

  // Never turn a document's arbitrary local path into an authenticated API request.
  // Sibling attachments keep their actual (possibly signed) URLs, not guessed tokens.
  function imageTarget(src, context) {
    const raw = String(src || "").trim();
    if (/^data:image\/(?:png|jpeg|gif|webp|avif|bmp|svg\+xml)[;,]/i.test(raw)) return raw;
    if (!raw || raw.startsWith("//") || raw.includes("\\")) return null;
    let parsed;
    try { parsed = new URL(raw, location.href); } catch { return null; }
    if (/^[a-z][\w+.-]*:/i.test(raw)) {
      if (parsed.protocol === "https:" && parsed.origin !== location.origin) return parsed.href;
      if (parsed.origin !== location.origin) return null;
    } else if (!raw.startsWith("/")) {
      let name;
      try { name = decodeURIComponent(raw.split(/[?#]/)[0]).split("/").pop(); } catch { return null; }
      const matches = (context.attachments || []).filter(a => a.filename === name);
      if (matches.length === 1) return imageTarget(matches[0].url, {});
      if (matches.length > 1 || !context.url) return null;
      try { parsed = new URL(raw, new URL(context.url, location.href)); } catch { return null; }
      if (parsed.origin !== location.origin) return parsed.protocol === "https:" ? parsed.href : null;
    }
    if (parsed.origin === location.origin && /^\/console\/api\/files\/\d+\/[0-9a-f]{32}\/[^/]+$/.test(parsed.pathname)) return parsed.href;
    return null;
  }

  function markdown(text) {
    const parser = window.markdownit({ html: false, linkify: true, breaks: true });
    parser.renderer.rules.image = (tokens, idx) => {
      const token = tokens[idx];
      const esc = parser.utils.escapeHtml;
      return `<img alt="${esc(token.content)}" data-pv-src="${esc(token.attrGet("src") || "")}">`;
    };
    const fragment = window.DOMPurify.sanitize(parser.render(text), {
      RETURN_DOM_FRAGMENT: true, FORBID_TAGS: ["style", "form", "iframe", "object", "embed"],
    });
    for (const a of fragment.querySelectorAll("a[href]")) {
      a.target = "_blank"; a.rel = "noopener noreferrer";
    }
    return fragment;
  }

  async function loadImage(img, context, shade) {
    const src = imageTarget(img.dataset.pvSrc, context);
    img.removeAttribute("data-pv-src");
    img.referrerPolicy = "no-referrer";
    const failed = () => {
      if (current === shade && img.isConnected) img.replaceWith(h("span", {
        class: "pv-image-missing", role: "img", text: `${img.alt || "Image"} — ${L().imageFailed}`,
      }));
    };
    if (!src) { failed(); return; }
    img.addEventListener("error", failed, { once: true });
    try {
      if (new URL(src, location.href).origin === location.origin) {
        const blob = await fetchBlob(src);
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(reader.result);
          reader.onerror = reject; reader.readAsDataURL(blob);
        });
        if (current === shade && img.isConnected) img.src = data;
      } else img.src = src;
    } catch { failed(); }
  }

  function rendered(k, text, name) {
    if (k === "markdown") return h("div", { class: "pv-doc md" }, markdown(text));
    if (k === "html") {
      const frame = h("iframe", { class: "pv-frame", title: name, referrerpolicy: "no-referrer" });
      frame.setAttribute("sandbox", ""); // everything off: no scripts, forms, popups or same-origin
      frame.srcdoc = text;
      return frame;
    }
    if (k === "csv") return table(text, name);
    if (k === "json") {
      try { return pre(JSON.stringify(JSON.parse(text), null, 2)); } catch { return pre(text); }
    }
    return pre(text);
  }

  let current = null;
  let previousFocus = null;
  let previousOverflow = "";
  function close() {
    if (!current) return;
    current.remove();
    current = null;
    document.removeEventListener("keydown", onKey, true);
    document.body.style.overflow = previousOverflow;
    previousFocus?.focus();
  }
  function onKey(e) {
    if (e.key === "Escape") {
      e.stopPropagation(); e.preventDefault();
      if (current?.classList.contains("pv-fullscreen")) current.querySelector(".pv-fullscreen-toggle").click();
      else close();
    }
  }

  // Open *name*; *load* resolves to its Blob; *onDownload* saves the whole file.
  async function open(name, load, onDownload, context = {}) {
    close();
    previousFocus = document.activeElement;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const k = kind(name) || "text";
    const body = h("div", { class: "pv-body" }, h("div", { class: "empty", text: "…" }));
    const bar = h("div", { class: "pv-bar" }, h("span", { class: "pv-name", text: name }));
    const shade = h("div", { class: "pv-shade", onclick: (e) => { if (e.target === shade) close(); } },
      h("div", { class: "pv-panel", role: "dialog", "aria-modal": "true", "aria-label": name }, bar, body));
    current = shade;
    document.addEventListener("keydown", onKey, true);
    document.body.append(shade);
    let data, blob;
    try {
      blob = await load();
      data = await readText(blob);
      data.size = blob.size;
      if (k === "markdown") await ensureMarkdown();
    } catch (err) {
      if (current === shade) { close(); toast(`${L().failed}: ${err.message}`); }
      return;
    }
    if (current !== shade) return;
    let showSource = false;
    const draw = () => {
      body.replaceChildren(showSource ? pre(data.text) : rendered(k, data.text, name));
      for (const img of body.querySelectorAll("img[data-pv-src]")) loadImage(img, context, shade);
      if (data.cut) body.prepend(h("p", { class: "pv-note", text: L().cut(sizeText(MAX_BYTES)) }));
    };
    const full = h("button", { class: "btn pv-fullscreen-toggle", type: "button", text: L().fullscreen, "aria-pressed": "false" });
    full.addEventListener("click", () => {
      const expanded = shade.classList.toggle("pv-fullscreen");
      full.textContent = expanded ? L().restore : L().fullscreen;
      full.setAttribute("aria-pressed", String(expanded));
    });
    const tools = [full, copyButton(() => Promise.resolve(blob))];
    if (k === "markdown" || k === "html" || k === "csv" || k === "json") {
      const flip = h("button", { class: "btn", type: "button", text: L().source,
        onclick: () => { showSource = !showSource; flip.textContent = showSource ? L().rendered : L().source; draw(); } });
      tools.push(flip);
    }
    if (onDownload) tools.push(h("button", { class: "btn", type: "button", text: `📎 ${L().download}`, onclick: onDownload }));
    tools.push(h("button", { class: "btn", type: "button", "aria-label": "close", text: "✕", onclick: close }));
    bar.append(h("span", { class: "dim pv-size", text: sizeText(data.size) }), ...tools);
    draw();
    full.focus();
  }

  // Fetch a file the console serves (with the console's credentials) or a chat attachment.
  async function fetchBlob(url) {
    const target = new URL(url, location.href);
    if (!/^https?:$/.test(target.protocol)) throw new Error("Unsupported file URL");
    const own = target.origin === location.origin;
    const headers = {};
    const token = own ? localStorage.getItem("token") : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(url, own ? { headers, credentials: "same-origin" } : { credentials: "omit" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }

  return { kind, open, fetchBlob, copyButton, get label() { return L().preview; } };
})();
