// Declarative pages: screens that automation adds by registering data over /api/pages. The
// definitions and open counts come with every board. Everything here is drawn as text, and a
// link only ever opens in a new tab, so a page's owner cannot run code in the Console.
"use strict";

window.ConsolePages = (() => {
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    open: "開く", done: "完了", skip: "見送り", reopen: "未処理に戻す", copy: "本文をコピー", copied: "コピーしました",
    close: "閉じる", tab_open: "未処理", tab_done: "完了", tab_skipped: "見送り", empty: "ここには何もありません",
    by_person: "手動", by_auto: (who) => `自動確認（${who}）`, result: "結果を開く", heading: "📋 ページ",
  } : {
    open: "Open", done: "Done", skip: "Skip", reopen: "Back to open", copy: "Copy text", copied: "Copied",
    close: "Close", tab_open: "Open", tab_done: "Done", tab_skipped: "Skipped", empty: "Nothing here",
    by_person: "by hand", by_auto: (who) => `checked automatically (${who})`, result: "Open result", heading: "📋 Pages",
  };

  let pages = [];
  let panel = null;
  let current = null;
  let tab = "open";
  let items = [];

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

  function show(slug) {
    const page = pages.find((p) => p.slug === slug);
    if (!page) return;
    ensurePanel();
    current = page;
    tab = "open";
    items = [];
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

  async function change(item, state) {
    try {
      await api(`/pages/${encodeURIComponent(current.slug)}/items/${encodeURIComponent(item.key)}`,
        { method: "PATCH", body: { state } });
      items = items.filter((i) => i.key !== item.key);
      draw();
      refresh();
    } catch (err) { toast(err.message); }
  }

  async function copy(item) {
    try { await navigator.clipboard.writeText(item.body || item.title); toast(L().copied); }
    catch (err) { toast(err.message); }
  }

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

  function row(item) {
    const l = L();
    const labels = {
      open: current.open_label || l.open, done: current.done_label || l.done, skip: current.skip_label || l.skip,
    };
    const actions = tab === "open"
      ? [item.url ? link(labels.open, item.url, "btn primary page-open") : null,
         item.body ? button(l.copy, () => copy(item)) : null,
         button(labels.done, () => change(item, "done")),
         button(labels.skip, () => change(item, "skipped"))]
      : [item.result_url ? link(l.result, item.result_url, "btn") : null,
         button(l.reopen, () => change(item, "open"))];
    return h("li", { class: "page-item", "data-key": item.key },
      item.title ? h("div", { class: "page-item-title", text: item.title }) : null,
      item.body ? h("div", { class: "page-item-body", text: item.body }) : null,
      h("div", { class: "page-item-meta dim", text: [
        when(item.created_at), item.source, tab === "open" ? "" : resolution(item),
      ].filter(Boolean).join(" · ") }),
      h("div", { class: "page-item-actions" }, actions));
  }

  function tabButton(key, text) {
    return h("button", {
      type: "button", class: "page-tab" + (tab === key ? " on" : ""), "aria-pressed": tab === key ? "true" : "false",
      text, onclick: () => { tab = key; items = []; draw(); load(); },
    });
  }

  function draw() {
    if (!panel || !current) return;
    const l = L();
    const count = tab === "open" && current.open_count ? ` · ${current.open_count}` : "";
    panel.replaceChildren(h("div", { class: "ws-shell page-shell" },
      h("div", { class: "ws-head" },
        h("h1", { text: current.title + count }),
        button(l.close, hide)),
      h("div", { class: "page-tabs" },
        tabButton("open", l.tab_open), tabButton("done", l.tab_done), tabButton("skipped", l.tab_skipped)),
      items.length
        ? h("ul", { class: "page-items" }, items.map(row))
        : h("p", { class: "empty page-empty", text: l.empty })));
    // Redrawing removes the focused button; keep focus inside so Escape still closes the panel.
    if (!panel.hidden && !panel.contains(document.activeElement)) panel.focus();
  }

  return { onBoard, nav, show, hide };
})();
