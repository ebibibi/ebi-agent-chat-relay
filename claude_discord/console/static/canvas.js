// Canvas mode (ADR-0025): the whole screen belongs to one resident secretary agent.
// Left: the widgets it placed over /api/canvas (decision cards, diffs, tables, Markdown,
// timelines, agent status, numbers), drawn here with one fixed renderer per kind — text through
// textContent, Markdown through the sanitising MD.render. Right: the secretary's conversation,
// which is the ordinary detail pane opened on its thread. The thread list is not shown.
// A decision widget is the same card secretary.js draws, answered the same way. Any other
// widget's buttons send their words to the secretary as the person's message.
// Loaded before app.js, which calls paint() from render(), onBoard() from refresh() and
// onClose() from closeItem().
"use strict";

window.ConsoleCanvas = (() => {
  const KEY = "consoleMode";
  const MODE = "canvas";
  const narrow = matchMedia("(max-width: 900px)");
  const STRINGS = {
    en: {
      mode: "Canvas", title: "🗂 Secretary", back: "← Back to the secretary", classic: "↩ Classic screen",
      start_title: "Let the secretary lay out your screen",
      start_body: "One agent keeps every thread in mind and places here what to do now. It wakes only when something changed, at most {max} times a day, and you can pause it.",
      start: "Start the secretary", starting: "Starting…", unavailable: "This deployment cannot run the secretary.",
      empty: "The secretary has not placed anything yet. Ask it on the right: \"what should I do now?\"",
      running: "⚙️ working", idle: "● waiting for changes", paused_tag: "⏸ paused",
      pause: "⏸ Pause", resume: "▶ Resume", restart: "🔄 Restart",
      restart_confirm: "Start a fresh secretary conversation? Its notes and widgets stay.",
      usage: (u, ctx) => `Auto wakes today ${u.wakes}/${u.max_wakes} · turns ${u.turns} · read ≈${tokens(u.input_tokens)} tokens (estimate)${ctx}`,
      context: (c) => ` · context ${c.pct}%`, pending: (n) => `${n} change(s) not read yet`,
      updated: (r) => `updated ${r}`, dismiss: "Remove from the screen", answered: "This request was answered or closed.",
      open: "Open the thread", chat: "💬 Secretary", no_running: "No agent is working right now.",
      states: { now: "▶", done: "✓", wait: "⏳", todo: "○" }, minutes: (m) => `${m} min`,
      before: "Before", after: "After", sent: "Sent to the secretary",
    },
    ja: {
      mode: "画面秘書", title: "🗂 秘書", back: "← 秘書に戻る", classic: "↩ 通常の画面",
      start_title: "秘書に画面を組み立ててもらう",
      start_body: "AI秘書が全部のスレッドを把握して、今やることをこの画面に並べます。変化があったときだけ動き（1日最大 {max} 回）、いつでも一時停止できます。",
      start: "秘書を起動する", starting: "起動しています…", unavailable: "この環境では秘書を動かせません。",
      empty: "秘書はまだ何も置いていません。右で「今何をすればいい？」と聞いてみてください。",
      running: "⚙️ 作業中", idle: "● 変化を待っています", paused_tag: "⏸ 一時停止中",
      pause: "⏸ 一時停止", resume: "▶ 再開", restart: "🔄 作り直す",
      restart_confirm: "秘書の会話を新しく作り直しますか？ メモと画面の部品は残ります。",
      usage: (u, ctx) => `今日の自動更新 ${u.wakes}/${u.max_wakes}回 · 応答 ${u.turns}回 · 読み込み 約${tokens(u.input_tokens)}トークン（推定）${ctx}`,
      context: (c) => ` · 文脈 ${c.pct}%`, pending: (n) => `未読の変化 ${n}件`,
      updated: (r) => `${r}に更新`, dismiss: "画面から外す", answered: "この依頼は回答済みか、閉じられています。",
      open: "スレッドを開く", chat: "💬 秘書と話す", no_running: "いまAIが進めている作業はありません。",
      states: { now: "▶", done: "✓", wait: "⏳", todo: "○" }, minutes: (m) => `${m}分`,
      before: "変更前", after: "変更後", sent: "秘書に伝えました",
    },
  };
  const S = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  let data = null; // { widgets, secretary } from /canvas
  let lastSig = "";
  let starting = false;
  let chatOpen = false; // narrow screens: the conversation covers the canvas while open

  const on = () => localStorage.getItem(KEY) === MODE;
  const secretaryId = () => (data && data.secretary && data.secretary.item_id) || null;

  function enter() {
    localStorage.setItem(KEY, MODE);
    state.open = null;
    load().then(render);
  }
  function leave() {
    localStorage.setItem(KEY, "classic");
    document.body.classList.remove("canvas", "canvas-chat");
    if (state.view === window.ConsoleSecretary.VIEW) state.view = "focus";
    render();
  }

  async function load() {
    if (!on()) return;
    try { data = await api("/canvas"); }
    catch (err) { data = data || { widgets: [], secretary: { available: false, usage: {} } }; console.warn("canvas:", err.message); }
  }
  async function onBoard() {
    if (!on()) return;
    await load();
    const sig = JSON.stringify([data, window.ConsoleSecretary.stateKey(), summarySig()]);
    if (sig !== lastSig) { lastSig = sig; paint(); }
  }
  // The decision cards come from the secretary summary in the board; redraw when they change.
  const summarySig = () => JSON.stringify(window.ConsoleSecretary.cardsFor(new Set(decisionThreads())).map((c) => c.request_id));
  const decisionThreads = () => ((data && data.widgets) || []).filter((w) => w.kind === "decision").map((w) => w.data.thread_id);

  // Called from render(): the mode owns the layout while it is on.
  function paint() {
    const box = $("canvas");
    const active = on();
    document.body.classList.toggle("canvas", active);
    document.body.classList.toggle("canvas-chat", active && narrow.matches && chatOpen);
    if (!box) return;
    box.hidden = !active;
    if (!active) { box.replaceChildren(); return; }
    // Someone typing an answer inside the canvas keeps their box (and its focus) until done.
    const typing = document.activeElement;
    if (typing && box.contains(typing) && typing.matches("textarea, input")) return;
    const id = secretaryId();
    // Wide screens always show a conversation on the right: the secretary unless a thread is open.
    if (!narrow.matches && id && !state.open && state.byId.has(id)) {
      state.open = id;
      queueMicrotask(() => openItem(id));
    }
    box.replaceChildren(header(), ...body());
  }

  // Back to the secretary from a thread opened from a widget; Esc and the close button land here.
  function onClose() {
    if (!on()) return false;
    const id = secretaryId();
    if (narrow.matches) { chatOpen = false; return false; }
    if (id && state.open !== id) { openItem(id); return true; }
    return true; // the secretary stays open on wide screens
  }

  function chat() {
    const id = secretaryId();
    if (!id) return;
    chatOpen = true;
    openItem(id);
  }

  async function start(restart = false) {
    if (restart && !confirm(S().restart_confirm)) return;
    starting = true; paint();
    try {
      const res = await api("/canvas/secretary", { method: "POST", body: { restart } });
      await refresh();
      await load();
      state.open = null;
      if (!narrow.matches) openItem(res.item_id);
    } catch (err) { toast(err.message); }
    finally { starting = false; paint(); }
  }

  async function setPaused(paused) {
    try { await api("/canvas/pause", { method: "POST", body: { paused } }); await load(); paint(); }
    catch (err) { toast(err.message); }
  }

  function header() {
    const s = S();
    const sec = (data && data.secretary) || {};
    const item = sec.item_id && state.byId.get(sec.item_id);
    const running = item ? item.running || item.queued : sec.running;
    const tags = [];
    if (sec.item_id) {
      tags.push(h("span", { class: "tag" + (running ? " hot" : ""), text: running ? s.running : s.idle }));
      if (sec.paused) tags.push(h("span", { class: "tag", text: s.paused_tag }));
      if (sec.pending && !running) tags.push(h("span", { class: "tag", text: s.pending(sec.pending) }));
    }
    const ctx = item && item.context ? s.context(item.context) : "";
    const usage = sec.item_id && sec.usage ? h("div", { class: "cv-usage dim", text: s.usage(sec.usage, ctx) }) : null;
    const buttons = [];
    if (sec.item_id) {
      buttons.push(h("button", { type: "button", class: "chip", onclick: () => setPaused(!sec.paused), text: sec.paused ? s.resume : s.pause }));
      buttons.push(h("button", { type: "button", class: "chip", onclick: () => start(true), text: s.restart }));
    }
    buttons.push(h("button", { type: "button", class: "chip", onclick: leave, text: s.classic }));
    const back = state.open && sec.item_id && state.open !== sec.item_id && !narrow.matches
      ? h("button", { type: "button", class: "btn cv-back", onclick: () => openItem(sec.item_id), text: s.back }) : null;
    return h("header", { class: "cv-head" },
      h("div", { class: "cv-head-row" }, h("h1", { text: s.title }), ...tags, h("span", { class: "cv-spacer" }), ...buttons),
      usage, back);
  }

  function body() {
    const s = S();
    const sec = (data && data.secretary) || {};
    if (!sec.item_id) {
      const max = sec.usage ? sec.usage.max_wakes : 24;
      return [h("section", { class: "cv-start" }, h("h2", { text: s.start_title }),
        h("p", { text: s.start_body.replace("{max}", max) }),
        sec.available === false ? h("p", { class: "dim", text: s.unavailable })
          : h("button", { type: "button", class: "btn primary big", disabled: starting ? "" : null,
            onclick: () => start(false), text: starting ? s.starting : s.start }))];
    }
    const widgets = (data && data.widgets) || [];
    const out = [];
    if (!widgets.length) out.push(h("p", { class: "cv-empty dim", text: s.empty }));
    else out.push(h("div", { class: "cv-grid" }, ...widgets.map(widget)));
    if (narrow.matches) out.push(h("button", { type: "button", class: "btn primary cv-chat", onclick: chat, text: s.chat }));
    return out;
  }

  // -- widgets ---------------------------------------------------------------
  const DRAW = { decision, diff, table, markdown, timeline, agents, metric };

  function widget(w) {
    const s = S();
    const draw = DRAW[w.kind];
    const head = h("div", { class: "cv-w-head" },
      h("h3", { text: w.title }),
      h("span", { class: "cv-spacer" }),
      h("span", { class: "dim cv-when", title: w.updated_at, text: s.updated(rel(w.updated_at)) }),
      h("button", { type: "button", class: "icon-btn cv-x", title: s.dismiss, "aria-label": s.dismiss,
        onclick: () => dismiss(w.key), text: "✕" }));
    const note = w.note ? h("div", { class: "cv-note dim", text: w.note }) : null;
    const actions = (w.actions || []).length ? h("div", { class: "cv-actions" },
      ...w.actions.map((a, i) => h("button", { type: "button", class: "btn" + (i === 0 ? " primary" : ""),
        onclick: (e) => press(w, i, e.currentTarget), text: a.label }))) : null;
    return h("article", { class: `cv-w cv-k-${w.kind} cv-size-${w.size}`, "data-key": w.key },
      head, note, h("div", { class: "cv-w-body" }, draw ? draw(w.data) : null), actions);
  }

  async function press(w, index, button) {
    button.disabled = true;
    try {
      await api(`/canvas/widgets/${encodeURIComponent(w.key)}/action`, { method: "POST", body: { index } });
      toast(S().sent);
      await refresh();
      if (!narrow.matches && secretaryId()) openItem(secretaryId());
    } catch (err) { toast(err.message); button.disabled = false; }
  }

  async function dismiss(key) {
    try { await api(`/canvas/widgets/${encodeURIComponent(key)}`, { method: "DELETE" }); await load(); paint(); }
    catch (err) { toast(err.message); }
  }

  const threadLink = (threadId, label) => h("button", { type: "button", class: "guide-link",
    onclick: () => { chatOpen = true; openItem(`t${threadId}`); }, text: label });

  function decision(d) {
    const cards = window.ConsoleSecretary.cardsFor(new Set([d.thread_id]));
    if (!cards.length) {
      const item = state.byId.get(`t${d.thread_id}`);
      return h("div", { class: "dim" }, document.createTextNode(S().answered + " "),
        threadLink(d.thread_id, `↗ ${(item && item.title) || S().open}`));
    }
    return h("ul", { class: "sec-cards" }, ...cards.map((c) => window.ConsoleSecretary.card(c)));
  }

  function diffLines(text) {
    return h("pre", { class: "cv-diff" }, ...text.split("\n").map((line) => h("span", {
      class: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "",
      text: line + "\n" })));
  }
  function diff(d) {
    if (d.diff) return diffLines(d.diff);
    const s = S();
    return h("div", { class: "cv-before-after" },
      h("div", {}, h("div", { class: "dim", text: s.before }), h("pre", { class: "cv-diff del-block", text: d.before || "" })),
      h("div", {}, h("div", { class: "dim", text: s.after }), h("pre", { class: "cv-diff add-block", text: d.after || "" })));
  }

  function table(d) {
    return h("div", { class: "cv-table-wrap" }, h("table", { class: "cv-table" },
      h("thead", {}, h("tr", {}, ...d.columns.map((c) => h("th", { text: c })))),
      h("tbody", {}, ...d.rows.map((r) => h("tr", {}, ...d.columns.map((_, i) => h("td", { text: r[i] || "" })))))));
  }

  function markdown(d) { return h("div", { class: "md" }, MD.render(d.text)); }

  function timeline(d) {
    const s = S();
    return h("ol", { class: "cv-timeline" }, ...d.entries.map((e) => h("li", { class: `st-${e.state}` },
      h("span", { class: "cv-t-time", text: e.time || "" }),
      h("span", { class: "cv-t-state", text: s.states[e.state] || "○" }),
      e.thread_id ? threadLink(e.thread_id, e.label) : h("span", { class: "cv-t-label", text: e.label }),
      e.minutes ? h("span", { class: "dim", text: s.minutes(e.minutes) }) : null)));
  }

  function agents(d) {
    const ids = (d.thread_ids || []).map((t) => `t${t}`);
    const sid = secretaryId();
    const rows = ids.length ? ids.map((id) => state.byId.get(id)).filter(Boolean)
      : state.items.filter((i) => (i.running || i.queued) && i.id !== sid);
    if (!rows.length) return h("div", { class: "dim", text: S().no_running });
    return h("ul", { class: "cv-agents" }, ...rows.slice(0, 20).map((i) => h("li", {},
      h("span", { text: STATUS_ICON[i.status] || "·" }),
      threadLink(i.thread_id || i.id.slice(1), i.title || i.thread_name || i.id),
      i.last_activity_at ? h("span", { class: "dim", text: rel(i.last_activity_at) }) : null)));
  }

  function metric(d) {
    return h("div", { class: "cv-metric" },
      h("div", { class: "cv-m-value", text: d.value }),
      h("div", { class: "cv-m-label", text: d.label }),
      d.sub ? h("div", { class: "dim", text: d.sub }) : null,
      typeof d.percent === "number" ? h("div", { class: "cv-bar" },
        h("span", { style: { width: `${Math.max(0, Math.min(100, d.percent))}%` } })) : null);
  }

  return { on, enter, leave, paint, onBoard, onClose, load, MODE, label: () => S().mode };
})();
