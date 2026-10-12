// The "Focus" view: what needs the human now, one ranking across every list. The server
// scores each item (whose move it is, priority, how close the deadline is), filling in
// priority and deadline from a fast model where nobody set them; work an agent is doing,
// finished, parked and snoozed items are left out. The top item stands alone under
// "Do this first", and every row says why it ranks and what the next step is.
// Loaded before app.js, which calls rows() from visibleRows() and note() from rowNode().
"use strict";

window.ConsoleFocus = (() => {
  function rows(items) {
    const ranked = items.filter((i) => i.focus !== null && i.focus !== undefined)
      .sort((a, b) => b.focus - a.focus || (a.due_at || "9999").localeCompare(b.due_at || "9999") ||
        (a.last_activity_at || "").localeCompare(b.last_activity_at || ""));
    if (!ranked.length) return [];
    const out = [{ group: T.focus_first }, { item: ranked[0], depth: 0 }];
    if (ranked.length > 1) out.push({ group: T.focus_next }, ...ranked.slice(1).map((item) => ({ item, depth: 0 })));
    return out;
  }

  function count(items) { return items.filter((i) => i.focus !== null && i.focus !== undefined).length; }

  // The model's reason and next step, under the row's meta line.
  function note(item) {
    const ai = item.ai;
    if (!ai || (!ai.next_step && !ai.reason)) return null;
    return h("div", { class: "ai-note" },
      ai.next_step ? h("span", { class: "next", text: `→ ${ai.next_step}` }) : null,
      ai.reason ? h("span", { class: "why", text: ai.reason }) : null);
  }

  // In the detail drawer: what the model guessed, including fields a human overrode.
  function drawerLine(item) {
    const ai = item.ai;
    if (!ai) return null;
    const bits = [`P${ai.priority}`];
    if (ai.due_at) bits.push(`${T.due} ${shortDate(ai.due_at)}`);
    return h("div", { class: "controls ai-line" },
      h("span", { class: "dim", text: `🤖 ${T.ai_guess}` }),
      h("span", { text: bits.join(" · ") }),
      ai.reason ? h("span", { class: "dim", text: ai.reason }) : null,
      ai.next_step ? h("span", { text: `→ ${ai.next_step}` }) : null);
  }

  return { rows, count, note, drawerLine };
})();
