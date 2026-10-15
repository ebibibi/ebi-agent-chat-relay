// Narrow any list to the statuses the human wants to act on now: "your reply", "to review",
// "your task", "completion candidates"… Several can be on at once (matched with OR); none
// means every status. Remembered per browser, like the place you are at.
// The chips count what the current view holds before this filter, so a status with nothing
// in it is not offered — except one already switched on, which must stay clickable to undo.
// Loaded before app.js, which calls apply() and bar() from visibleRows().
"use strict";

window.ConsoleStatusFilter = (() => {
  // Whose move it is first, then the agent's states, then what is parked or finished.
  const ORDER = ["waiting", "review", "action", "error", "candidate", "running", "idle", "todo", "someday", "done"];

  function load() {
    try {
      const list = JSON.parse(localStorage.getItem("statusFilter") || "[]");
      return new Set(Array.isArray(list) ? list.filter((s) => ORDER.includes(s)) : []);
    } catch { return new Set(); }
  }
  let on = load();
  function save(next) {
    on = next;
    localStorage.setItem("statusFilter", JSON.stringify([...on]));
    render();
  }
  const toggle = (status) => {
    const next = new Set(on);
    if (next.has(status)) next.delete(status); else next.add(status);
    save(next);
  };

  const apply = (items) => on.size ? items.filter((i) => on.has(i.status)) : items;

  // `rows` are the view's rows before filtering; an item listed twice is counted once.
  function bar(rows) {
    const counts = new Map();
    const seen = new Set();
    for (const r of rows) {
      if (!r.item || seen.has(r.item.id)) continue;
      seen.add(r.item.id);
      counts.set(r.item.status, (counts.get(r.item.status) || 0) + 1);
    }
    const shown = ORDER.filter((s) => counts.has(s) || on.has(s));
    if (shown.length < 2 && !on.size) return null;
    const chip = (active, label, title, onclick) => h("button", { type: "button", class: "chip" + (active ? " on" : ""),
      "aria-pressed": active ? "true" : "false", title, onclick, text: label });
    return h("li", { class: "place-bar status-bar" }, h("span", { class: "dim", text: T.status_filter }),
      chip(!on.size, T.status_all, T.status_all, () => save(new Set())),
      ...shown.map((s) => chip(on.has(s), `${STATUS_ICON[s] || "·"} ${T.st[s]} ${counts.get(s) || 0}`,
        T.st[s], () => toggle(s))));
  }

  return { apply, bar, active: () => on.size > 0 };
})();
