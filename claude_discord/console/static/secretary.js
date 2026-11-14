// Secretary mode (ADR-0024): a second display mode beside the classic lists. Its home is one
// screen built for decisions, not threads — the server sends it with every board (secretary.py),
// built without a model:
//   🔴 Decide now — at most three questions: what is asked, why, the recommended answer. A
//      button or a short reply goes to the thread through the same reply endpoint as the
//      detail pane, so the agent carries on exactly as if the person had typed it there.
//   👀 To review · 🤖 Agents at work (one line each) · ✅ Finished today (🏁 can be confirmed)
// Every card keeps a way into the original thread. The classic mode is untouched: with the
// mode off this file adds only the switch in the menu and a silent "opened a thread" record.
// The trial's metrics (which threads were opened, which answers came from a card) are posted
// to /secretary/events as metadata only — never the text.
// Loaded before app.js, which calls on(), rows(), onBoard(), nav(), switcher(), title(), logOpen().
"use strict";

window.ConsoleSecretary = (() => {
  const KEY = "consoleMode";
  const VIEW = "secretary";
  const STRINGS = {
    en: {
      title: "🗂 Secretary", mode: "Display mode", classic: "Classic", secretary: "Secretary",
      decide: "🔴 Decide now", review: "👀 To review", working: "🤖 Agents at work", done: "✅ Finished today",
      calm: "🌿 Nothing to decide right now.", calm_more: "What remains is moving on its own.",
      suggest: "Recommended", send_suggest: "Send the recommended answer", reply_ph: "Reply in a few words…",
      send: "Send", open: "Open the thread", sent: "Sent — the agent carries on", more: (n) => `${n} more → Focus`,
      later: (n) => `${n} waiting for something else`, since: (r) => `asked ${r}`, confirm: "✓ Confirm done",
      none_working: "No agent is working right now.", none_done: "Nothing finished yet today.",
      list_more: (n) => `and ${n} more`,
      reasons: { overdue: "overdue", due_soon: "due within 24h", priority: "high priority" },
      status: { waiting: "❓ asked you", action: "📋 only you can do it", error: "⚠️ failed" },
    },
    ja: {
      title: "🗂 秘書", mode: "表示モード", classic: "従来", secretary: "秘書",
      decide: "🔴 今決めること", review: "👀 見てほしいもの", working: "🤖 AIが進めていること", done: "✅ 今日終わったこと",
      calm: "🌿 今決めることはありません。", calm_more: "残りはAIが自分で進めています。",
      suggest: "おすすめ", send_suggest: "おすすめの答えで返す", reply_ph: "ひとことで返す…",
      send: "送る", open: "元のスレッドを開く", sent: "送りました — AIが続きを進めます", more: (n) => `ほか ${n} 件 → 今やること`,
      later: (n) => `ほかに ${n} 件は別のことを待っています`, since: (r) => `${r}から待ち`, confirm: "✓ 完了にする",
      none_working: "いまAIが進めている作業はありません。", none_done: "今日はまだ終わったものがありません。",
      list_more: (n) => `ほか ${n} 件`,
      reasons: { overdue: "期限超過", due_soon: "24時間以内に期限", priority: "優先度が高い" },
      status: { waiting: "❓ AIが質問中", action: "📋 本人しかできない作業", error: "⚠️ 失敗" },
    },
  };
  const S = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  const on = () => localStorage.getItem(KEY) === VIEW;
  let summary = null;
  // Cards answered here, until the board shows the thread moving: id -> the text sent.
  const sent = new Map();
  const drafts = {};

  function onBoard(next) {
    summary = next;
    // Once a sent card has left "decide now", nothing needs to remember it.
    const live = new Set(((next && next.decide) || []).map((d) => d.id));
    for (const id of [...sent.keys()]) if (!live.has(id)) sent.delete(id);
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
    const count = summary ? summary.decide.length + summary.more : 0;
    return h("button", { class: state.view === VIEW ? "active" : "", "aria-current": state.view === VIEW ? "page" : null,
      onclick: () => setView(VIEW) }, h("span", { text: S().title }),
      h("span", { class: "count" + (count ? " hot" : ""), text: count || "" }));
  }

  const title = () => S().title;

  async function answer(card, text, button) {
    text = (text || "").trim();
    if (!text) return;
    if (button) button.disabled = true;
    try {
      await api(`/items/${encodeURIComponent(card.id)}/reply`, { method: "POST", body: { text } });
      sent.set(card.id, text);
      delete drafts[card.id];
      const waited = card.since ? Math.max(0, Math.round((Date.now() - Date.parse(card.since)) / 1000)) : null;
      record("answer", card.id, Number.isFinite(waited) ? { waited_seconds: waited } : {});
      toast(S().sent);
      render();
      refresh();
    } catch (err) {
      toast(err.message);
      if (button) button.disabled = false;
    }
  }

  function decisionCard(card) {
    const s = S();
    const head = h("div", { class: "sec-meta" },
      h("span", { class: "tag", text: s.status[card.status] || card.status }),
      ...card.reasons.map((r) => s.reasons[r]).filter(Boolean).map((r) => h("span", { class: "tag hot", text: r })),
      card.since ? h("span", { class: "dim", text: s.since(rel(card.since)) }) : null,
      card.place ? h("span", { class: "tag place", text: `📍 ${card.place}` }) : null);
    const open = h("button", { type: "button", class: "btn sec-open", onclick: () => openItem(card.id), text: `↗ ${s.open}` });
    const body = [
      h("div", { class: "sec-thread", text: card.title }),
      h("div", { class: "sec-ask", text: card.ask }),
      card.why ? h("div", { class: "sec-why dim", text: card.why }) : null,
    ];
    if (sent.has(card.id)) {
      return h("li", { class: "sec-card sent", "data-item-id": card.id }, head, ...body,
        h("div", { class: "sec-sent", text: `✅ ${s.sent}: ${sent.get(card.id)}` }), open);
    }
    const input = h("textarea", { class: "sec-reply", rows: "1", maxlength: "4000", placeholder: s.reply_ph,
      "aria-label": s.reply_ph, oninput: (e) => { drafts[card.id] = e.target.value; } });
    input.value = drafts[card.id] || "";
    const sendBtn = h("button", { type: "submit", class: "btn primary", text: s.send });
    const form = h("form", { class: "sec-form", onsubmit: (e) => { e.preventDefault(); answer(card, input.value, sendBtn); } },
      input, sendBtn);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    const quick = [];
    if (card.suggest) {
      quick.push(h("button", { type: "button", class: "btn primary sec-suggest", title: s.send_suggest,
        onclick: (e) => answer(card, card.suggest, e.currentTarget) }, h("span", { class: "dim", text: `${s.suggest}: ` }),
        h("span", { text: card.suggest })));
    }
    for (const option of card.options) {
      if (option === card.suggest) continue;
      quick.push(h("button", { type: "button", class: "chip", onclick: (e) => answer(card, option, e.currentTarget), text: option }));
    }
    return h("li", { class: "sec-card", "data-item-id": card.id }, head, ...body,
      quick.length ? h("div", { class: "sec-quick" }, ...quick) : null, form, open);
  }

  function line(entry, extra) {
    return h("li", { class: "sec-line" },
      h("button", { type: "button", class: "guide-link", onclick: () => openItem(entry.id), text: entry.title || entry.id }),
      entry.since ? h("span", { class: "dim", text: rel(entry.since) }) : null, extra || null);
  }

  function block(cls, heading, entries, empty, more) {
    const s = S();
    return h("li", { class: `sec-block ${cls}` }, h("div", { class: "sec-head", text: heading }),
      entries.length ? h("ul", { class: "sec-lines" }, ...entries) : h("div", { class: "dim", text: empty }),
      more ? h("div", { class: "dim", text: s.list_more(more) }) : null);
  }

  // The rows app.js draws for the secretary view: blocks only, no thread rows.
  function rows() {
    const s = S();
    const sum = summary || { decide: [], more: 0, later: 0, review: [], working: [], done_today: [], calm: true };
    const decide = h("li", { class: "sec-block sec-decide" }, h("div", { class: "sec-head", text: s.decide }),
      sum.decide.length ? h("ul", { class: "sec-cards" }, ...sum.decide.map(decisionCard))
        : h("div", { class: "guide-calm", text: s.calm }),
      !sum.decide.length && sum.calm ? h("div", { class: "dim", text: s.calm_more }) : null,
      sum.more ? h("button", { type: "button", class: "guide-link", onclick: () => setView("focus"), text: s.more(sum.more) }) : null,
      sum.later ? h("div", { class: "dim", text: s.later(sum.later) }) : null);
    const confirm = (entry) => entry.candidate ? h("button", { type: "button", class: "chip",
      onclick: () => window.ConsoleFinish.finish(entry.id), text: s.confirm }) : null;
    const out = [decide];
    if (sum.review.length) out.push(block("sec-review", s.review, sum.review.map((e) => line(e)), "", sum.review_more));
    out.push(block("sec-working", s.working, sum.working.map((e) => line(e)), s.none_working, sum.working_more));
    out.push(block("sec-done", s.done, sum.done_today.map((e) => line(e, confirm(e))), s.none_done, 0));
    return out.map((node) => ({ node }));
  }

  return { on, onBoard, rows, nav, switcher, title, logOpen, VIEW };
})();
