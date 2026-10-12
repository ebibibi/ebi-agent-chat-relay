// The "Recent" view: every item, finished ones included, newest activity first, under a
// heading per day. The triage views move an item between lists as its status changes,
// which is right for deciding what to do next and wrong for "where did that thing go?".
// Here an item keeps its place in time, and each row names the list it now lives in.
// Loaded before app.js, which calls rows() from visibleRows().
"use strict";

window.ConsoleRecent = (() => {
  function when(item) { return item.last_activity_at || item.created_at || ""; }

  function dayKey(d) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }

  function dayLabel(iso, today, yesterday) {
    if (!iso) return "—";
    const d = new Date(iso);
    const key = dayKey(d);
    if (key === dayKey(today)) return T.today;
    if (key === dayKey(yesterday)) return T.yesterday;
    const opts = { month: "numeric", day: "numeric", weekday: "short" };
    if (d.getFullYear() !== today.getFullYear()) opts.year = "numeric";
    return d.toLocaleDateString(LANG, opts);
  }

  // Sorted on the parsed time, not the string: the platform and the session table do not
  // write timestamps in one format.
  function rows(items) {
    const stamp = (i) => (when(i) ? new Date(when(i)).getTime() : 0);
    const sorted = [...items].sort((a, b) => stamp(b) - stamp(a));
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    const out = [];
    let current = null;
    for (const item of sorted) {
      const label = dayLabel(when(item), today, yesterday);
      if (label !== current) { out.push({ group: label }); current = label; }
      out.push({ item, depth: 0 });
    }
    return out;
  }

  return { rows };
})();
