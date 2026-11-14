// The panel beside a thread family's conversation: what the person is asked for, what each
// thread is doing, what is settled, and a reply box. It does not scroll with the conversation,
// so a child's report cannot push the current question out of view, and it can be read and
// answered on its own. The person can fold it to a rail; the choice is remembered.
// What is measured (status, running, waits, latest reports) needs no agent; what only the
// parent can say comes from /items/{id}/situation and shows how old it is.
// Loaded after family.js, wspane.js and situation_reply.js and before app.js.
"use strict";
window.ConsoleSituation = (() => {
  const REFRESH_MS = 8_000;
  const SPLIT_MIN_PX = 640; // below this the panel becomes a tab beside the conversation
  const STALE_MS = 15 * 60_000;
  const HIDDEN_KEY = "situationHidden";
  const STRINGS = {
    en: { title: "📍 Where things stand", asks: "🙋 Needed from you", running: "⚙️ Running", decided: "✅ Decided and done",
      members: "👥 Each thread", chat: "💬 Conversation", tab: "📍 Where things stand", none: "Nothing right now",
      answer: "↩ Answer", answerHint: "Quote this in the reply box below", reply: "↩ Reply", replyHint: "Reply to this thread from the box below",
      updated: (t) => `Parent's record: ${t}`, stale: "older than the latest activity",
      unwritten: "The parent has not written anything yet; this is what is measured.",
      queued: "waiting for a slot", wait: "waiting for", done: (n) => `${n} finished`, candidate: "ready to confirm",
      noReport: "No report yet", hide: "Hide this panel", show: "Show where things stand",
      background: "↪ Handling a report or a finished wait", unexplained: "no request" },
    ja: { title: "📍 現在地", asks: "🙋 求められていること", running: "⚙️ 実行中", decided: "✅ 決定・完了",
      members: "👥 各スレッドの様子", chat: "💬 会話", tab: "📍 現在地", none: "今はありません",
      answer: "↩ 回答する", answerHint: "下の返信欄に引用します", reply: "↩ 返信", replyHint: "下の返信欄からこのスレッドに返信します",
      updated: (t) => `親の記入: ${t}`, stale: "最新の動きより古い記入です",
      unwritten: "親はまだ記入していません。下は実測です。",
      queued: "実行枠の待ち", wait: "待機中", done: (n) => `完了 ${n}件`, candidate: "完了の確認待ち",
      noReport: "まだ報告はありません", hide: "このパネルを隠す", show: "現在地を表示",
      background: "↪ 子の報告や待機完了への対応", unexplained: "説明なし" },
  };
  const L = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  const cache = new Map(); // item id -> {at, data}
  const pending = new Set();
  const reports = new Map(); // item id -> {version, text}: the latest agent message, shortened
  const reportsPending = new Set();
  let tab = "chat";
  let observer = null;
  let hidden = localStorage.getItem(HIDDEN_KEY) === "1";

  const redrawOpen = () => {
    const current = state.open && state.byId.get(state.open);
    if (current) render(current);
  };

  async function load(id) {
    if (pending.has(id)) return;
    const hit = cache.get(id);
    if (hit && Date.now() - hit.at < REFRESH_MS) return;
    pending.add(id);
    try {
      const data = await api(`/items/${encodeURIComponent(id)}/situation`);
      const before = JSON.stringify(cache.get(id)?.data);
      cache.set(id, { at: Date.now(), data });
      if (JSON.stringify(data) !== before) redrawOpen();
    } catch {
      // Measured parts still show; the next poll tries again.
      cache.set(id, { at: Date.now(), data: cache.get(id)?.data || null });
    } finally {
      pending.delete(id);
    }
  }

  // Latest report of each member, refetched only when the board says the thread moved.
  const version = (i) => `${i.last_activity_at || ""}|${i.status}|${Boolean(i.running)}`;
  async function loadReports(members) {
    const wanted = members.filter((i) => i.thread_id && !reportsPending.has(i.id) &&
      reports.get(i.id)?.version !== version(i)).slice(0, Math.max(0, 3 - reportsPending.size));
    if (!wanted.length) return;
    await Promise.allSettled(wanted.map(async (i) => {
      reportsPending.add(i.id);
      try {
        const data = await api(`/items/${encodeURIComponent(i.id)}/messages?limit=20`);
        reports.set(i.id, { version: version(i), text: window.ConsoleFamily.excerpt(data.messages || []) });
      } catch { reports.set(i.id, { version: version(i), text: "" }); }
      finally { reportsPending.delete(i.id); }
    }));
    redrawOpen();
  }

  // The thread whose record to show: a parent writes its own, a leaf shows its root's.
  function recordHolder(item, family) {
    return state.items.some((i) => i.parent_id === item.id) ? item : family.root;
  }

  function model(item, family, holder, data) {
    const written = data && data.situation;
    const all = [family.root, ...family.rows.map((r) => r.item)];
    const needs = all.filter(window.ConsoleFamily.needsAttention)
      // The parent's own marker says the same as the questions it wrote down.
      .filter((i) => !(i.id === holder.id && written && written.asks.length));
    const ids = new Set(all.map((i) => i.thread_id).filter(Boolean));
    const byThread = new Map(all.map((i) => [i.thread_id, i]));
    // The family's open decision requests, the same cards as the secretary home (ADR-0024).
    const cards = window.ConsoleSecretary.cardsFor(ids);
    const carded = new Set(cards.map((c) => c.thread_id));
    const waits = ((data && data.waits) || []).filter((w) => ids.has(w.thread_id));
    // Who still moves or waits on the person, those needing them first.
    const open = all.filter((i) => i.status !== "done");
    const members = [...open.filter((i) => needs.includes(i)), ...open.filter((i) => !needs.includes(i))];
    return {
      holder, written, needs, waits, byThread, members, root: family.root, cards,
      // Waiting on the person with no request written: listed apart, as "no explanation".
      unexplained: needs.filter((i) => !carded.has(i.thread_id)),
      candidates: all.filter((i) => i.status === "candidate"),
      finished: all.filter((i) => i.status === "done"),
    };
  }

  const count = (m) => (m.written ? m.written.asks.length : 0) + m.cards.length + m.unexplained.length;
  const inline = (text) => h("div", { class: "md sit-text" }, window.ConsoleNotes.linkify(MD.render(text)));
  const title = (i) => i.title || i.thread_name || i.id;

  const openLink = (item, icon) => h("button", { type: "button", class: "linkish sit-thread",
    onclick: () => openItem(item.id) }, `${icon || STATUS_ICON[item.status] || "·"} ${title(item)}`);
  const threadRow = (item, detail, icon) => h("li", { class: "sit-row" }, openLink(item, icon),
    detail ? h("div", { class: "dim sit-detail", text: detail }) : null);
  const replyButton = (item) => h("button", { type: "button", class: "btn sit-answer", title: L().replyHint,
    text: L().reply, onclick: () => window.ConsoleSituationReply.choose(item.id, { focus: true }) });

  function memberRow(item, m) {
    const l = L();
    const report = reports.get(item.id)?.text;
    const status = item.queued ? l.queued : item.running ? T.st.running || "" : T.st[item.status] || item.status;
    return h("li", { class: "sit-row sit-member" + (m.needs.includes(item) ? " attention" : "") },
      h("div", { class: "sit-member-head" }, openLink(item),
        h("span", { class: "dim sit-detail", text: [status, rel(item.last_activity_at)].filter(Boolean).join(" · ") })),
      h("p", { class: "sit-report" + (report ? "" : " dim"), text: report || (item.ai && item.ai.next_step) || l.noReport }),
      replyButton(item));
  }

  function section(heading, tone, empty, ...rows) {
    const body = rows.flat().filter(Boolean);
    return h("section", { class: `sit-section ${tone}` },
      h("h3", { class: "sit-head", text: heading }),
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
    const cards = m.cards.map((c) => window.ConsoleSecretary.card(c));
    const unexplainedTag = () => h("span", { class: "tag sit-unexplained", text: l.unexplained });
    // Text an older turn wrote into asks: kept visible, but marked as having no request behind it.
    const asks = written.asks.map((text) => h("li", { class: "sit-row sit-ask" }, unexplainedTag(), inline(text),
      h("button", { type: "button", class: "btn sit-answer", title: l.answerHint, text: l.answer,
        onclick: () => window.ConsoleSituationReply.quote(m.holder.id, text) })));
    const needs = m.unexplained.map((i) => h("li", { class: "sit-row sit-ask" }, unexplainedTag(), openLink(i),
      h("p", { class: "sit-report", text: reports.get(i.id)?.text || (i.ai && i.ai.next_step) || l.noReport }),
      replyButton(i)));
    const doing = written.doing.map((text) => h("li", { class: "sit-row" }, inline(text)));
    const waits = m.waits.map((w) => {
      const owner = m.byThread.get(w.thread_id);
      return h("li", { class: "sit-row" },
        h("div", { text: `⏳ ${l.wait}: ${w.label}` }),
        owner ? h("div", { class: "dim sit-detail", text: title(owner) }) : null);
    });
    const decided = written.decided.map((text) => h("li", { class: "sit-row" }, inline(text)));
    const candidates = m.candidates.map((i) => threadRow(i, l.candidate));
    const finished = m.finished.length ? h("li", { class: "sit-row" }, h("details", {},
      h("summary", { class: "dim", text: l.done(m.finished.length) }),
      h("ul", { class: "sit-list" }, ...m.finished.map((i) => threadRow(i))))) : null;
    const others = m.members.filter((i) => !m.needs.includes(i));
    return [
      h("div", { class: "sit-top" },
        h("h2", { class: "sit-title", text: l.title }),
        h("button", { type: "button", class: "btn sit-hide", title: l.hide, "aria-label": l.hide, text: "›",
          onclick: () => setHidden(true) })),
      freshness(m, family),
      section(l.asks, "asks", l.none, cards, needs, asks),
      section(l.running, "running", l.none, doing, waits),
      section(l.members, "members", l.none, others.map((i) => memberRow(i, m))),
      section(l.decided, "decided", l.none, decided, candidates, finished),
    ];
  }

  function setHidden(next) {
    hidden = next;
    localStorage.setItem(HIDDEN_KEY, next ? "1" : "0");
    if (!next && $("d-body")?.dataset.layout === "tabs") tab = "situation";
    // A full redraw: the family list's own visibility comes back with it.
    if (state.open) renderDetail();
  }

  // With the panel open, the Workspace entry joins the panel so the conversation keeps the
  // height. The family list stays in the conversation column (folded to one line beside the
  // panel by default, see family.js): a child's list holds the way back to the parent and the
  // child's own reply escape hatch. Folded, everything returns to the conversation column.
  function place(open, family) {
    const side = $("d-side"), main = $("d-main"), notes = $("d-notes"), reply = $("d-side-reply");
    if (!side || !main || !notes || !reply) return;
    const familyNode = $("d-family"), workspace = $("d-ws");
    if (workspace && open && workspace.parentElement !== side) side.insertBefore(workspace, reply);
    if (workspace && !open && workspace.parentElement !== main) main.insertBefore(workspace, notes);
    if (familyNode && familyNode.parentElement !== main) main.insertBefore(familyNode, notes);
  }

  function paintTabs(body, show, tabs, asks) {
    const bar = $("d-tabs");
    if (!bar) return;
    bar.hidden = !(show && tabs);
    const active = show && tabs && !hidden ? tab : "chat";
    body.dataset.tab = active;
    if (bar.hidden) { bar.replaceChildren(); return; }
    const l = L();
    const label = asks ? `${l.tab} (${asks})` : l.tab;
    if (hidden) {
      bar.replaceChildren(h("button", { type: "button", class: "sit-tab sit-reopen", title: l.show,
        onclick: () => setHidden(false), text: label }));
      return;
    }
    const button = (key, text) => h("button", { type: "button", class: "sit-tab" + (active === key ? " on" : ""),
      "aria-pressed": active === key ? "true" : "false", onclick: () => switchTab(key), text });
    bar.replaceChildren(button("chat", l.chat), button("situation", label));
  }

  function switchTab(next) {
    const msgs = $("msgs");
    const top = msgs ? msgs.scrollTop : 0;
    tab = next;
    redrawOpen();
    if (msgs && next === "chat") msgs.scrollTop = top;
  }

  function paintRail(side, asks) {
    const rail = $("d-side-rail");
    if (!rail) return;
    side.classList.toggle("collapsed", hidden);
    rail.hidden = !hidden;
    const l = L();
    rail.title = l.show;
    rail.setAttribute("aria-label", l.show);
    rail.replaceChildren(h("span", { text: "📍" }), asks ? h("span", { class: "sit-rail-count", text: String(asks) }) : null);
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
    side.hidden = !show || (hidden && tabs);
    place(show && !hidden, family);
    if (!show) { paintTabs(body, false, tabs, 0); return; }
    const m = model(item, family, holder, data);
    paintTabs(body, true, tabs, count(m));
    paintRail(side, count(m));
    if (hidden) return;
    loadReports(m.members);
    window.ConsoleSituationReply.render(
      m.members.map((i) => ({ item: i, role: i.id === m.root.id ? "parent" : "child" })), holder.id);
    const key = JSON.stringify([LANG, holder.id, m.written, m.waits,
      m.cards.map((c) => [c.request_id, c.card]), window.ConsoleSecretary.stateKey(),
      [...m.members, ...m.candidates, ...m.finished].map((i) =>
        [i.id, i.status, i.queued, i.running, i.title, i.ai && i.ai.next_step, reports.get(i.id)?.text,
          rel(i.last_activity_at)]),
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
    const rail = $("d-side-rail");
    if (rail) rail.onclick = () => setHidden(false);
    if (!window.ResizeObserver) { body.dataset.layout = "split"; return; }
    // Known before the first draw: the family list's line depends on it (family.js).
    const width = body.getBoundingClientRect().width;
    if (width) body.dataset.layout = width < SPLIT_MIN_PX ? "tabs" : "split";
    observer = new ResizeObserver(([entry]) => {
      const layout = entry.contentRect.width < SPLIT_MIN_PX ? "tabs" : "split";
      if (body.dataset.layout === layout) return;
      body.dataset.layout = layout;
      if (state.open) renderDetail(); // the family list too, not only the panel
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
  setInterval(() => { if (!document.hidden) redrawOpen(); }, REFRESH_MS);

  // Whether the open panel lists this parent's threads: family.js then starts its own list
  // folded to one line instead of repeating them in full above the conversation.
  // Decided from the item, not from the last redraw: family.js draws before the panel does.
  const holdsFamily = (id) => {
    if (hidden) return false;
    const family = window.ConsoleFamily.model(state.items, id);
    return Boolean(family && !family.parent && family.rows.length);
  };

  return { render, attach, backgroundIds, markBackground, holdsFamily };
})();
