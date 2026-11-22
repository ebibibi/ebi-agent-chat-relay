// Declarative pages: screens that automation adds by registering data over /api/pages. The
// definitions and open counts come with every board. Everything here is drawn as text, and a
// link only ever opens in a new tab, so a page's owner cannot run code in the Console.
//
// A page doubles as a manual posting queue for any network (#1518): an item may name where it
// goes (a free label), carry images or videos, and declare a character limit. Nothing here
// knows a network by name.
"use strict";

window.ConsolePages = (() => {
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    open: "開く", done: "完了", skip: "見送り", reopen: "未処理に戻す", copy: "本文をコピー", copied: "コピーしました",
    close: "閉じる", tab_open: "未処理", tab_done: "完了", tab_skipped: "見送り", empty: "ここには何もありません",
    by_person: "手動", by_auto: (who) => `自動確認（${who}）`, result: "結果を開く", heading: "📋 ページ",
    all: "すべて", no_target: "投稿先なし", targets: "投稿先で絞り込む",
    over: (n) => `${n} 超過`, save: "保存", share: "共有", play: "再生", result_url: "投稿URL（任意）",
    bad_url: "投稿URLは http(s) で始まるリンクにしてください", attachments: "添付",
  } : {
    open: "Open", done: "Done", skip: "Skip", reopen: "Back to open", copy: "Copy text", copied: "Copied",
    close: "Close", tab_open: "Open", tab_done: "Done", tab_skipped: "Skipped", empty: "Nothing here",
    by_person: "by hand", by_auto: (who) => `checked automatically (${who})`, result: "Open result", heading: "📋 Pages",
    all: "All", no_target: "No target", targets: "Filter by target",
    over: (n) => `${n} over`, save: "Save", share: "Share", play: "Play", result_url: "Post URL (optional)",
    bad_url: "The post URL must start with http(s)", attachments: "Attachments",
  };
  const STORED = "/console/api/pages/";

  let pages = [];
  let panel = null;
  let current = null;
  let tab = "open";
  let items = [];
  let target = null; // null = every target
  const blobs = new Map(); // stored image URL -> Promise<Blob>; videos are too large to keep

  function onBoard(list) {
    pages = Array.isArray(list) ? list : [];
    if (!panel || panel.hidden || !current) return;
    const fresh = pages.find((p) => p.slug === current.slug);
    if (!fresh) { hide(); return; }
    const changed = fresh.open_count !== current.open_count;
    current = fresh;
    // Automation may have resolved something while the panel was open.
    if (changed) load();
  }

  function nav() {
    if (!pages.length) return null;
    return h("div", { class: "nav-pages" },
      h("div", { class: "sep" }),
      h("div", { class: "dim nav-heading", text: L().heading }),
      ...pages.map((p) => h("button", {
        type: "button", class: "nav-page", onclick: () => show(p.slug),
      }, h("span", { text: p.title }), h("span", {
        class: "count" + (p.open_count ? " hot" : ""), text: p.open_count ? String(p.open_count) : "",
      }))));
  }

  function ensurePanel() {
    if (panel) return;
    panel = h("section", { id: "page-panel", class: "workspace-panel page-panel", role: "dialog", "aria-modal": "true", tabindex: "-1" });
    panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); hide(); } });
    panel.addEventListener("click", (e) => { if (e.target === panel) hide(); });
    document.body.append(panel);
  }

  const targetKey = (slug) => `page-target:${slug}`;

  function show(slug) {
    const page = pages.find((p) => p.slug === slug);
    if (!page) return;
    ensurePanel();
    current = page;
    tab = "open";
    items = [];
    target = localStorage.getItem(targetKey(slug));
    panel.hidden = false;
    panel.setAttribute("aria-label", page.title);
    $("nav").classList.remove("open");
    draw();
    panel.focus();
    load();
  }

  function hide() { if (panel) panel.hidden = true; }

  async function load() {
    const slug = current.slug;
    const wanted = tab;
    try {
      const data = await api(`/pages/${encodeURIComponent(slug)}/items?state=${wanted}`);
      // A tab or page changed while this was on its way: the newer request will draw.
      if (current?.slug !== slug || tab !== wanted) return;
      items = data.items || [];
      draw();
    } catch (err) { toast(err.message); }
  }

  async function change(item, state, resultUrl = "") {
    try {
      const body = resultUrl ? { state, result_url: resultUrl } : { state };
      await api(`/pages/${encodeURIComponent(current.slug)}/items/${encodeURIComponent(item.key)}`,
        { method: "PATCH", body });
      items = items.filter((i) => i.key !== item.key);
      draw();
      refresh();
    } catch (err) { toast(err.message); }
  }

  async function copy(item) {
    try { await navigator.clipboard.writeText(item.body || item.title); toast(L().copied); }
    catch (err) { toast(err.message); }
  }

  // -- attachments ----------------------------------------------------------------------------
  const stored = (a) => typeof a.url === "string" && a.url.startsWith(STORED);
  const icon = (kind) => kind === "image" ? "🖼️" : kind === "video" ? "🎬" : "📎";

  function blobOf(a) {
    const url = a.url;
    if (a.kind === "video") return window.ConsolePreview.fetchBlob(url);
    if (!blobs.has(url)) {
      const pending = window.ConsolePreview.fetchBlob(url);
      blobs.set(url, pending);
      pending.catch(() => blobs.delete(url));
    }
    return blobs.get(url);
  }

  // A data: URL, not a blob: one: the Console's CSP allows img-src 'self' data: only.
  const dataUrl = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });

  async function save(a) {
    try {
      const href = URL.createObjectURL(await blobOf(a));
      const link = h("a", { href, download: a.name });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10000);
    } catch (err) { toast(err.message); }
  }

  function imageTile(a) {
    const tile = h("button", { type: "button", class: "page-thumb", title: a.name, "aria-label": a.name },
      h("span", { class: "page-thumb-wait", text: icon("image") }));
    blobOf(a).then(dataUrl).then((src) => {
      tile.replaceChildren(h("img", { src, alt: a.name }));
    }).catch(() => tile.replaceChildren(h("span", { class: "page-thumb-wait", text: "⚠️" })));
    tile.addEventListener("click", () => {
      const lightbox = window.ConsoleLightbox;
      if (lightbox) lightbox(a.name, blobOf(a).then(dataUrl), () => save(a));
    });
    return tile;
  }

  // Videos are fetched only when asked for: a phone should not download every clip on open.
  function videoTile(a) {
    const tile = h("button", { type: "button", class: "page-thumb page-video", title: a.name },
      h("span", { text: `▶ ${L().play}` }), h("span", { class: "page-thumb-name", text: a.name }));
    tile.addEventListener("click", async () => {
      try {
        const src = URL.createObjectURL(await blobOf(a));
        const video = h("video", { class: "page-player", src, controls: "", playsinline: "", preload: "metadata" });
        tile.replaceWith(video);
        video.play().catch(() => { /* a click is not always enough to autoplay */ });
      } catch (err) { toast(err.message); }
    });
    return tile;
  }

  function attachment(a) {
    if (!stored(a)) {
      return h("div", { class: "page-file" },
        h("a", { class: "page-file-link", href: a.url, target: "_blank", rel: "noopener noreferrer",
          text: `${icon(a.kind)} ${a.name} ↗` }));
    }
    return h("div", { class: "page-file" },
      a.kind === "video" ? videoTile(a) : a.kind === "image" ? imageTile(a)
        : h("span", { class: "page-file-name", text: `${icon(a.kind)} ${a.name}` }),
      h("button", { type: "button", class: "btn page-save", text: `⬇ ${L().save}`,
        "aria-label": `${L().save}: ${a.name}`, onclick: () => save(a) }));
  }

  // The system share sheet hands text and files to an app (a phone's posting app) in one step.
  function canShare() { return typeof navigator.share === "function" && typeof navigator.canShare === "function"; }

  async function share(item) {
    try {
      const files = await Promise.all((item.attachments || []).filter(stored).map(async (a) => {
        const blob = await blobOf(a);
        return new File([blob], a.name, { type: blob.type });
      }));
      const data = { text: item.body || item.title };
      if (files.length && navigator.canShare({ files })) data.files = files;
      await navigator.share(data);
    } catch (err) { if (err.name !== "AbortError") toast(err.message); }
  }

  // -- rows -----------------------------------------------------------------------------------
  const button = (text, onclick, cls = "btn") => h("button", { type: "button", class: cls, text, onclick });
  const link = (text, url, cls) => h("a", { class: cls, href: url, target: "_blank", rel: "noopener noreferrer", text });
  const when = (iso) => {
    const moment = new Date(iso);
    return Number.isNaN(moment.getTime()) ? "" : moment.toLocaleString(ja() ? "ja-JP" : "en-US",
      { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  function resolution(item) {
    const l = L();
    const who = item.resolved_by === "person" ? l.by_person : item.resolved_by ? l.by_auto(item.resolved_by) : "";
    return [when(item.resolved_at), who].filter(Boolean).join(" · ");
  }

  function counter(item) {
    if (!item.limit) return null;
    const over = item.length - item.limit;
    return h("span", { class: "page-count" + (over > 0 ? " over" : ""),
      text: `${item.length} / ${item.limit}` + (over > 0 ? ` · ⚠ ${L().over(over)}` : "") });
  }

  function openActions(item, labels) {
    const l = L();
    const posted = h("input", { type: "url", class: "page-result", placeholder: l.result_url,
      "aria-label": l.result_url, inputmode: "url", autocomplete: "off" });
    const done = () => {
      const url = posted.value.trim();
      if (url && !/^https?:\/\/\S+$/i.test(url)) { toast(l.bad_url); posted.focus(); return; }
      change(item, "done", url);
    };
    posted.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); done(); } });
    const files = (item.attachments || []).some(stored);
    return [item.url ? link(labels.open, item.url, "btn primary page-open") : null,
      item.body ? button(l.copy, () => copy(item)) : null,
      canShare() && (item.body || files) ? button(`↗ ${l.share}`, () => share(item), "btn page-share") : null,
      posted,
      button(labels.done, done, "btn page-done"),
      button(labels.skip, () => change(item, "skipped"))];
  }

  function row(item) {
    const l = L();
    const labels = {
      open: current.open_label || l.open, done: current.done_label || l.done, skip: current.skip_label || l.skip,
    };
    const actions = tab === "open"
      ? openActions(item, labels)
      : [item.result_url ? link(l.result, item.result_url, "btn") : null,
         button(l.reopen, () => change(item, "open"))];
    const files = item.attachments || [];
    return h("li", { class: "page-item", "data-key": item.key },
      item.target || item.title ? h("div", { class: "page-item-head" },
        item.target ? h("span", { class: "page-target", text: item.target }) : null,
        item.title ? h("span", { class: "page-item-title", text: item.title }) : null) : null,
      item.body ? h("div", { class: "page-item-body", text: item.body }) : null,
      files.length ? h("div", { class: "page-files", "aria-label": l.attachments }, files.map(attachment)) : null,
      h("div", { class: "page-item-meta dim" },
        counter(item),
        h("span", { text: [when(item.created_at), item.source, tab === "open" ? "" : resolution(item)]
          .filter(Boolean).join(" · ") })),
      h("div", { class: "page-item-actions" }, actions));
  }

  function tabButton(key, text) {
    return h("button", {
      type: "button", class: "page-tab" + (tab === key ? " on" : ""), "aria-pressed": tab === key ? "true" : "false",
      text, onclick: () => { tab = key; items = []; draw(); load(); },
    });
  }

  // -- the target filter ----------------------------------------------------------------------
  function targetCounts() {
    const counts = new Map();
    for (const item of items) counts.set(item.target || "", (counts.get(item.target || "") || 0) + 1);
    return counts;
  }

  function choose(value) {
    target = value;
    if (value === null) localStorage.removeItem(targetKey(current.slug));
    else localStorage.setItem(targetKey(current.slug), value);
    draw();
  }

  function targetBar(counts, active) {
    // Only worth a row when the list holds more than one target.
    if (counts.size < 2) return null;
    const l = L();
    const chip = (value, text, n) => h("button", {
      type: "button", class: "page-chip" + (active === value ? " on" : ""),
      "aria-pressed": active === value ? "true" : "false", onclick: () => choose(value),
    }, h("span", { text }), h("span", { class: "count", text: String(n) }));
    const named = [...counts.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
    return h("div", { class: "page-targets", role: "group", "aria-label": l.targets },
      chip(null, l.all, items.length),
      ...named.map((value) => chip(value, value, counts.get(value))),
      counts.has("") ? chip("", l.no_target, counts.get("")) : null);
  }

  function draw() {
    if (!panel || !current) return;
    const l = L();
    const counts = targetCounts();
    // A remembered target with nothing in this list shows everything rather than an empty page.
    const active = target !== null && counts.has(target) && counts.size > 1 ? target : null;
    const shown = active === null ? items : items.filter((i) => (i.target || "") === active);
    const count = tab === "open" && current.open_count ? ` · ${current.open_count}` : "";
    panel.replaceChildren(h("div", { class: "ws-shell page-shell" },
      h("div", { class: "ws-head" },
        h("h1", { text: current.title + count }),
        button(l.close, hide)),
      h("div", { class: "page-tabs" },
        tabButton("open", l.tab_open), tabButton("done", l.tab_done), tabButton("skipped", l.tab_skipped)),
      targetBar(counts, active),
      shown.length
        ? h("ul", { class: "page-items" }, shown.map(row))
        : h("p", { class: "empty page-empty", text: l.empty })));
    // Redrawing removes the focused button; keep focus inside so Escape still closes the panel.
    if (!panel.hidden && !panel.contains(document.activeElement)) panel.focus();
  }

  return { onBoard, nav, show, hide };
})();
