// Distraction-free modes: nothing on screen but the conversation.
// "one": the open thread fills the window; navigation, list and usage strip are hidden.
// "queue": work through everything that needs you, one thread at a time. The queue is the
// Focus ranking (importance first) of threads you can act on here and now, narrowed to the
// time bucket chosen in Focus (⚡ quick / ⏱ short / 🧘 deep). Sending a reply, finishing
// or skipping moves straight to the next thread, so no list is ever picked from. A thread
// that starts needing you while the queue runs — a new question, an agent that came back
// after a reply — joins its end; an emptied queue says so and keeps watching.
// Loaded before app.js, which calls bar() from renderDetail(), paint() from render(),
// onBoard() from refresh(), sent() after a reply, onClose() from closeItem() and escape() for Esc.
"use strict";

window.ConsoleZen = (() => {
  // A thread handled in this run comes back only once the conversation moved on after it,
  // and not merely because our own reply landed a moment later.
  const SETTLE_MS = 15_000;
  let mode = null; // null | "one" | "queue"
  let order = []; // item ids in the order they joined the queue
  const handled = new Map(); // id -> ms when it was sent, finished or skipped
  let total = 0;

  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    one: "🎯 集中モード", queue: "🔁 連続処理", exit: "✕ 終了", exit_hint: "通常の画面に戻る（Esc）",
    skip: "⏭ スキップ", skip_hint: "これは後回しにして次へ（n）", position: (i, n) => `${i} / ${n}`,
    left: (n) => `残り ${n} 件`, clear: "🎉 いま片付けるものはありません",
    clear_more: "新しく返事が必要なスレッドが来たら、ここに自動で出します。",
    zen: "🎯 集中", zen_hint: "このスレッドだけを表示する（z）",
    start: (n) => `🔁 連続で片付ける（${n}）`, start_hint: "今やることを1件ずつ全画面で出し、送信したら次へ進みます",
    none: "いま片付けられるものはありません",
  } : {
    one: "🎯 Focus mode", queue: "🔁 Work through", exit: "✕ Exit", exit_hint: "Back to the full console (Esc)",
    skip: "⏭ Skip", skip_hint: "Leave this for later and go to the next one (n)", position: (i, n) => `${i} / ${n}`,
    left: (n) => `${n} left`, clear: "🎉 Nothing needs you right now",
    clear_more: "A thread that needs your reply will show up here on its own.",
    zen: "🎯 Focus", zen_hint: "Show only this thread (z)",
    start: (n) => `🔁 Work through (${n})`, start_hint: "Show what needs you one thread at a time; sending moves to the next",
    none: "Nothing to work through right now",
  };

  const F = () => window.ConsoleFocus;
  // What the queue offers: a thread whose move is yours, that you can take here and now,
  // within the chosen time bucket. A running or queued turn is the agent's move.
  const wanted = (i) => Boolean(i && i.thread_id) && i.focus !== null && i.focus !== undefined &&
    i.actionable !== false && !i.running && !i.queued && !F().elsewhere(i) && F().effortOk(i);
  const ranked = () => state.items.filter(wanted).sort(F().byImportance);
  const settled = (i) => {
    const at = handled.get(i.id);
    if (at === undefined) return true;
    return Boolean(i.last_activity_at) && new Date(i.last_activity_at).getTime() > at + SETTLE_MS;
  };
  const candidates = () => ranked().filter(settled);
  const remaining = () => order.filter((id) => !handled.has(id) && wanted(state.byId.get(id)));

  function apply() {
    document.body.classList.toggle("zen", mode !== null);
    document.body.classList.toggle("zen-queue", mode === "queue");
    renderEmpty();
  }

  function enter(next) {
    mode = next;
    $("nav").classList.remove("open");
    apply();
    render();
  }

  function exit() {
    mode = null;
    order = [];
    handled.clear();
    apply();
    render();
    revealSelection();
  }

  // Pull in what started needing you; a thread handled earlier rejoins at the end.
  function grow() {
    for (const i of candidates()) {
      if (handled.has(i.id)) { handled.delete(i.id); order = order.filter((id) => id !== i.id); }
      if (!order.includes(i.id)) { order.push(i.id); total += 1; }
    }
  }

  function advance() {
    grow();
    const next = remaining()[0];
    if (!next) {
      if (state.open) { markSeen(state.open); state.open = null; }
      render();
      return;
    }
    openItem(next).then(() => { const box = $("reply"); if (box && mode === "queue") box.focus(); });
  }

  function start() {
    order = [];
    total = 0;
    handled.clear();
    grow();
    if (!order.length) { toast(L().none); return; }
    enter("queue");
    advance();
  }

  const done = (id) => { if (id) handled.set(id, Date.now()); };
  function skip() {
    if (mode !== "queue") return;
    done(state.open);
    advance();
  }

  // After a reply is posted (app.js sendReply): in a queue, the next thread is up.
  function sent(id) {
    if (mode !== "queue") return;
    done(id);
    advance();
  }

  // closeItem(): leaving a thread in a queue (finish, the ← button) moves on to the next
  // one; leaving it in single-thread focus ends the mode. True when the queue took over.
  function onClose(id) {
    if (mode === "one") { mode = null; apply(); return false; }
    if (mode !== "queue") return false;
    done(id);
    advance();
    return true;
  }

  function onBoard() {
    if (mode !== "queue") return;
    const before = order.length;
    grow();
    if (!state.open && order.length > before) advance();
  }

  // Esc outside a text box: leave the mode, keep the thread open.
  function escape() {
    if (!mode) return false;
    exit();
    return true;
  }

  function toggle() {
    if (mode) { exit(); return; }
    if (state.open) enter("one");
  }

  // The strip at the top of the detail pane while a mode is on (re-inserted after each rebuild).
  function bar(pane) {
    const old = pane.querySelector(":scope > .zen-bar");
    if (!mode) { if (old) old.remove(); return; }
    const t = L();
    const parts = [h("span", { class: "zen-title", text: mode === "queue" ? t.queue : t.one })];
    if (mode === "queue") {
      // Where we are: the open thread counts as the one in hand, not as one left.
      const left = remaining().filter((id) => id !== state.open).length;
      const at = Math.max(1, total - left);
      parts.push(h("span", { class: "zen-pos", text: t.position(at, total) }),
        h("span", { class: "dim", text: t.left(left) }),
        F().effortChosen() ? h("span", { class: "tag", text: F().effortLabel(F().effortChosen()) }) : null);
    }
    // What this thread asks of you, and how long the AI thinks it takes.
    const cur = state.byId.get(state.open);
    const next = cur && cur.ai && cur.ai.next_step;
    if (cur) parts.push(F().effortTag(cur), next ? h("span", { class: "zen-next", text: `→ ${next}` }) : null);
    parts.push(h("span", { class: "zen-gap" }));
    if (mode === "queue") parts.push(h("button", { type: "button", class: "btn zen-skip", title: t.skip_hint, onclick: skip, text: t.skip }));
    parts.push(h("button", { type: "button", class: "btn zen-exit", title: t.exit_hint, onclick: exit, text: t.exit }));
    const node = h("div", { class: "zen-bar", role: "toolbar" }, ...parts);
    if (old) old.replaceWith(node); else pane.prepend(node);
  }

  // A queue with nothing left: the whole window says so, instead of an empty grid.
  function renderEmpty() {
    let box = $("zen-empty");
    const show = mode === "queue" && !state.open;
    if (!show) { if (box) box.hidden = true; return; }
    const t = L();
    if (!box) { box = h("div", { id: "zen-empty", class: "zen-empty" }); document.body.append(box); }
    box.hidden = false;
    box.replaceChildren(h("div", { class: "zen-empty-card" },
      h("div", { class: "zen-empty-title", text: t.clear }),
      h("div", { class: "dim", text: t.clear_more }),
      h("button", { type: "button", class: "btn", onclick: exit, text: t.exit })));
  }

  // The 🎯 button in the detail header.
  function button(item) {
    if (mode) return null;
    return h("button", { type: "button", class: "btn d-zen", title: L().zen_hint, onclick: () => {
      if (state.open !== item.id) openItem(item.id);
      enter("one");
    }, text: L().zen });
  }

  // The "work through" button in the Focus effort bar.
  function startButton() {
    const n = ranked().length;
    return h("button", { type: "button", class: "btn primary zen-start", title: L().start_hint,
      disabled: n ? null : true, onclick: start, text: L().start(n) });
  }

  const active = () => mode;
  return { active, start, skip, sent, onClose, onBoard, escape, toggle, bar, button, startButton, paint: renderEmpty };
})();
