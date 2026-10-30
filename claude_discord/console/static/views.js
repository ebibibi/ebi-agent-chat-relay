// Saved views: a name for a rule in the filter language (query.js), listed in the menu like
// the built-in lists. A view lists every item the rule selects, across all lists — finished
// ones only when the rule asks for them (is:done, status:done …). The filter box still narrows
// it further. The list lives on the server and comes with every board, so a view saved on the
// desktop is on the phone at the next poll. Loaded before app.js (matches, viewRows, renderNav).
"use strict";

window.ConsoleViews = (() => {
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    heading: "🔖 保存ビュー", save: "🔖 ビューに保存", save_hint: "いまの絞り込みに名前を付けて、メニューに置く",
    name_prompt: "ビューの名前", query_prompt: "ルール（絞り込みの書き方）", rule: "ルール：",
    edit: "✎ 編集", remove: "🗑 削除", remove_confirm: (n) => `保存ビュー「${n}」を削除しますか？（スレッドは消えません）`,
    saved: (n) => `🔖「${n}」を保存しました`, removed: "保存ビューを削除しました",
    unknown: "⚠ 読めなかった条件：", syntax: "書き方のヘルプ",
  } : {
    heading: "🔖 Saved views", save: "🔖 Save as view", save_hint: "Name the current filter and keep it in the menu",
    name_prompt: "Name of the view", query_prompt: "Rule (filter syntax)", rule: "Rule:",
    edit: "✎ Edit", remove: "🗑 Delete", remove_confirm: (n) => `Delete the saved view "${n}"? (No thread is deleted.)`,
    saved: (n) => `🔖 Saved "${n}"`, removed: "Saved view deleted",
    unknown: "⚠ Not understood:", syntax: "Syntax help",
  };
  const SYNTAX = "tag:teams-exporter · status:waiting,review · p:<=1 · project:… · place:… · backend:codex · " +
    "origin:console · list:me · is:running|pinned|overdue|child|done · has:due|tag · -term (not) · \"phrase\"";

  let views = [];
  const current = () => views.find((v) => v.id === state.view) || null;
  const isKey = (key) => /^v[0-9a-f]{10}$/.test(key || "");
  const active = () => isKey(state.view);
  // The active view's rule, or null outside a saved view.
  const query = () => (active() ? current()?.query ?? "" : null);
  const title = () => { const v = active() && current(); return v ? `🔖 ${v.name}` : null; };

  function onBoard(list) {
    views = Array.isArray(list) ? list : [];
    // A view deleted on another device leaves this one on the default list.
    if (active() && !current()) { state.view = "focus"; localStorage.setItem("view", "focus"); }
  }

  function count(v) {
    const done = window.ConsoleQuery.wantsDone(v.query);
    const parsed = window.ConsoleQuery.parse(v.query);
    return state.items.filter((i) => (done || i.bucket !== "done") && window.ConsoleQuery.test(i, parsed)).length;
  }

  function nav() {
    if (!views.length) return null;
    return h("div", { class: "nav-views-saved" },
      h("div", { class: "sep" }),
      h("div", { class: "dim nav-heading", text: L().heading }),
      ...views.map((v) => h("button", {
        class: state.view === v.id ? "active" : "", "aria-current": state.view === v.id ? "page" : null,
        title: v.query, onclick: () => setView(v.id),
      }, h("span", { text: v.name }), h("span", { class: "count", text: String(count(v) || "") }))));
  }

  // A saved view lists its items in board order, under project headings when grouping is on.
  const rows = (items) => window.ConsoleWsFilter.rows(items);

  async function call(path, method, body) {
    try { const data = await api(path, { method, body }); onBoard(data.views); return data.views; }
    catch (err) { toast(err.message); return null; }
  }

  async function save() {
    const q = state.filter.trim();
    if (!q) return;
    const name = prompt(L().name_prompt, q.slice(0, 40));
    if (!name || !name.trim()) return;
    const before = new Set(views.map((v) => v.id));
    const list = await call("/views", "POST", { name, query: q });
    if (!list) return;
    const made = list.find((v) => !before.has(v.id));
    state.filter = "";
    $("filter").value = "";
    toast(L().saved(made ? made.name : name));
    if (made) setView(made.id); else render();
  }

  async function edit() {
    const v = current();
    if (!v) return;
    const name = prompt(L().name_prompt, v.name);
    if (name === null) return;
    const q = prompt(L().query_prompt, v.query);
    if (q === null) return;
    if (await call(`/views/${encodeURIComponent(v.id)}`, "PATCH", { name, query: q })) render();
  }

  async function remove() {
    const v = current();
    if (!v || !confirm(L().remove_confirm(v.name))) return;
    if (await call(`/views/${encodeURIComponent(v.id)}`, "DELETE")) {
      toast(L().removed);
      setView("focus");
    }
  }

  // The bar on top of the list: the active view's rule with its controls, and any term of
  // the filter box or the rule that could not be read (so a typo is not a silent "nothing").
  function bar() {
    const l = L();
    const unread = [...window.ConsoleQuery.parse(query() || "").errors, ...window.ConsoleQuery.parse(state.filter).errors];
    const parts = [];
    const v = active() && current();
    if (v) {
      parts.push(h("span", { class: "dim", text: l.rule }), h("code", { class: "view-rule", text: v.query }),
        h("button", { type: "button", class: "chip", onclick: edit, text: l.edit }),
        h("button", { type: "button", class: "chip", onclick: remove, text: l.remove }));
    }
    if (unread.length) parts.push(h("span", { class: "view-unknown", title: SYNTAX, text: `${l.unknown} ${unread.join(" ")}` }));
    return parts.length ? h("li", { class: "place-bar view-bar" }, ...parts) : null;
  }

  // The "save as view" button beside the filter box; shown while the box holds a rule.
  function syncSaveButton() {
    const btn = $("save-view");
    if (!btn) return;
    btn.textContent = L().save;
    btn.title = L().save_hint;
    btn.hidden = !state.filter.trim();
    $("filter").title = `${L().syntax}: ${SYNTAX}`;
  }

  return { onBoard, active, isKey, query, title, nav, rows, bar, save, syncSaveButton, list: () => views };
})();
