// Narrow any list to Ebi Workspace contexts, and optionally group a list by its entries.
// Chips are the top-level contexts the current view holds ("Work", "Home" …), matched with
// OR, plus ❔ for threads not bound yet; none on means every thread. "Group" lists a bucket
// view under one heading per bound entry, unbound last. Both are remembered per browser.
// Nothing shows without an Ebi Workspace. Loaded before app.js (ungroupedRows, viewRows).
"use strict";

window.ConsoleWsFilter = (() => {
  const NONE = "__none__";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja()
    ? { label: "🗂️ コンテキスト：", all: "すべて", none: "❔ 未判定", group: "≡ プロジェクト別", group_hint: "プロジェクトごとに見出しを付けて並べる" }
    : { label: "🗂️ Context:", all: "All", none: "❔ Unresolved", group: "≡ By project", group_hint: "List under one heading per project" };

  function load() {
    try {
      const list = JSON.parse(localStorage.getItem("wsFilter") || "[]");
      return new Set(Array.isArray(list) ? list.filter((s) => typeof s === "string" && s) : []);
    } catch { return new Set(); }
  }
  let on = load();
  let grouped = localStorage.getItem("wsGroup") === "1";
  const enabled = () => Boolean(window.ConsoleWorkspace?.available());
  const top = (item) => item.workspace_path ? item.workspace_path.split("/")[0] : NONE;

  function save(next) {
    on = next;
    localStorage.setItem("wsFilter", JSON.stringify([...on]));
    render();
  }
  const toggle = (key) => {
    const next = new Set(on);
    if (next.has(key)) next.delete(key); else next.add(key);
    save(next);
  };
  const active = () => enabled() && on.size > 0;
  const apply = (items) => active() ? items.filter((i) => on.has(top(i))) : items;

  // `items` are the view's items before this filter; counts are per top-level context.
  function bar(items) {
    if (!enabled()) return null;
    const counts = new Map();
    for (const item of items) counts.set(top(item), (counts.get(top(item)) || 0) + 1);
    const keys = [...counts.keys(), ...on].filter((k, i, all) => all.indexOf(k) === i)
      .sort((a, b) => (a === NONE) - (b === NONE) || a.localeCompare(b));
    const canGroup = !["tree", "recent", "focus"].includes(state.view);
    if (keys.length < 2 && !on.size && !grouped) return null;
    const l = L();
    const chip = (isOn, label, title, onclick) => h("button", { type: "button", class: "chip" + (isOn ? " on" : ""),
      "aria-pressed": isOn ? "true" : "false", title, onclick, text: label });
    return h("li", { class: "place-bar ws-filter-bar" }, h("span", { class: "dim", text: l.label }),
      chip(!on.size, l.all, l.all, () => save(new Set())),
      ...keys.map((k) => chip(on.has(k), `${k === NONE ? l.none : k} ${counts.get(k) || 0}`, k === NONE ? l.none : k, () => toggle(k))),
      canGroup ? chip(grouped, l.group, l.group_hint, () => {
        grouped = !grouped; localStorage.setItem("wsGroup", grouped ? "1" : "0"); render();
      }) : null);
  }

  // A bucket view's rows, under one heading per bound entry when grouping is on.
  function rows(items) {
    const flat = items.map((item) => ({ item, depth: 0 }));
    if (!enabled() || !grouped) return flat;
    const groups = new Map();
    for (const item of items) {
      const key = item.workspace_path || NONE;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const keys = [...groups.keys()].sort((a, b) => (a === NONE) - (b === NONE) || a.localeCompare(b));
    return keys.flatMap((k) => [{ group: k === NONE ? L().none : `🗂️ ${k}`, cls: "group ws-group" },
      ...groups.get(k).map((item) => ({ item, depth: 0 }))]);
  }

  return { apply, bar, rows, active };
})();
