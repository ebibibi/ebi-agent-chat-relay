// The "Focus" view: what the human can do now, one ranking across every list. Two axes
// stay apart. Importance (the server's score: whose move it is, priority, deadline) orders
// the rows; readiness decides where they go. Only work that can be done right now ranks
// under "Do this first" and "Then"; anything waiting (for a time, a person, a job) goes to
// "Waiting", soonest first, and moves up by itself once the time it named has come.
// Priority, deadline and readiness come from a fast model where nobody set them.
// Loaded before app.js, which calls rows() from visibleRows() and note() from rowNode().
"use strict";

window.ConsoleFocus = (() => {
  const ranked = (i) => i.focus !== null && i.focus !== undefined;
  const byImportance = (a, b) => b.focus - a.focus ||
    (a.due_at || "9999").localeCompare(b.due_at || "9999") ||
    (a.last_activity_at || "").localeCompare(b.last_activity_at || "");
  const readyAt = (i) => (i.ai && i.ai.ready_at) || "9999";

  function rows(items) {
    const now = items.filter((i) => ranked(i) && i.actionable !== false).sort(byImportance);
    const later = items.filter((i) => ranked(i) && i.actionable === false)
      .sort((a, b) => readyAt(a).localeCompare(readyAt(b)) || byImportance(a, b));
    const out = [];
    const add = (label, list) => {
      if (list.length) out.push({ group: label }, ...list.map((item) => ({ item, depth: 0 })));
    };
    add(T.focus_first, now.slice(0, 1));
    add(T.focus_next, now.slice(1));
    add(T.focus_waiting, later);
    return out;
  }

  function count(items) { return items.filter((i) => ranked(i) && i.actionable !== false).length; }

  function waitText(ai) {
    const when = ai.ready_at ? T.focus_from(shortDate(ai.ready_at)) : "";
    return `⏳ ${[when, ai.waiting_for].filter(Boolean).join(" · ") || T.focus_waiting}`;
  }

  // Under the row's meta line: what it waits for, or the next step and why it ranks.
  function note(item) {
    const ai = item.ai;
    if (!ai) return null;
    return h("div", { class: "ai-note" + (item.actionable === false ? " waiting" : "") },
      item.actionable === false ? h("span", { class: "wait", text: waitText(ai) }) : null,
      ai.next_step ? h("span", { class: "next", text: `→ ${ai.next_step}` }) : null,
      ai.reason ? h("span", { class: "why", text: ai.reason }) : null);
  }

  // In the detail drawer: what the model guessed, including fields a human overrode.
  function drawerLine(item) {
    const ai = item.ai;
    if (!ai) return null;
    const bits = [`P${ai.priority}`];
    if (ai.due_at) bits.push(`${T.due} ${shortDate(ai.due_at)}`);
    bits.push(ai.ready === false ? waitText(ai) : `▶ ${T.focus_ready}`);
    return h("div", { class: "controls ai-line" },
      h("span", { class: "dim", text: `🤖 ${T.ai_guess}` }),
      h("span", { text: bits.join(" · ") }),
      ai.reason ? h("span", { class: "dim", text: ai.reason }) : null,
      ai.next_step ? h("span", { text: `→ ${ai.next_step}` }) : null);
  }

  return { rows, count, note, drawerLine };
})();
