// Read any Ebi Workspace note the way Obsidian shows it, and follow its links.
// Route: #note/<vault-relative path>. Back/forward are the browser's own history.
// Rendering: markdown-it (html off) with small Obsidian plugins — front matter as a properties
// table, [[wikilinks]] (resolved by the server like Obsidian), ![[embeds]] for images and one
// level of notes, > [!callouts], task checkboxes — then DOMPurify, returned as a DOM fragment
// (never innerHTML). Both libraries are vendored single files loaded on first use only.
// Excluded notes are invisible here: the server answers for them exactly like a missing note.
"use strict";

window.ConsoleNotes = (() => {
  const PREFIX = "#note/";
  let libs = null;
  let md = null;
  let panel = null;
  let current = null; // {path, text, links, ...}
  let returnHash = "";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    back: "← 戻る", forward: "進む →", close: "閉じる", backlinks: "🔗 このノートへのリンク", none: "なし",
    loading: "読み込み中…", missing: "ノートが見つかりません（存在しないか、表示しない設定です）", unresolved: "リンク先のノートがありません",
    properties: "プロパティ", ambiguous: "同名のノートが複数あります", copy: "パスをコピー", copied: "パスをコピーしました",
  } : {
    back: "← Back", forward: "Forward →", close: "Close", backlinks: "🔗 Linked from", none: "None",
    loading: "Loading…", missing: "Note not found (missing, or hidden by your settings)", unresolved: "No note with that name",
    properties: "Properties", ambiguous: "Several notes share this name", copy: "Copy path", copied: "Path copied",
  };

  // -- libraries ------------------------------------------------------------------------
  function load(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = () => reject(new Error(`could not load ${src}`));
      document.head.append(s);
    });
  }
  function ensureLibs() {
    libs ||= Promise.all([load("/static/vendor-markdown-it.min.js"), load("/static/vendor-purify.min.js")])
      .then(() => { md = build(); });
    return libs;
  }

  const fileUrl = (path) => `/console/api/workspace/file?path=${encodeURIComponent(path)}`;
  const noteHref = (path) => PREFIX + path.split("/").map(encodeURIComponent).join("/");
  const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

  function build() {
    const m = window.markdownit({ html: false, linkify: true, breaks: true, typographer: false });
    const esc = m.utils.escapeHtml;

    // [[target#heading|alias]] and ![[embed]]
    m.inline.ruler.before("link", "wikilink", (state, silent) => {
      const src = state.src;
      let at = state.pos;
      const embed = src.charCodeAt(at) === 0x21; // !
      if (embed) at += 1;
      if (src.slice(at, at + 2) !== "[[") return false;
      const end = src.indexOf("]]", at + 2);
      if (end < 0) return false;
      const raw = src.slice(at + 2, end);
      if (!raw || raw.includes("\n") || raw.includes("[[")) return false;
      if (!silent) {
        const token = state.push("wikilink", "", 0);
        token.meta = { raw, embed };
      }
      state.pos = end + 2;
      return true;
    });
    m.renderer.rules.wikilink = (tokens, idx) => {
      const { raw, embed } = tokens[idx].meta;
      const [left, alias] = raw.split("|");
      const target = left.split("#")[0].split("^")[0].trim();
      const label = alias || left.trim();
      const resolved = current && current.links ? current.links[target] : undefined;
      if (embed && resolved && IMAGE.test(resolved)) {
        return `<img class="note-img" alt="${esc(label)}" src="${esc(fileUrl(resolved))}">`;
      }
      if (embed && resolved && /\.md$/i.test(resolved)) {
        return `<div class="note-embed" data-embed="${esc(resolved)}"></div>`;
      }
      const cls = resolved ? "wikilink" : "wikilink unresolved";
      const href = resolved ? noteHref(resolved) : "#";
      const heading = left.includes("#") ? ` data-heading="${esc(left.split("#")[1])}"` : "";
      return `<a class="${cls}" href="${esc(href)}" data-target="${esc(target)}"${heading}>${esc(label)}</a>`;
    };

    // - [ ] task / - [x] done → a read-only checkbox
    m.core.ruler.after("inline", "tasks", (state) => {
      for (const token of state.tokens) {
        if (token.type !== "inline" || !token.children || !token.children.length) continue;
        const first = token.children[0];
        const match = first.type === "text" && /^\[([ xX])\]\s/.exec(first.content);
        if (!match) continue;
        first.content = first.content.slice(match[0].length);
        const box = new state.Token("html_inline", "", 0);
        box.content = `<input type="checkbox" disabled${match[1] === " " ? "" : " checked"}> `;
        token.children.unshift(box);
      }
    });

    // > [!type] Title → a callout box
    m.core.ruler.after("inline", "callouts", (state) => {
      const tokens = state.tokens;
      for (let i = 0; i < tokens.length; i += 1) {
        if (tokens[i].type !== "blockquote_open") continue;
        const inline = tokens[i + 2];
        if (!inline || inline.type !== "inline") continue;
        const match = /^\[!([\w-]+)\]([+-]?)[ \t]*([^\n]*)/.exec(inline.content);
        if (!match) continue;
        const type = match[1].toLowerCase();
        tokens[i].attrJoin("class", `callout callout-${type}`);
        const title = new state.Token("html_inline", "", 0);
        title.content = `<span class="callout-title">${esc(match[3] || match[1])}</span>`;
        const rest = inline.children.slice(1);
        const firstText = inline.children[0];
        const lines = firstText && firstText.type === "text" ? firstText.content.split("\n") : [];
        // Drop the marker line; keep anything after a soft break on the same paragraph.
        const keep = rest.length && rest[0].type === "softbreak" ? rest.slice(1) : rest;
        if (lines.length > 1) firstText.content = lines.slice(1).join("\n");
        inline.children = lines.length > 1 ? [title, firstText, ...keep] : [title, ...keep];
      }
    });
    return m;
  }

  // -- front matter -------------------------------------------------------------------------
  function split(text) {
    const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
    if (!match) return { props: [], body: text };
    const props = [];
    for (const line of match[1].split(/\r?\n/)) {
      const kv = /^([\w\-ぁ-んァ-ヶ一-龠]+)\s*:\s*(.*)$/.exec(line);
      if (kv) props.push([kv[1], kv[2]]);
      else if (props.length && /^\s+-\s+/.test(line)) props[props.length - 1][1] += ` ${line.trim()}`;
    }
    return { props, body: text.slice(match[0].length) };
  }

  function render(text) {
    const { props, body } = split(text);
    const html = md.render(body);
    const fragment = window.DOMPurify.sanitize(html, {
      RETURN_DOM_FRAGMENT: true,
      ADD_ATTR: ["data-target", "data-embed", "data-heading"],
      FORBID_TAGS: ["style", "form", "iframe", "object", "embed"],
    });
    for (const a of fragment.querySelectorAll("a[href]")) {
      if (/^https?:/i.test(a.getAttribute("href"))) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
    }
    const out = document.createDocumentFragment();
    if (props.length) {
      out.append(h("details", { class: "note-props" }, h("summary", { text: L().properties }),
        h("table", {}, ...props.map(([k, v]) => h("tr", {}, h("th", { text: k }), h("td", { text: v }))))));
    }
    out.append(fragment);
    return out;
  }

  // -- panel ----------------------------------------------------------------------------------
  function ensurePanel() {
    if (panel) return panel;
    panel = h("section", { id: "note-viewer", class: "note-viewer", role: "dialog", "aria-modal": "true", hidden: true });
    panel.addEventListener("click", onClick);
    panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    document.body.append(panel);
    return panel;
  }

  function crumbs(path) {
    const parts = path.split("/");
    return h("div", { class: "note-crumbs" }, ...parts.map((p, i) =>
      h("span", { class: i === parts.length - 1 ? "note-crumb last" : "note-crumb dim", text: i === parts.length - 1 ? p.replace(/\.md$/, "") : `${p} / ` })));
  }

  function frame(path, ...content) {
    const l = L();
    ensurePanel().replaceChildren(h("div", { class: "note-shell" },
      h("header", { class: "note-head" },
        h("button", { type: "button", class: "btn", text: l.back, onclick: () => history.back() }),
        h("button", { type: "button", class: "btn", text: l.forward, onclick: () => history.forward() }),
        crumbs(path),
        h("button", { type: "button", class: "btn ghost", text: l.copy, onclick: async () => {
          try { await navigator.clipboard.writeText(path); toast(l.copied); } catch (err) { toast(err.message); }
        } }),
        h("button", { type: "button", class: "btn", text: l.close, onclick: close })),
      h("article", { class: "note-body md", tabindex: "-1" }, ...content)));
    panel.hidden = false;
  }

  async function show(path) {
    frame(path, h("p", { class: "dim", text: L().loading }));
    try {
      const [note] = await Promise.all([api(`/workspace/note?path=${encodeURIComponent(path)}`), ensureLibs()]);
      if (decodeHash() !== path) return; // navigated on meanwhile
      current = note;
      const body = h("div", { class: "note-content" });
      body.append(render(note.text));
      const back = h("div", { class: "note-backlinks" }, h("h3", { text: L().backlinks }), h("p", { class: "dim", text: L().loading }));
      frame(note.path, body, back);
      panel.querySelector(".note-body").focus();
      fillEmbeds(body);
      fillBacklinks(note.path, back);
    } catch {
      if (decodeHash() === path) frame(path, h("p", { text: L().missing }));
    }
  }

  async function fillEmbeds(body) {
    for (const box of body.querySelectorAll(".note-embed")) {
      try {
        const note = await api(`/workspace/note?path=${encodeURIComponent(box.dataset.embed)}`);
        const saved = current;
        current = note; // links inside the embed resolve against the embedded note
        const inner = render(note.text);
        current = saved;
        for (const nested of inner.querySelectorAll(".note-embed")) nested.remove(); // one level only
        box.append(h("a", { class: "note-embed-title", href: noteHref(note.path), text: note.path.replace(/\.md$/, "") }), inner);
      } catch { box.remove(); }
    }
  }

  async function fillBacklinks(path, box) {
    try {
      const data = await api(`/workspace/backlinks?path=${encodeURIComponent(path)}`);
      if (!current || current.path !== path) return;
      box.replaceChildren(h("h3", { text: L().backlinks }), data.backlinks.length
        ? h("ul", {}, ...data.backlinks.map((p) => h("li", {}, h("a", { href: noteHref(p), text: p.replace(/\.md$/, "") }))))
        : h("p", { class: "dim", text: L().none }));
    } catch { box.remove(); }
  }

  function onClick(event) {
    const a = event.target.closest("a");
    if (!a) return;
    if (a.classList.contains("unresolved")) { event.preventDefault(); toast(L().unresolved); return; }
    const href = a.getAttribute("href") || "";
    // A plain Markdown link to another note: [text](other.md), relative to this note.
    if (!href.startsWith("#") && !/^[a-z]+:/i.test(href) && /\.md(#.*)?$/i.test(href) && current) {
      event.preventDefault();
      const base = current.path.split("/").slice(0, -1);
      for (const part of decodeURIComponent(href.split("#")[0]).split("/")) {
        if (part === "..") base.pop(); else if (part && part !== ".") base.push(part);
      }
      open(base.join("/"));
    }
  }

  // -- routing --------------------------------------------------------------------------------
  function decodeHash() {
    if (!location.hash.startsWith(PREFIX)) return null;
    try { return location.hash.slice(PREFIX.length).split("/").map(decodeURIComponent).join("/"); }
    catch { return null; }
  }
  function open(path) {
    if (!location.hash.startsWith(PREFIX)) returnHash = location.hash;
    location.hash = noteHref(path);
  }
  function close() {
    if (panel) panel.hidden = true;
    current = null;
    history.pushState(null, "", returnHash || "#focus");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }
  function route() {
    const path = decodeHash();
    if (path) show(path);
    else if (panel && !panel.hidden) { panel.hidden = true; current = null; }
  }
  window.addEventListener("hashchange", route);
  window.addEventListener("load", () => { if (decodeHash()) { returnHash = "#focus"; route(); } });

  return { open, isOpen: () => Boolean(panel && !panel.hidden), href: noteHref };
})();
