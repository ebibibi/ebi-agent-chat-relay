// Secretary mode (ADR-0024): a second display mode beside the classic lists. Its home is one
// screen built for decisions, not threads — the server sends it with every board (secretary.py),
// built without a model from the decision requests the asking agents wrote themselves:
//   🔴 Decide now / 👀 To review — cards of at most six lines (what to decide, what it is about,
//      the choices, the deadline), one button per choice, the full request folded under
//      "More". An answer goes to the asking thread through the same reply endpoint as the
//      detail pane, restating the question, and closes the request.
//   📭 Waiting without a request — listed by title only; nothing is summarised for them.
//   🤖 Agents at work (one line each) · ✅ Finished today (🏁 can be confirmed)
// The classic mode is untouched: with the mode off this file adds only the switch in the menu
// and a silent "opened a thread" record. The trial's metrics (which threads were opened, which
// answers came from a card) are posted to /secretary/events as metadata only — never the text.
// Loaded before app.js, which calls on(), rows(), onBoard(), nav(), switcher(), title(), logOpen().
"use strict";

window.ConsoleSecretary = (() => {
  const KEY = "consoleMode";
  const VIEW = "secretary";
  const STRINGS = {
    en: {
      title: "🗂 Secretary", mode: "Display mode", classic: "Classic", secretary: "Secretary",
      decide: "🔴 Decide now", review: "👀 To review", working: "🤖 Agents at work", done: "✅ Finished today",
      unexplained: "📭 Waiting on you, without a request",
      unexplained_note: "The agent did not write what it needs. Open the thread to see.",
      calm: "🌿 Nothing to decide right now.", calm_more: "What remains is moving on its own.",
      about: "About: ", deadline: "Deadline: ", sep: ": ", recommended: "recommended", more_detail: "More ▾",
      own_answer: "✏️ Another answer", reply_ph: "Your answer…", send: "Send", open: "Open the thread",
      sent: "Sent — the agent carries on", since: (r) => `asked ${r}`, confirm: "✓ Confirm done",
      show_more: (n) => `Show ${n} more`, answer: (q, a) => `Answer to "${q}": ${a}`,
      none_working: "No agent is working right now.", none_done: "Nothing finished yet today.",
      list_more: (n) => `and ${n} more`,
      reasons: { overdue: "overdue", due_soon: "due within 24h", priority: "high priority" },
      kind: { waiting: "❓ question", action: "📋 only you can do it", review: "👀 to review" },
      status: { waiting: "❓ asked you", action: "📋 only you can do it", review: "👀 to review", error: "⚠️ failed" },
      detail_parts: [["about", "What this is about"], ["now", "What is happening, and why now"],
        ["ask", "What to decide"], ["choices", "The choices and what each leads to"],
        ["recommendation", "Recommendation and why"], ["materials", "Material to decide with"],
        ["deadline", "Deadline, and what happens without a decision"]],
    },
    ja: {
      title: "🗂 秘書", mode: "表示モード", classic: "従来", secretary: "秘書",
      decide: "🔴 今決めること", review: "👀 見てほしいもの", working: "🤖 AIが進めていること", done: "✅ 今日終わったこと",
      unexplained: "📭 説明のない依頼",
      unexplained_note: "AIが判断依頼書を書いていません。スレッドを開いて確かめてください。",
      calm: "🌿 今決めることはありません。", calm_more: "残りはAIが自分で進めています。",
      about: "何の話：", deadline: "期限：", sep: "：", recommended: "おすすめ", more_detail: "詳しく ▾",
      own_answer: "✏️ 別の答え", reply_ph: "答えを書く…", send: "送る", open: "元のスレッドを開く",
      sent: "送りました — AIが続きを進めます", since: (r) => `${r}から待ち`, confirm: "✓ 完了にする",
      show_more: (n) => `ほか ${n} 件を表示`, answer: (q, a) => `「${q}」への回答: ${a}`,
      none_working: "いまAIが進めている作業はありません。", none_done: "今日はまだ終わったものがありません。",
      list_more: (n) => `ほか ${n} 件`,
      reasons: { overdue: "期限超過", due_soon: "24時間以内に期限", priority: "優先度が高い" },
      kind: { waiting: "❓ 質問", action: "📋 本人しかできない作業", review: "👀 確認" },
      status: { waiting: "❓ AIが質問中", action: "📋 本人しかできない作業", review: "👀 確認待ち", error: "⚠️ 失敗" },
      detail_parts: [["about", "1. これは何の話か"], ["now", "2. いま何が起きていて、なぜ今聞くのか"],
        ["ask", "3. 決めてほしいこと"], ["choices", "4. 選択肢と、それぞれ選ぶと何が起きるか"],
        ["recommendation", "5. おすすめと理由"], ["materials", "6. 判断材料"],
        ["deadline", "7. 期限と、決めないとどうなるか"]],
    },
  };
  const S = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  const on = () => localStorage.getItem(KEY) === VIEW;
  let summary = null;
  // Cards answered here, until the board shows the thread moving: id -> the text sent.
  const sent = new Map();
  const drafts = {};
  // Per request: the detail is unfolded, the person is writing an answer of their own.
  const openDetail = new Set();
  const writingOwn = new Set();
  // Card blocks the person expanded past the first few.
  const showAll = new Set();
  const FIRST_CARDS = 3;

  function onBoard(next) {
    summary = next;
    // Once a sent card has left "decide now", nothing needs to remember it.
    const live = new Set([...((next && next.decide) || []), ...((next && next.review) || [])].map((c) => c.request_id));
    for (const id of [...sent.keys()]) if (!live.has(id)) sent.delete(id);
    for (const set of [openDetail, writingOwn]) for (const id of [...set]) if (!live.has(id)) set.delete(id);
  }

  function record(kind, id, extra = {}) {
    // Metrics are best-effort: a failed record never gets in the person's way.
    api("/secretary/events", { method: "POST", body: { kind, item_id: id, mode: on() ? VIEW : "classic", ...extra } })
      .catch((err) => console.warn("secretary: event not recorded", err));
  }
  const logOpen = (id) => { if (id) record("open", id); };

  function setMode(next) {
    localStorage.setItem(KEY, next);
    if (next === VIEW) setView(VIEW);
    else setView(state.view === VIEW ? "focus" : state.view);
  }

  // The switch in the menu, in both modes.
  function switcher() {
    const s = S();
    const chip = (key, label) => h("button", { type: "button", class: "chip" + ((on() ? VIEW : "classic") === key ? " on" : ""),
      "aria-pressed": (on() ? VIEW : "classic") === key ? "true" : "false", onclick: () => setMode(key), text: label });
    return h("div", { class: "mode-switch", role: "group", "aria-label": s.mode },
      h("span", { class: "dim", text: s.mode }), chip("classic", s.classic), chip(VIEW, s.secretary));
  }

  // The secretary home as a menu entry, only in secretary mode.
  function nav() {
    if (!on()) return null;
    const count = summary ? summary.decide.length + (summary.decide_more || 0) + summary.review.length : 0;
    return h("button", { class: state.view === VIEW ? "active" : "", "aria-current": state.view === VIEW ? "page" : null,
      onclick: () => setView(VIEW) }, h("span", { text: S().title }),
      h("span", { class: "count" + (count ? " hot" : ""), text: count || "" }));
  }

  const title = () => S().title;

  // The same cards are drawn in the list and in an open family's situation panel.
  function redraw() {
    render();
    const item = state.open && state.items.find((i) => i.id === state.open);
    if (item && window.ConsoleSituation) window.ConsoleSituation.render(item);
  }
  // What the panel's redraw key needs to know about the cards' own state.
  const stateKey = () => JSON.stringify([[...sent], [...writingOwn]]);

  // What reaches the thread: the question restated, so the agent knows which one is answered.
  const answerText = (card, text) => S().answer(card.card.title || card.item_title, text);

  async function answer(card, text, label, button) {
    text = (text || "").trim();
    if (!text) return;
    if (button) button.disabled = true;
    try {
      await api(`/items/${encodeURIComponent(card.id)}/reply`, { method: "POST",
        body: { text: answerText(card, text), request_id: card.request_id } });
      sent.set(card.request_id, label || text);
      delete drafts[card.request_id];
      const waited = card.since ? Math.max(0, Math.round((Date.now() - Date.parse(card.since)) / 1000)) : null;
      record("answer", card.id, Number.isFinite(waited) ? { waited_seconds: waited } : {});
      toast(S().sent);
      redraw();
      refresh();
    } catch (err) {
      toast(err.message);
      if (button) button.disabled = false;
    }
  }

  // The folded full text: the seven parts, Markdown (the draft, the diff, the table) rendered safely.
  function detailBlock(card) {
    const s = S();
    const parts = s.detail_parts.filter(([key]) => card.detail[key]);
    return h("details", { class: "sec-detail", open: openDetail.has(card.request_id) ? "" : null,
      ontoggle: (e) => { if (e.target.open) openDetail.add(card.request_id); else openDetail.delete(card.request_id); } },
      h("summary", { text: s.more_detail }),
      ...parts.map(([key, heading]) => h("section", { class: "sec-part" },
        h("h4", { text: heading }), h("div", { class: "md" }, MD.render(card.detail[key])))));
  }

  function replyForm(card) {
    const s = S();
    const input = h("textarea", { class: "sec-reply", rows: "1", maxlength: "4000", placeholder: s.reply_ph,
      "aria-label": s.reply_ph, oninput: (e) => { drafts[card.request_id] = e.target.value; } });
    input.value = drafts[card.request_id] || "";
    const sendBtn = h("button", { type: "submit", class: "btn primary", text: s.send });
    const form = h("form", { class: "sec-form", onsubmit: (e) => { e.preventDefault(); answer(card, input.value, null, sendBtn); } },
      input, sendBtn);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    return form;
  }

  // The card: at most six lines — what to decide, what it is about, the choices, the deadline —
  // then one button per choice and the folded detail. Answerable without opening anything.
  function decisionCard(card) {
    const s = S();
    const c = card.card;
    const head = h("div", { class: "sec-meta" },
      h("span", { class: "tag", text: s.kind[card.kind] || card.kind }),
      ...card.reasons.map((r) => s.reasons[r]).filter(Boolean).map((r) => h("span", { class: "tag hot", text: r })),
      card.since ? h("span", { class: "dim", text: s.since(rel(card.since)) }) : null,
      card.place ? h("span", { class: "tag place", text: `📍 ${card.place}` }) : null,
      h("button", { type: "button", class: "guide-link sec-thread", title: s.open, onclick: () => openItem(card.id),
        text: `↗ ${card.item_title}` }));
    const body = [
      h("h3", { class: "sec-title", text: `${card.kind === "review" ? "👀" : "🔴"} ${c.title}` }),
      h("div", { class: "sec-context" }, h("span", { class: "dim", text: s.about }), document.createTextNode(c.context)),
      h("ul", { class: "sec-options" }, ...c.options.map((o) => h("li", { class: o.recommended ? "rec" : "" },
        h("b", { text: o.label }), o.recommended ? h("span", { class: "tag rec", text: s.recommended }) : null,
        document.createTextNode(`${s.sep}${o.effect}`)))),
      h("div", { class: "sec-deadline" }, h("span", { class: "dim", text: s.deadline }), document.createTextNode(c.deadline)),
    ];
    if (sent.has(card.request_id)) {
      return h("li", { class: "sec-card sent", "data-item-id": card.id }, head, ...body,
        h("div", { class: "sec-sent", text: `✅ ${s.sent}: ${sent.get(card.request_id)}` }));
    }
    const buttons = c.options.map((o) => h("button", { type: "button", class: o.recommended ? "btn primary" : "btn",
      onclick: (e) => answer(card, `${o.label} — ${o.effect}`, o.label, e.currentTarget), text: o.label }));
    const writing = writingOwn.has(card.request_id);
    buttons.push(h("button", { type: "button", class: "chip", "aria-expanded": writing ? "true" : "false",
      onclick: () => { if (writing) writingOwn.delete(card.request_id); else writingOwn.add(card.request_id); redraw(); },
      text: s.own_answer }));
    return h("li", { class: "sec-card", "data-item-id": card.id }, head, ...body,
      h("div", { class: "sec-buttons" }, ...buttons), writing ? replyForm(card) : null, detailBlock(card));
  }

  function line(entry, extra) {
    return h("li", { class: "sec-line" },
      h("button", { type: "button", class: "guide-link", onclick: () => openItem(entry.id), text: entry.title || entry.id }),
      entry.since ? h("span", { class: "dim", text: rel(entry.since) }) : null, extra || null);
  }

  function block(cls, heading, entries, empty, more, note) {
    const s = S();
    return h("li", { class: `sec-block ${cls}` }, h("div", { class: "sec-head", text: heading }),
      note ? h("div", { class: "dim sec-note", text: note }) : null,
      entries.length ? h("ul", { class: "sec-lines" }, ...entries) : h("div", { class: "dim", text: empty }),
      more ? h("div", { class: "dim", text: s.list_more(more) }) : null);
  }

  function cardBlock(cls, heading, cards, more) {
    const s = S();
    const shown = showAll.has(cls) ? cards : cards.slice(0, FIRST_CARDS);
    const hidden = cards.length - shown.length + (more || 0);
    return h("li", { class: `sec-block ${cls}` }, h("div", { class: "sec-head", text: heading }),
      h("ul", { class: "sec-cards" }, ...shown.map(decisionCard)),
      hidden ? h("button", { type: "button", class: "guide-link", onclick: () => { showAll.add(cls); render(); },
        text: s.show_more(hidden) }) : null);
  }

  // The rows app.js draws for the secretary view: blocks only, no thread rows.
  function rows() {
    const s = S();
    const sum = summary || { decide: [], review: [], unexplained: [], working: [], done_today: [], calm: true };
    const out = [];
    if (sum.decide.length) out.push(cardBlock("sec-decide", s.decide, sum.decide, sum.decide_more));
    else out.push(h("li", { class: "sec-block sec-decide" }, h("div", { class: "sec-head", text: s.decide }),
      h("div", { class: "guide-calm", text: s.calm }), sum.calm ? h("div", { class: "dim", text: s.calm_more }) : null));
    if (sum.review.length) out.push(cardBlock("sec-review", s.review, sum.review, sum.review_more));
    if (sum.unexplained.length) {
      out.push(block("sec-unexplained", s.unexplained, sum.unexplained.map((e) => line(e,
        h("span", { class: "tag", text: s.status[e.status] || e.status }))), "", sum.unexplained_more, s.unexplained_note));
    }
    const confirm = (entry) => entry.candidate ? h("button", { type: "button", class: "chip",
      onclick: () => window.ConsoleFinish.finish(entry.id), text: s.confirm }) : null;
    out.push(block("sec-working", s.working, sum.working.map((e) => line(e)), s.none_working, sum.working_more));
    out.push(block("sec-done", s.done, sum.done_today.map((e) => line(e, confirm(e))), s.none_done, 0));
    return out.map((node) => ({ node }));
  }

  // The open requests of some threads (a family), drawn as the same cards: situation.js shows
  // them as "needed from you", so one request has one card wherever it appears.
  function cardsFor(threadIds) {
    const all = summary ? [...summary.decide, ...summary.review] : [];
    return all.filter((c) => threadIds.has(c.thread_id));
  }

  return { on, onBoard, rows, nav, switcher, title, logOpen, cardsFor, card: decisionCard, stateKey, VIEW };
})();
