// Focus session (canvas mode, ADR-0025): the open decisions one at a time, filling the screen.
// The queue is the plan the secretary prepared (PUT /api/canvas/session: order by context, with
// material per decision), followed by any other open request; without a plan, the open requests
// in the secretary home's order. Each decision shows its card large, its material drawn with the
// canvas renderers (canvas.js draw()) and its full request folded below.
// Keys: 1-3 choose · C answer in your own words · S later · → next · Esc end.
// An answer goes to the asking thread through the secretary-mode answer path (secretary.js),
// closing its request. When the session ends the secretary is told what was settled.
"use strict";

window.ConsoleSession = (() => {
  const STRINGS = {
    en: {
      title: "🎯 Focus session", left: (m) => `${m} min left`, over: (m) => `${m} min over`,
      position: (i, n) => `decision ${i} of ${n}`, end: "✕ End", next_up: "Next (similar contexts together)",
      agents: "Agents at work", results: "This session", none: "Nothing else waiting.",
      no_agents: "No agent is working.", nothing_yet: "Nothing settled yet.",
      keys: "1 2 3 decide · C answer in your words · S later · → next · Esc end",
      own: "Your answer (Ctrl+Enter sends)", send: "Send", recommended: "recommended",
      decided: "✅", later: "⏭ later", done_title: "🎉 All done for this session",
      done_body: "Each answer went to its thread. The secretary has been told.", close: "Back to the canvas",
      empty: "No open decisions right now.", deadline: "Deadline: ", irreversible: "can't be undone",
      told: (d, l) => `Sent to the secretary: ${d} decided, ${l} for later`, open_thread: "↗ Open the thread (the session waits)",
      more: "Full request ▾", min: (m) => `${m} min`,
    },
    ja: {
      title: "🎯 集中セッション", left: (m) => `あと ${m}分`, over: (m) => `${m}分超過`,
      position: (i, n) => `決めること ${n}件のうち${i}件目`, end: "✕ 終える", next_up: "このあと（似た文脈でまとめてあります）",
      agents: "AI が動いているもの", results: "この回の成果", none: "ほかに待っているものはありません。",
      no_agents: "いま動いている AI はありません。", nothing_yet: "まだ決めたものはありません。",
      keys: "1 2 3 で即決 · C 条件をつけて答える · S 後回し · → 次へ · Esc 終える",
      own: "自分の言葉で答える（Ctrl+Enter で送信）", send: "送る", recommended: "おすすめ",
      decided: "✅", later: "⏭ 後回し", done_title: "🎉 この回の決めごとは終わりです",
      done_body: "答えはそれぞれのスレッドに届けました。秘書にも伝えてあります。", close: "画面に戻る",
      empty: "いま決めることはありません。", deadline: "期限：", irreversible: "戻せない",
      told: (d, l) => `秘書に伝えました：決定 ${d}件・後回し ${l}件`, open_thread: "↗ 元のスレッドを開く（セッションは中断して待ちます）",
      more: "詳しい依頼書 ▾", min: (m) => `${m}分`,
    },
  };
  const S = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  let open_ = false;
  let queue = []; // [{ card, plan }]
  let index = 0;
  let decided = []; // [{ title, answer, thread_id }]
  let later = [];
  let startedAt = 0;
  let minutes = 15;
  let writing = false;
  let busy = false;
  let timer = null;

  function build() {
    const plan = (window.ConsoleCanvas.session && window.ConsoleCanvas.session()) || null;
    const cards = window.ConsoleSecretary.all();
    const byThread = new Map(cards.map((c) => [String(c.thread_id), c]));
    const out = [];
    const seen = new Set();
    for (const p of (plan && plan.items) || []) {
      const card = byThread.get(String(p.thread_id));
      if (card && !seen.has(card.request_id)) { out.push({ card, plan: p }); seen.add(card.request_id); }
    }
    for (const card of cards) if (!seen.has(card.request_id)) out.push({ card, plan: null });
    minutes = (plan && plan.minutes) || 15;
    return out;
  }

  function open() {
    suspended_ = false;
    queue = build();
    index = 0; decided = []; later = []; writing = false; busy = false;
    startedAt = Date.now();
    open_ = true;
    clearInterval(timer);
    timer = setInterval(() => { if (open_) paintTop(); }, 15_000);
    paint();
  }

  // Opening a thread from the session suspends it: the queue, the answers so far and the clock
  // stay, and the canvas and the thread offer a way back (canvas.js).
  let suspended_ = false;
  function suspend() {
    if (!open_) return;
    open_ = false;
    suspended_ = true;
    $("cv-session")?.remove();
  }
  function resume() {
    if (!suspended_) return;
    suspended_ = false;
    open_ = true;
    paint();
  }
  function openThread(id) {
    suspend();
    openItem(id);
    render();
  }

  async function end() {
    if (!open_ && !suspended_) return;
    open_ = false;
    suspended_ = false;
    clearInterval(timer);
    $("cv-session")?.remove();
    if (decided.length || later.length) {
      try {
        await api("/canvas/session/finish", { method: "POST", body: {
          decided: decided.map((d) => ({ title: d.title, answer: d.answer })),
          later: later.map((d) => ({ title: d.title })) } });
        toast(S().told(decided.length, later.length));
      } catch (err) { toast(err.message); }
    }
    refresh();
  }

  const current = () => queue[index] || null;

  function advance() {
    writing = false;
    index += 1;
    paint();
  }

  async function choose(option) {
    const entry = current();
    if (!entry || busy) return;
    const opt = entry.card.card.options[option];
    if (!opt) return;
    await send(entry, `${opt.label} — ${opt.effect}`, opt.label);
  }

  async function send(entry, text, label) {
    busy = true;
    try {
      if (!(await window.ConsoleSecretary.answer(entry.card, text, label))) return;
      decided.push({ title: entry.card.card.title, answer: label || text, thread_id: entry.card.thread_id });
      advance();
    } finally { busy = false; }
  }

  function postpone() {
    const entry = current();
    if (!entry) return;
    later.push({ title: entry.card.card.title, thread_id: entry.card.thread_id });
    advance();
  }

  // -- drawing ------------------------------------------------------------------
  function paint() {
    let box = $("cv-session");
    if (!open_) { box?.remove(); return; }
    if (!box) {
      box = h("div", { id: "cv-session", class: "cv-session", role: "dialog", "aria-label": S().title });
      document.body.append(box);
    }
    box.replaceChildren(top(), h("div", { class: "cv-ss-body" }, main(), side()));
    const input = box.querySelector(".cv-ss-own textarea");
    if (input) input.focus();
  }

  function paintTop() {
    const old = document.querySelector("#cv-session > .cv-ss-top");
    if (old) old.replaceWith(top());
  }

  function top() {
    const s = S();
    const spent = Math.floor((Date.now() - startedAt) / 60000);
    const remaining = minutes - spent;
    const total = queue.length;
    const shown = Math.min(index + 1, total);
    return h("header", { class: "cv-ss-top" },
      h("b", { text: s.title }),
      h("span", { class: "dim", text: `${remaining >= 0 ? s.left(remaining) : s.over(-remaining)} · ${total ? s.position(shown, total) : ""}` }),
      h("div", { class: "cv-ss-progress" }, h("span", { style: { width: `${total ? (index / total) * 100 : 100}%` } })),
      h("button", { type: "button", class: "btn", onclick: end, text: s.end }),
      window.ConsoleScreenAI.bar());
  }

  function main() {
    const s = S();
    const entry = current();
    if (!entry) {
      return h("section", { class: "cv-ss-main cv-ss-done" },
        h("h1", { text: queue.length ? s.done_title : s.empty }),
        queue.length ? h("p", { text: s.done_body }) : null,
        h("button", { type: "button", class: "btn primary big", onclick: end, text: s.close }));
    }
    const { card, plan } = entry;
    const c = card.card;
    const tags = [];
    if (plan && plan.group) tags.push(h("span", { class: "tag", text: plan.group }));
    // Only the negative forms: "元に戻せます" (can be undone) must not be flagged.
    if (/戻せない|戻せません|取り消せない|取り消せません|cannot be undone|can't be undone|irreversible/i.test(c.context)) tags.push(h("span", { class: "tag hot", text: s.irreversible }));
    tags.push(h("span", { class: "tag", text: `${s.deadline}${c.deadline}` }));
    tags.push(h("button", { type: "button", class: "guide-link", title: s.open_thread, onclick: () => openThread(card.id),
      text: `↗ ${card.item_title}` }));
    const options = c.options.map((o, i) => h("button", { type: "button",
      class: "cv-ss-option" + (o.recommended ? " rec" : ""), onclick: () => choose(i), disabled: busy ? "" : null },
      h("span", { class: "cv-ss-key", text: String(i + 1) }),
      h("span", { class: "cv-ss-label", text: o.label + (o.recommended ? `（${s.recommended}）` : "") }),
      h("span", { class: "cv-ss-effect dim", text: o.effect })));
    const materials = (plan && plan.materials) || [];
    const drawn = materials.length ? h("div", { class: "cv-ss-materials" }, ...materials.map((m) =>
      h("article", { class: `cv-w cv-k-${m.kind}` }, h("div", { class: "cv-w-head" }, h("h3", { text: m.title })),
        h("div", { class: "cv-w-body" }, window.ConsoleCanvas.draw(m.kind, m.data))))) : null;
    const detailParts = ["about", "now", "recommendation", "materials"].filter((k) => card.detail && card.detail[k]);
    const detail = detailParts.length ? h("details", { class: "cv-ss-detail", open: materials.length ? null : "" },
      h("summary", { text: s.more }),
      ...detailParts.map((k) => h("div", { class: "md" }, MD.render(card.detail[k])))) : null;
    const own = writing ? ownForm(entry) : null;
    const files = window.ConsoleSecretary.filesBlock(card);
    if (window.ConsoleSecretary.nothingToSee(card)) {
      return h("section", { class: "cv-ss-main" },
        h("div", { class: "cv-ss-tags" }, ...tags),
        h("h1", { class: "cv-ss-title", text: c.title }),
        h("p", { class: "cv-ss-context", text: c.context }),
        h("p", { class: "cv-ss-nomaterial", text: window.ConsoleSecretary.strings().in_thread }),
        h("div", { class: "cv-ss-options" }, h("button", { type: "button", class: "btn primary big",
          onclick: () => openThread(card.id), text: window.ConsoleSecretary.strings().open_to_check })),
        h("div", { class: "cv-ss-keys dim", text: s.keys }));
    }
    return h("section", { class: "cv-ss-main" },
      h("div", { class: "cv-ss-tags" }, ...tags),
      h("h1", { class: "cv-ss-title", text: c.title }),
      h("p", { class: "cv-ss-context", text: c.context }),
      plan && plan.why ? h("p", { class: "dim", text: plan.why }) : null,
      h("div", { class: "cv-ss-options" }, ...options),
      own, files, drawn, detail,
      h("div", { class: "cv-ss-keys dim", text: s.keys }));
  }

  function ownForm(entry) {
    const s = S();
    const input = h("textarea", { rows: "2", maxlength: "4000", placeholder: s.own, "aria-label": s.own });
    const submit = () => { const text = input.value.trim(); if (text) send(entry, text, text.slice(0, 80)); };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); submit(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); writing = false; paint(); }
    });
    return h("div", { class: "cv-ss-own" }, input,
      h("button", { type: "button", class: "btn primary", onclick: submit, text: s.send }));
  }

  function side() {
    const s = S();
    const rest = queue.slice(index + 1);
    const running = state.items.filter((i) => (i.running || i.queued) && !i.canvas_secretary).slice(0, 8);
    const list = (entries, empty) => entries.length ? h("ul", { class: "cv-ss-list" }, ...entries) : h("div", { class: "dim", text: empty });
    return h("aside", { class: "cv-ss-side" },
      h("h4", { text: s.next_up }),
      list(rest.map((e) => h("li", {}, h("span", { text: e.card.kind === "review" ? "👀" : "🔴" }),
        h("span", { text: e.card.card.title }),
        e.plan && e.plan.group ? h("span", { class: "dim", text: ` · ${e.plan.group}` }) : null)), s.none),
      h("h4", { text: s.agents }),
      list(running.map((i) => h("li", {}, h("span", { text: "⚙️" }), h("span", { text: i.title || i.thread_name || i.id }))), s.no_agents),
      h("h4", { text: s.results }),
      list([...decided.map((d) => h("li", {}, h("span", { text: s.decided }), h("span", { text: `${d.title} → ${d.answer}` }))),
        ...later.map((d) => h("li", { class: "dim" }, h("span", { text: s.later }), h("span", { text: ` ${d.title}` })))], s.nothing_yet));
  }

  // A /#focus/t<id> link in the request's text opens the thread behind the session: suspend it.
  document.addEventListener("click", (e) => {
    if (!open_) return;
    const anchor = e.target.closest && e.target.closest("#cv-session a[href]");
    if (anchor && window.ConsoleLinks.route(anchor.href)) suspend();
  }, true);

  // Keys, ahead of the Console's own shortcuts while the session is open.
  document.addEventListener("keydown", (e) => {
    if (!open_) return;
    const typing = e.target instanceof HTMLElement && e.target.matches("textarea, input");
    if (typing) return;
    let handled = true;
    if (e.key === "Escape") end();
    else if (["1", "2", "3"].includes(e.key)) choose(Number(e.key) - 1);
    else if (e.key === "c" || e.key === "C") { if (current()) { writing = true; paint(); } }
    else if (e.key === "s" || e.key === "S") postpone();
    else if (e.key === "ArrowRight") { if (current()) advance(); }
    else handled = false;
    if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  return { open, end, suspend, resume, repaint: () => paint(), isOpen: () => open_, suspended: () => suspended_ };
})();
