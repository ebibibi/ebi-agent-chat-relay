// In-place preview for typical files: Markdown, HTML, CSV/TSV, JSON and other text open in a
// full-window viewer instead of downloading. Markdown is rendered by the console's own
// DOM-building renderer (no HTML parsing). HTML is rendered in an iframe with an empty
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
          copy: "Copy contents to clipboard", copying: "Copying…", copied: "Copied", copyFailed: "Could not copy contents", rows: (n) => `first ${n} rows` },
    ja: { preview: "プレビュー", source: "ソース", rendered: "表示", download: "ダウンロード",
          cut: (n) => `先頭 ${n} だけ表示しています。全体はダウンロードしてください`, failed: "ファイルを開けませんでした",
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

  function rendered(k, text, name) {
    if (k === "markdown") return h("div", { class: "pv-doc md" }, MD.render(text));
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
  function close() {
    if (!current) return;
    current.remove();
    current = null;
    document.removeEventListener("keydown", onKey, true);
  }
  function onKey(e) {
    if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); }
  }

  // Open *name*; *load* resolves to its Blob; *onDownload* saves the whole file.
  async function open(name, load, onDownload) {
    close();
    const k = kind(name) || "text";
    const body = h("div", { class: "pv-body" }, h("div", { class: "empty", text: "…" }));
    const bar = h("div", { class: "pv-bar" }, h("span", { class: "pv-name", text: name }));
    const shade = h("div", { class: "pv-shade", onclick: (e) => { if (e.target === shade) close(); } },
      h("div", { class: "pv-panel", role: "dialog", "aria-label": name }, bar, body));
    current = shade;
    document.addEventListener("keydown", onKey, true);
    document.body.append(shade);
    let data, blob;
    try {
      blob = await load();
      data = await readText(blob);
      data.size = blob.size;
    } catch (err) { close(); toast(`${L().failed}: ${err.message}`); return; }
    if (current !== shade) return;
    let showSource = false;
    const draw = () => {
      body.replaceChildren(showSource ? pre(data.text) : rendered(k, data.text, name));
      if (data.cut) body.prepend(h("p", { class: "pv-note", text: L().cut(sizeText(MAX_BYTES)) }));
    };
    const tools = [copyButton(() => Promise.resolve(blob))];
    if (k === "markdown" || k === "html" || k === "csv" || k === "json") {
      const flip = h("button", { class: "btn", type: "button", text: L().source,
        onclick: () => { showSource = !showSource; flip.textContent = showSource ? L().rendered : L().source; draw(); } });
      tools.push(flip);
    }
    if (onDownload) tools.push(h("button", { class: "btn", type: "button", text: `📎 ${L().download}`, onclick: onDownload }));
    tools.push(h("button", { class: "btn", type: "button", "aria-label": "close", text: "✕", onclick: close }));
    bar.append(h("span", { class: "dim pv-size", text: sizeText(data.size) }), ...tools);
    draw();
  }

  // Fetch a file the console serves (with the console's credentials) or a chat attachment.
  async function fetchBlob(url) {
    const own = url.startsWith("/");
    const headers = {};
    const token = own ? localStorage.getItem("token") : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(url, own ? { headers, credentials: "same-origin" } : { credentials: "omit" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }

  return { kind, open, fetchBlob, copyButton, get label() { return L().preview; } };
})();
