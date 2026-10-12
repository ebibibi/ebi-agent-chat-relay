// The "Focus" view: what the human can do now, one ranking across every list. Three axes
// stay apart. Importance (the server's score: priority, deadline, whose move it is) orders
// the rows. Readiness decides whether a row is here at all: anything waiting (for a time, a
// person, a job) goes to "Waiting", soonest first, and comes back once its time has come.
// Place: work bound to a location or device ("home", "office", "work PC") shows a 📍 tag,
// and once the human says where they are, work bound elsewhere moves to its own group, so
// "Do this first" is always something that can be done here and now.
// Priority, deadline, readiness and place come from a fast model where nobody set them.
// Loaded before app.js, which calls rows() from visibleRows() and note() from rowNode().
"use strict";

window.ConsoleFocus = (() => {
  const ranked = (i) => i.focus !== null && i.focus !== undefined;
  const byImportance = (a, b) => b.focus - a.focus ||
    (a.due_at || "9999").localeCompare(b.due_at || "9999") ||
    (a.last_activity_at || "").localeCompare(b.last_activity_at || "");
  const readyAt = (i) => (i.ai && i.ai.ready_at) || "9999";

  // Where the human is now; "" means "don't filter by place". Remembered per browser.
  let here = localStorage.getItem("here") || "";
  const elsewhere = (i) => Boolean(here && i.place && i.place !== here);
  function setHere(place) {
    here = place;
    localStorage.setItem("here", place);
    render();
  }

  function placeBar() {
    const known = window.ConsolePlaces.names();
    if (here && !known.includes(here)) known.push(here);
    const chip = (value, label) => h("button", { type: "button", class: "chip" + (here === value ? " on" : ""),
      "aria-pressed": here === value ? "true" : "false", onclick: () => setHere(value), text: label });
    return h("li", { class: "place-bar" }, h("span", { class: "dim", text: T.here }),
      chip("", T.anywhere), ...known.map((p) => chip(p, `📍 ${p}`)),
      h("button", { type: "button", class: "chip ghost", title: T.place_manage,
        onclick: () => window.ConsolePlaces.manage(), text: `⚙ ${T.place_manage}` }));
  }

  function rows(items) {
    const live = items.filter(ranked);
    const now = live.filter((i) => i.actionable !== false && !elsewhere(i)).sort(byImportance);
    const away = live.filter((i) => i.actionable !== false && elsewhere(i)).sort(byImportance);
    const later = live.filter((i) => i.actionable === false)
      .sort((a, b) => readyAt(a).localeCompare(readyAt(b)) || byImportance(a, b));
    const out = [];
    out.push({ node: placeBar() });
    const add = (label, list) => {
      if (list.length) out.push({ group: label }, ...list.map((item) => ({ item, depth: 0 })));
    };
    add(T.focus_first, now.slice(0, 1));
    add(T.focus_next, now.slice(1));
    add(T.focus_elsewhere, away);
    add(T.focus_waiting, later);
    return out;
  }

  function count(items) {
    return items.filter((i) => ranked(i) && i.actionable !== false && !elsewhere(i)).length;
  }

  function waitText(ai) {
    const when = ai.ready_at ? T.focus_from(shortDate(ai.ready_at)) : "";
    return `⏳ ${[when, ai.waiting_for].filter(Boolean).join(" · ") || T.focus_waiting}`;
  }

  // The 📍 tag on a row's meta line, in every list.
  function placeTag(item) {
    if (!item.place) return null;
    return h("span", { class: "tag place" + (item.place_by === "ai" ? " ai" : "") + (elsewhere(item) ? " away" : ""),
      title: item.place_by === "ai" ? T.ai_set : T.place, text: `📍 ${item.place}` });
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
    if (ai.place) bits.push(`📍 ${ai.place}`);
    return h("div", { class: "controls ai-line" },
      h("span", { class: "dim", text: `🤖 ${T.ai_guess}` }),
      h("span", { text: bits.join(" · ") }),
      ai.reason ? h("span", { class: "dim", text: ai.reason }) : null,
      ai.next_step ? h("span", { text: `→ ${ai.next_step}` }) : null);
  }

  return { rows, count, note, drawerLine, placeTag };
})();
