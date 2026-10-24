// The "Focus" view: what the human can do now, one ranking across every list. Three axes
// stay apart. Importance (the server's score: priority, deadline, whose move it is) orders
// the rows. Readiness decides whether a row is here at all: anything waiting (for a time, a
// person, a job) goes to "Waiting", soonest first, and comes back once its time has come.
// Place: work bound to a location or device ("home", "office", "work PC") shows a 📍 tag,
// and once the human says where they are — one place or several (home and the work PC) —
// work bound to none of them moves to its own group, so "Do this first" is always
// something that can be done here and now.
// Time: the model also estimates how long your part takes before the agent can carry on —
// ⚡ quick (a yes/no, a choice), ⏱ short (read or check something), 🧘 deep (real work).
// Each row shows it; the bar above the list narrows Focus to one bucket, and the
// "work through" button there runs that bucket one thread at a time (zen.js).
// Priority, deadline, readiness and place come from a fast model where nobody set them.
// Readiness can be overridden ("can do now" / "waiting"); on a thread the override lasts
// until the conversation moves again, then the model's fresh guess takes over.
// Loaded before app.js, which calls rows() from visibleRows() and note() from rowNode().
"use strict";

window.ConsoleFocus = (() => {
  const ranked = (i) => i.focus !== null && i.focus !== undefined;
  const byImportance = (a, b) => b.focus - a.focus ||
    (a.due_at || "9999").localeCompare(b.due_at || "9999") ||
    (a.last_activity_at || "").localeCompare(b.last_activity_at || "");
  const readyAt = (i) => (i.ai && i.ai.ready_at) || "9999";

  // Where the human is now: any number of places, matched with OR; none means "don't filter
  // by place". Remembered per browser; an older single-place setting carries over.
  function loadHere() {
    const raw = localStorage.getItem("here") || "";
    try {
      const list = JSON.parse(raw);
      if (Array.isArray(list)) return new Set(list.filter((p) => typeof p === "string" && p));
    } catch { /* a plain place name from before */ }
    return new Set(raw ? [raw] : []);
  }
  let here = loadHere();
  const elsewhere = (i) => Boolean(here.size && i.place && !here.has(i.place));
  function saveHere(next) {
    here = next;
    localStorage.setItem("here", JSON.stringify([...here]));
    render();
  }
  const toggleHere = (place) => {
    const next = new Set(here);
    if (next.has(place)) next.delete(place); else next.add(place);
    saveHere(next);
  };

  // How long your part takes: one bucket at a time, or all. Remembered per browser.
  const EFFORTS = ["quick", "short", "deep"];
  const EFFORT_ICON = { quick: "⚡", short: "⏱", deep: "🧘" };
  let effort = EFFORTS.includes(localStorage.getItem("focusEffort")) ? localStorage.getItem("focusEffort") : "";
  const effortOk = (i) => !effort || i.effort === effort;
  const effortChosen = () => effort;
  const effortLabel = (e) => `${EFFORT_ICON[e]} ${T.effort[e]}`;
  function setEffort(next) {
    effort = next;
    localStorage.setItem("focusEffort", next);
    render();
  }

  // "⚡ 2 min" on a row: the model's estimate of the human's part.
  function effortTag(item) {
    if (!item.effort) return null;
    const minutes = item.ai && item.ai.minutes;
    return h("span", { class: `tag effort ${item.effort}`, title: T.effort_hint,
      text: `${EFFORT_ICON[item.effort]} ${minutes ? T.effort_min(minutes) : T.effort[item.effort]}` });
  }

  function effortBar(items) {
    const here = items.filter((i) => ranked(i) && i.actionable !== false && !elsewhere(i));
    const n = (e) => here.filter((i) => i.effort === e).length;
    const chip = (key, label) => h("button", { type: "button", class: "chip" + (effort === key ? " on" : ""),
      "aria-pressed": effort === key ? "true" : "false", onclick: () => setEffort(key), text: label });
    return h("li", { class: "effort-bar" }, h("span", { class: "dim", text: T.effort_bar, title: T.effort_hint }),
      chip("", T.effort_all),
      ...EFFORTS.map((e) => chip(e, `${effortLabel(e)} ${n(e)}`)),
      window.ConsoleZen ? window.ConsoleZen.startButton() : null);
  }

  function placeBar() {
    const known = window.ConsolePlaces.names();
    for (const p of here) if (!known.includes(p)) known.push(p);
    const chip = (on, label, onclick) => h("button", { type: "button", class: "chip" + (on ? " on" : ""),
      "aria-pressed": on ? "true" : "false", onclick, text: label });
    return h("li", { class: "place-bar" }, h("span", { class: "dim", text: T.here, title: T.here_hint }),
      chip(!here.size, T.anywhere, () => saveHere(new Set())),
      ...known.map((p) => chip(here.has(p), `📍 ${p}`, () => toggleHere(p))),
      h("button", { type: "button", class: "chip ghost", title: T.place_manage,
        onclick: () => window.ConsolePlaces.manage(), text: `⚙ ${T.place_manage}` }));
  }

  // The guide card above the list (server: guide.py): the next move and why, decisions to
  // take together, a plain "nothing needs you" when that is true, and — only with an Ebi
  // Workspace — finished work whose records were not updated. Without a Workspace the
  // server sends drift: null and that part simply does not exist.
  let guide = null;
  const setGuide = (next) => { guide = next; };
  const link = (id, label, cls) => h("button", { type: "button", class: "guide-link" + (cls ? " " + cls : ""),
    onclick: () => openItem(id), text: label });

  function nextMove(g, byId) {
    const pick = g.next.find((n) => !elsewhere(byId.get(n.id) || n));
    if (!pick) {
      const counts = T.guide_counts(g.counts);
      return [
        h("div", { class: "guide-calm", text: g.calm ? T.guide_calm : T.guide_elsewhere(g.next.length) }),
        counts ? h("div", { class: "dim", text: counts }) : null,
        g.calm && (g.counts.running || g.counts.waiting) ? h("div", { class: "dim", text: T.guide_calm_more }) : null,
      ];
    }
    const reasons = pick.reasons.map((r) => T.guide_reasons[r]).filter(Boolean);
    return [
      link(pick.id, pick.title || T.guide_open, "guide-next"),
      reasons.length ? h("div", { class: "guide-reasons" },
        ...reasons.map((r) => h("span", { class: "tag", text: r }))) : null,
      pick.next_step ? h("div", { class: "next", text: `→ ${pick.next_step}` }) : null,
      pick.why ? h("div", { class: "dim", text: pick.why }) : null,
      pick.unblocks ? h("div", { class: "guide-unblocks", text: T.guide_unblocks }) : null,
    ];
  }

  function section(title, entries) {
    if (!entries.length) return null;
    return h("div", { class: "guide-section" }, h("div", { class: "guide-head", text: title }),
      h("ul", { class: "guide-list" }, ...entries.map((e) => h("li", {}, e))));
  }

  function card(items) {
    if (!guide || !Array.isArray(guide.next)) return null;
    const byId = new Map(items.map((i) => [i.id, i]));
    const batches = (guide.batches || []).map((b) => link(b.root_id, T.guide_batch(b.title || b.root_id, b.items.length)));
    const drift = (guide.drift || []).map((d) => h("span", {}, link(d.id, d.title || d.id),
      h("span", { class: "dim", text: ` — ${d.kind === "task_open" ? T.guide_task_open(d.task) : T.guide_record_stale}` })));
    return h("li", { class: "guide-card" },
      h("div", { class: "guide-head", text: T.guide_title }),
      ...nextMove(guide, byId),
      section(T.guide_batches, batches),
      section(T.guide_drift, drift));
  }

  function rows(items) {
    const live = items.filter((i) => ranked(i) && effortOk(i));
    const now = live.filter((i) => i.actionable !== false && !elsewhere(i)).sort(byImportance);
    const away = live.filter((i) => i.actionable !== false && elsewhere(i)).sort(byImportance);
    const later = live.filter((i) => i.actionable === false)
      .sort((a, b) => readyAt(a).localeCompare(readyAt(b)) || byImportance(a, b));
    const out = [];
    const guideCard = card(items);
    if (guideCard) out.push({ node: guideCard });
    out.push({ node: placeBar() });
    out.push({ node: effortBar(items) });
    // Ebi Workspace (focusws.js): today's plan, its ranked next actions after the AI work
    // that is ready here, and its waiting-for list beside the waiting threads.
    const ws = window.ConsoleFocusWorkspace;
    const node = (n) => { if (n) out.push({ node: n }); };
    node(ws?.planCard());
    const add = (label, list) => {
      if (list.length) out.push({ group: label }, ...list.map((item) => ({ item, depth: 0 })));
    };
    add(T.focus_first, now.slice(0, 1));
    add(T.focus_next, now.slice(1));
    node(ws?.tasksCard());
    node(ws?.stalledCard());
    add(T.focus_elsewhere, away);
    add(T.focus_waiting, later);
    node(ws?.waitingCard());
    return out;
  }

  function count(items) {
    return items.filter((i) => ranked(i) && i.actionable !== false && !elsewhere(i)).length;
  }

  function waitText(ai) {
    const when = ai.ready_at ? T.focus_from(shortDate(ai.ready_at)) : "";
    return `⏳ ${[when, ai.waiting_for].filter(Boolean).join(" · ") || T.focus_waiting}`;
  }

  const yours = (item) => item.readiness_by === "you";
  const setReadiness = (item, readiness) => patch(item.id, { readiness });

  // The ⏳/▶ button in the detail header: what Focus believes, one click to flip it.
  // Dashed like the 📍 button while it is the model's guess. Only for the human's own work.
  function readyToggle(item) {
    if (!ranked(item)) return null;
    const waiting = item.actionable === false;
    return h("button", { class: "btn d-ready" + (yours(item) ? " on" : " ai"), type: "button",
      title: yours(item) ? T.ready_yours : T.ready_hint, "aria-pressed": waiting ? "true" : "false",
      onclick: () => setReadiness(item, waiting ? "now" : "waiting"),
      text: waiting ? `⏳ ${T.ready_wait}` : `▶ ${T.ready_now}` });
  }

  // The 📍 tag on a row's meta line, in every list.
  function placeTag(item) {
    if (!item.place) return null;
    return h("span", { class: "tag place" + (item.place_by === "ai" ? " ai" : "") + (elsewhere(item) ? " away" : ""),
      title: item.place_by === "ai" ? T.ai_set : T.place, text: `📍 ${item.place}` });
  }

  // Under the row's meta line: what it waits for, or the next step and why it ranks.
  // A waiting row carries a "can do now" button, for when the model misread the wait.
  function note(item) {
    const ai = item.ai || (yours(item) ? {} : null);
    if (!ai) return null;
    const waiting = item.actionable === false;
    const wait = yours(item) ? `⏳ ${T.ready_wait} 👤` : waitText(ai);
    return h("div", { class: "ai-note" + (waiting ? " waiting" : "") },
      waiting ? h("span", { class: "wait", title: yours(item) ? T.ready_yours : "", text: wait }) : null,
      waiting ? h("button", { type: "button", class: "chip ready-now", title: T.ready_hint,
        onclick: (e) => { e.stopPropagation(); setReadiness(item, "now"); }, text: `▶ ${T.ready_now}` }) : null,
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
    if (ai.minutes) bits.push(`${EFFORT_ICON[item.effort] || "⏱"} ${T.effort_min(ai.minutes)}`);
    return h("div", { class: "controls ai-line" },
      h("span", { class: "dim", text: `🤖 ${T.ai_guess}` }),
      h("span", { text: bits.join(" · ") }),
      ai.reason ? h("span", { class: "dim", text: ai.reason }) : null,
      ai.next_step ? h("span", { text: `→ ${ai.next_step}` }) : null,
      yours(item) ? h("button", { type: "button", class: "btn", title: T.ready_yours,
        onclick: () => setReadiness(item, null), text: `🤖 ${T.ready_back_ai}` }) : null);
  }

  return { rows, count, note, drawerLine, placeTag, setGuide, readyToggle,
    elsewhere, byImportance, effortOk, effortChosen, effortLabel, effortTag };
})();
