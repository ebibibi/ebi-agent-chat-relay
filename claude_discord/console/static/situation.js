// The panel beside a thread family's conversation: what the person is asked for, what is
// running, what is settled. It does not scroll with the conversation, so a child's report
// cannot push the current question out of view.
// What is measured (status, running, waits) needs no agent; what only the parent can say
// comes from /items/{id}/situation and shows how old it is.
// Loaded after family.js and wspane.js and before app.js, which calls these from renderDetail.
"use strict";
window.ConsoleSituation = (() => {
  const REFRESH_MS = 8_000;
  const SPLIT_MIN_PX = 640; // below this the panel becomes a tab beside the conversation
  const STALE_MS = 15 * 60_000;
  const STRINGS = {
    en: { title: "📍 Where things stand", asks: "🙋 Needed from you", running: "⚙️ Running", decided: "✅ Decided and done",
      chat: "💬 Conversation", tab: "📍 Where things stand", none: "Nothing right now", answer: "↩ Answer",
      answerHint: "Quote this in the reply to the parent", updated: (t) => `Parent's record: ${t}`,
      stale: "older than the latest activity", unwritten: "The parent has not written anything yet; this is what is measured.",
      queued: "waiting for a slot", wait: "waiting for", done: (n) => `${n} finished`, candidate: "ready to confirm",
      background: "↪ Handling a report or a finished wait" },
    ja: { title: "📍 現在地", asks: "🙋 求められていること", running: "⚙️ 実行中", decided: "✅ 決定・完了",
      chat: "💬 会話", tab: "📍 現在地", none: "今はありません", answer: "↩ 回答する",
      answerHint: "親への返信欄に引用します", updated: (t) => `親の記入: ${t}`,
      stale: "最新の動きより古い記入です", unwritten: "親はまだ記入していません。下は実測です。",
      queued: "実行枠の待ち", wait: "待機中", done: (n) => `完了 ${n}件`, candidate: "完了の確認待ち",
      background: "↪ 子の報告や待機完了への対応" },
  };
  const L = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  const cache = new Map(); // item id -> {at, data}
  const pending = new Set();
  let tab = "chat";
  let observer = null;

  async function load(id) {
    if (pending.has(id)) return;
    const hit = cache.get(id);
    if (hit && Date.now() - hit.at < REFRESH_MS) return;
    pending.add(id);
    try {
      const data = await api(`/items/${encodeURIComponent(id)}/situation`);
      const before = JSON.stringify(cache.get(id)?.data);
      cache.set(id, { at: Date.now(), data });
      const current = state.open && state.byId.get(state.open);
      if (current && JSON.stringify(data) !== before) render(current);
    } catch {
      // Measured parts still show; the next poll tries again.
      cache.set(id, { at: Date.now(), data: cache.get(id)?.data || null });
    } finally {
      pending.delete(id);
    }
  }

  // The thread whose record to show: a parent writes its own, a leaf shows its root's.
  function recordHolder(item, family) {
    return state.items.some((i) => i.parent_id === item.id) ? item : family.root;
  }

  function model(item, family, holder, data) {
    const written = data && data.situation;
    const members = [family.root, ...family.rows.map((r) => r.item)];
    const needs = members.filter(window.ConsoleFamily.needsAttention)
      // The parent's own marker says the same as the questions it wrote down.
      .filter((i) => !(i.id === holder.id && written && written.asks.length));
    const ids = new Set(members.map((i) => i.thread_id).filter(Boolean));
    const byThread = new Map(members.map((i) => [i.thread_id, i]));
    const waits = ((data && data.waits) || []).filter((w) => ids.has(w.thread_id));
    return {
      holder, written, needs, waits, byThread,
      running: members.filter((i) => i.running || i.queued),
      candidates: members.filter((i) => i.status === "candidate"),
      finished: members.filter((i) => i.status === "done"),
    };
  }

  const count = (m) => (m.written ? m.written.asks.length : 0) + m.needs.length;
  const inline = (text) => h("div", { class: "md sit-text" }, window.ConsoleNotes.linkify(MD.render(text)));

  function answer(holder, text) {
    const quote = `${text.split("\n").map((line) => `> ${line}`).join("\n")}\n\n`;
    state.drafts[holder.id] = quote + (state.drafts[holder.id] || "");
    const focus = () => {
      const box = $("reply");
      if (!box) return;
      box.value = state.drafts[holder.id];
      grow(box);
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    };
    if (state.open === holder.id) focus();
    else { openItem(holder.id); requestAnimationFrame(focus); }
  }

  const threadRow = (item, detail, icon) => h("li", { class: "sit-row" },
    h("button", { type: "button", class: "linkish sit-thread", onclick: () => openItem(item.id) },
      `${icon || STATUS_ICON[item.status] || "·"} ${item.title || item.thread_name || item.id}`),
    detail ? h("div", { class: "dim sit-detail", text: detail }) : null);

  function section(title, tone, empty, ...rows) {
    const body = rows.flat().filter(Boolean);
    return h("section", { class: `sit-section ${tone}` },
      h("h3", { class: "sit-head", text: title }),
      body.length ? h("ul", { class: "sit-list" }, ...body)
        : h("div", { class: "dim sit-empty", text: empty }));
  }

  function freshness(m, family) {
    const l = L();
    if (!m.written) return h("p", { class: "dim sit-updated", text: l.unwritten });
    const latest = Math.max(...[family.root, ...family.rows.map((r) => r.item)]
      .map((i) => Date.parse(i.last_activity_at) || 0));
    const stale = latest - Date.parse(m.written.updated_at) > STALE_MS;
    return h("p", { class: "dim sit-updated" + (stale ? " stale" : ""),
      text: l.updated(rel(m.written.updated_at)) + (stale ? ` · ${l.stale}` : "") });
  }

  function core(m, family) {
    const l = L();
    const written = m.written || { asks: [], doing: [], decided: [] };
    const asks = written.asks.map((text) => h("li", { class: "sit-row sit-ask" }, inline(text),
      h("button", { type: "button", class: "btn sit-answer", title: l.answerHint, text: l.answer,
        onclick: () => answer(m.holder, text) })));
    const needs = m.needs.map((i) => threadRow(i, i.ai && i.ai.next_step));
    const doing = written.doing.map((text) => h("li", { class: "sit-row" }, inline(text)));
    const running = m.running.map((i) => threadRow(i, i.queued ? l.queued : ""));
    const waits = m.waits.map((w) => {
      const owner = m.byThread.get(w.thread_id);
      return h("li", { class: "sit-row" },
        h("div", { text: `⏳ ${l.wait}: ${w.label}` }),
        owner ? h("div", { class: "dim sit-detail", text: owner.title || owner.id }) : null);
    });
    const decided = written.decided.map((text) => h("li", { class: "sit-row" }, inline(text)));
    const candidates = m.candidates.map((i) => threadRow(i, l.candidate));
    const finished = m.finished.length ? h("li", { class: "sit-row" }, h("details", {},
      h("summary", { class: "dim", text: l.done(m.finished.length) }),
      h("ul", { class: "sit-list" }, ...m.finished.map((i) => threadRow(i))))) : null;
    return [
      h("h2", { class: "sit-title", text: l.title }),
      freshness(m, family),
      section(l.asks, "asks", l.none, asks, needs),
      section(l.running, "running", l.none, doing, running, waits),
      section(l.decided, "decided", l.none, decided, candidates, finished),
    ];
  }

  // Family list and Workspace entry join the panel, so the conversation keeps the height.
  // A child keeps the family list in the conversation column at any width: it holds the
  // way back to the parent and the only way to reply to the child, which the parent's
  // record would otherwise push below the panel's fold.
  function place(show, family) {
    const side = $("d-side"), main = $("d-main"), notes = $("d-notes");
    if (!side || !main || !notes) return;
    const familyNode = $("d-family"), workspace = $("d-ws");
    const beside = (node, there) => {
      if (!node) return;
      if (there && node.parentElement !== side) side.append(node);
      if (!there && node.parentElement !== main) main.insertBefore(node, notes);
    };
    beside(familyNode, show && !(family && family.parent));
    beside(workspace, show);
  }

  function paintTabs(body, show, tabs, asks) {
    const bar = $("d-tabs");
    if (!bar) return;
    bar.hidden = !(show && tabs);
    const active = show && tabs ? tab : "chat";
    body.dataset.tab = active;
    if (bar.hidden) { bar.replaceChildren(); return; }
    const l = L();
    const button = (key, label) => h("button", { type: "button", class: "sit-tab" + (active === key ? " on" : ""),
      "aria-pressed": active === key ? "true" : "false", onclick: () => switchTab(key), text: label });
    bar.replaceChildren(button("chat", l.chat), button("situation", asks ? `${l.tab} (${asks})` : l.tab));
  }

  function switchTab(next) {
    const msgs = $("msgs");
    const top = msgs ? msgs.scrollTop : 0;
    tab = next;
    const current = state.open && state.byId.get(state.open);
    if (current) render(current);
    if (msgs && next === "chat") msgs.scrollTop = top;
  }

  // Called from renderDetail on every redraw; rebuilds only when something it shows changed.
  function render(item) {
    const body = $("d-body"), side = $("d-side");
    if (!body || !side) return;
    const family = window.ConsoleFamily.model(state.items, item.id);
    const inFamily = Boolean(family && (family.parent || family.rows.length));
    const holder = family ? recordHolder(item, family) : item;
    if (holder.thread_id) load(holder.id);
    const data = cache.get(holder.id)?.data || null;
    const situation = data && data.situation;
    const written = Boolean(situation && (situation.asks.length || situation.doing.length || situation.decided.length));
    const show = Boolean(family && (inFamily || written));
    const tabs = body.dataset.layout === "tabs";
    side.hidden = !show;
    place(show, family);
    if (!show) { paintTabs(body, false, tabs, 0); return; }
    const m = model(item, family, holder, data);
    paintTabs(body, true, tabs, count(m));
    const key = JSON.stringify([LANG, holder.id, m.written, m.waits,
      [...m.needs, ...m.running, ...m.candidates, ...m.finished].map((i) =>
        [i.id, i.status, i.queued, i.title, i.ai && i.ai.next_step]),
      m.written ? rel(m.written.updated_at) : ""]);
    // Kept on the node: opening another thread rebuilds the pane and empties it.
    const slot = $("d-side-core");
    if (slot.dataset.key === key) return;
    slot.dataset.key = key;
    const top = side.scrollTop;
    slot.replaceChildren(...core(m, family));
    side.scrollTop = top;
  }

  // Follows the pane's own width: it depends on the window, the list and the resize handle.
  function attach(body) {
    observer?.disconnect();
    if (!window.ResizeObserver) { body.dataset.layout = "split"; return; }
    observer = new ResizeObserver(([entry]) => {
      const layout = entry.contentRect.width < SPLIT_MIN_PX ? "tabs" : "split";
      if (body.dataset.layout === layout) return;
      body.dataset.layout = layout;
      const current = state.open && state.byId.get(state.open);
      if (current) render(current);
    });
    observer.observe(body);
  }

  // Which agent messages belong to a turn the relay started rather than the person.
  function backgroundIds(messages) {
    const ids = new Set();
    let automatic = false;
    for (const m of messages) {
      if (m.kind === "human") automatic = false;
      else if (m.kind === "activity" && /^\[(MESSAGE FROM ANOTHER AI SESSION|WAIT FINISHED)/.test(m.content || "")) automatic = true;
      else if (m.kind === "agent" && automatic) ids.add(m.id);
    }
    return ids;
  }
  function markBackground(node) {
    node.classList.add("background");
    node.prepend(h("div", { class: "dim msg-background", text: L().background }));
    return node;
  }

  // A parent's record changes nothing on the board, so the board's refresh would not redraw it.
  setInterval(() => {
    const current = !document.hidden && state.open && state.byId.get(state.open);
    if (current) render(current);
  }, REFRESH_MS);

  return { render, attach, backgroundIds, markBackground };
})();
