// The "Goals" view: what the person is working toward, each goal with the threads that work
// toward it. The goals are ### headings in one Markdown note in the Ebi Workspace vault
// (GET /goals); this view only shows them and sends a person's edits back (POST /goals),
// which the server writes into the note with a revision check. Threads are the board's own
// rows, so opening, replying and finishing work as in every other list. Which goal a thread
// belongs to is worked out on the server (a person's choice, the thread's Workspace project,
// its parent); a person changes it with the 🧭 chip under the row, stored as a goal:<id> tag.
// Loaded before app.js, which calls rows() from viewRows() and note() from rowNode().
"use strict";

window.ConsoleGoals = (() => {
  const VIEW = "goals";
  const RELOAD_MS = 15000;
  const STR = {
    en: {
      title: "🧭 Goals", source: "Source:", active: (n, l) => (l ? `${n} of ${l} active` : `${n} active`),
      add: "＋ Write a goal", show_paused: "Show paused and done", hide_paused: "Hide paused and done",
      done_when: "Done when:", no_due: "no due date", due: (d, x) => `due ${d}${x}`, left: (n) => ` · ${n} d left`,
      overdue: (n) => ` · ${n} d over`, running: "⚡ running", yours: "🙋 yours", done7: "✅ done in 7 d",
      quiet: "⚠ quiet for 14 days", milestones: "Milestones", next: "next:", project_add: "＋ project",
      none_yet: "No thread works toward this goal yet.", outside: "Projects moving outside your goals",
      unlinked: "Not linked to a goal", deliverables: "📦", edit: "✏️ Edit", save: "Save", cancel: "Cancel",
      pause: "⏸ Pause", complete: "✅ Done", resume: "▶ Resume", up: "↑", down: "↓",
      f_title: "Goal", f_done: "Done when (how you will know)", f_due: "Due (YYYY-MM-DD or YYYY-MM)",
      over_limit: (l) => `This makes more than ${l} active goals. Pause one first?`,
      conflict: "The note changed elsewhere; reloaded. Try again.", saved: "Saved to the note",
      setup: "Choose the Markdown note that holds your goals (vault path, e.g. 02_Contexts/Me/goals.md). Each ### heading under ## Goals is a goal.",
      setup_save: "Use this note", no_workspace: "Goals need an Ebi Workspace vault.",
      missing: (p) => `The goals note ${p} is missing or hidden.`, loading: "Loading goals…",
      pick: "Goal", pick_auto: "Automatic", pick_none: "No goal", placed: { chosen: "your choice", project: "from the project", parent: "from the parent" },
      project_pick: "Link a Workspace project", state: { paused: "⏸ paused", done: "✅ done" },
    },
    ja: {
      title: "🧭 目標", source: "正本:", active: (n, l) => (l ? `進行中 ${n} / 上限 ${l}` : `進行中 ${n}`),
      add: "＋ 目標を書く", show_paused: "止めた・達成した目標も表示", hide_paused: "止めた・達成した目標を隠す",
      done_when: "達成の定義:", no_due: "期限なし", due: (d, x) => `期限 ${d}${x}`, left: (n) => ` · あと${n}日`,
      overdue: (n) => ` · ${n}日超過`, running: "⚡ 実行中", yours: "🙋 あなた待ち", done7: "✅ 7日で完了",
      quiet: "⚠ 14日動きなし", milestones: "マイルストーン", next: "次:", project_add: "＋ プロジェクト",
      none_yet: "この目標に紐づくスレッドはまだありません。", outside: "注力の外で動いているプロジェクト",
      unlinked: "どの目標にも紐づかない", deliverables: "📦", edit: "✏️ 直す", save: "保存", cancel: "やめる",
      pause: "⏸ 止める", complete: "✅ 達成", resume: "▶ 再開", up: "↑", down: "↓",
      f_title: "目標", f_done: "達成の定義（何をもって達成とするか）", f_due: "期限（YYYY-MM-DD か YYYY-MM）",
      over_limit: (l) => `進行中の目標が上限 ${l} を超えます。先にどれかを止めますか？`,
      conflict: "ノートが別の場所で更新されたので読み直しました。もう一度操作してください。", saved: "ノートに保存しました",
      setup: "目標を書いた Markdown ノートを選んでください（ボールト内のパス。例: 02_Contexts/自分/目標.md）。## 目標 の下の ### 見出しが1つずつ目標になります。",
      setup_save: "このノートを使う", no_workspace: "目標を使うには Ebi Workspace のボールトが必要です。",
      missing: (p) => `目標のノート ${p} が見つからないか、非表示に設定されています。`, loading: "目標を読み込み中…",
      pick: "目標", pick_auto: "自動", pick_none: "目標なし", placed: { chosen: "あなたが選択", project: "プロジェクトから", parent: "親スレッドから" },
      project_pick: "Workspace のプロジェクトを紐づける", state: { paused: "⏸ 止めている", done: "✅ 達成" },
    },
  };
  const L = () => STR[document.documentElement.lang === "ja" ? "ja" : "en"];

  let data = null;
  let loadedAt = 0;
  let loading = null;
  let editing = null; // a goal id, "new", or null
  let showPaused = localStorage.getItem("goalsShowPaused") === "1";

  const isView = () => typeof state !== "undefined" && state.view === VIEW;

  async function load(force = false) {
    if (loading) return loading;
    if (!force && data && Date.now() - loadedAt < RELOAD_MS) return null;
    loading = (async () => {
      try { data = await api("/goals"); }
      catch (err) { data = { available: false, reason: "error", error: err.message }; }
      finally { loadedAt = Date.now(); loading = null; }
      if (isView()) render();
    })();
    return loading;
  }

  // The board moved: the goals' threads may have, too.
  function onBoard() { if (isView()) load(); }

  // -- writing ---------------------------------------------------------------------------
  async function send(action, { quiet = false } = {}) {
    try {
      await api("/goals", { method: "POST", body: { ...action, revision: data?.note?.revision } });
      if (!quiet) toast(L().saved);
      editing = null;
    } catch (err) {
      toast(err.status === 409 ? L().conflict : err.message);
    }
    await load(true);
  }

  async function chooseGoal(item, goalId) {
    const tags = (item.tags || []).filter((t) => !t.startsWith("goal:"));
    if (goalId) tags.push(`goal:${goalId}`);
    await patch(item.id, { tags: tags.sort() });
    await load(true);
  }

  // -- small pieces ----------------------------------------------------------------------
  function daysUntil(iso) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return Math.round((new Date(`${iso}T00:00:00`) - today) / 86400000);
  }

  function dueChip(goal) {
    const l = L();
    if (!goal.due) return h("span", { class: "goal-chip", text: l.no_due });
    const n = daysUntil(goal.due);
    const extra = goal.state !== "active" ? "" : n < 0 ? l.overdue(-n) : l.left(n);
    return h("span", { class: "goal-chip due" + (n < 0 && goal.state === "active" ? " over" : ""), text: l.due(goal.due, extra) });
  }

  function progress(project) {
    const m = project.milestones;
    if (!m || !m.total) return null;
    const l = L();
    const pct = Math.round((m.done / m.total) * 100);
    return h("div", { class: "goal-progress" },
      h("span", { class: "dim", text: `${l.milestones} · ${project.name}` }),
      h("span", { class: "goal-bar", role: "img", "aria-label": `${m.done}/${m.total}` },
        h("i", { style: { width: `${pct}%` } })),
      h("span", { text: `${m.done}/${m.total}` }),
      m.next ? h("span", { class: "dim", text: `${l.next} ${m.next.name}${m.next.date ? ` 📅${m.next.date.slice(5)}` : ""}` }) : null);
  }

  function counts(c, quiet) {
    const l = L();
    return h("div", { class: "goal-counts" },
      h("span", { class: "c-run", text: `${l.running} ${c.running}` }),
      h("span", { class: "c-you" + (c.yours ? " hot" : ""), text: `${l.yours} ${c.yours}` }),
      h("span", { class: "c-done", text: `${l.done7} ${c.done}` }),
      quiet ? h("span", { class: "c-quiet", text: l.quiet }) : null);
  }

  const SHOWN_DELIVERABLES = 5;
  // Only web links and links inside the Console; anything else is shown as text.
  const safeUrl = (url) => typeof url === "string" && (/^https?:\/\//i.test(url) || url.startsWith("/#"));
  function deliverables(list) {
    if (!list || !list.length) return null;
    const more = list.length - SHOWN_DELIVERABLES;
    return h("div", { class: "goal-deliverables" }, h("span", { text: L().deliverables }),
      more > 0 ? h("span", { class: "dim goal-more", text: `+${more}` }) : null,
      ...list.slice(0, SHOWN_DELIVERABLES).map((d) => safeUrl(d.url)
        ? h("a", { href: d.url, target: d.url.startsWith("/#") ? null : "_blank", rel: "noopener", text: d.title || d.url })
        : h("span", { class: "dim", text: d.title || d.file })));
  }

  function projectLinks(goal) {
    const l = L();
    const chips = goal.projects.map((p) => h("span", { class: "goal-project" },
      h("a", { href: `#note/${encodeURI(`02_Contexts/${p.path}/status.md`)}`, text: `🗂️ ${p.path}` }),
      h("button", { type: "button", class: "goal-x", title: "✕", "aria-label": "✕",
        onclick: () => send({ op: "unlink", id: goal.id, project: p.path }) }, "✕")));
    const add = h("button", { type: "button", class: "btn ghost goal-add-project", text: l.project_add,
      onclick: (e) => projectPicker(goal, e.currentTarget) });
    return h("div", { class: "goal-projects" }, ...chips, add);
  }

  function popup(anchor, label, options) {
    document.querySelector(".ws-bind-pop")?.remove();
    const input = h("input", { type: "search", class: "ws-bind-search", placeholder: label, "aria-label": label });
    const list = h("div", { class: "ws-bind-list", role: "listbox" });
    const close = () => { pop.remove(); document.removeEventListener("mousedown", outside, true); };
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      list.replaceChildren(...options.filter((o) => !q || o.label.toLowerCase().includes(q)).map((o) =>
        h("button", { type: "button", role: "option", class: "ws-bind-option" + (o.on ? " on" : ""),
          "aria-selected": o.on ? "true" : "false", onclick: async () => { close(); await o.run(); }, text: o.label })));
    };
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    const pop = h("div", { class: "ws-bind-pop goal-pop", role: "dialog", "aria-label": label }, input, list);
    const outside = (e) => { if (!pop.contains(e.target) && e.target !== anchor) close(); };
    document.addEventListener("mousedown", outside, true);
    anchor.after(pop);
    draw();
    input.focus();
  }

  function projectPicker(goal, anchor) {
    const linked = new Set(goal.projects.map((p) => p.path));
    const entries = (window.ConsoleWorkspace?.entries() || []).filter((e) => e.kind === "project" && !linked.has(e.path));
    popup(anchor, L().project_pick, entries.map((e) => ({ label: `📁 ${e.path}`,
      run: () => send({ op: "link", id: goal.id, project: e.path }) })));
  }

  function goalPicker(item, anchor) {
    const l = L();
    const chosen = (item.tags || []).find((t) => t.startsWith("goal:"))?.slice(5) || null;
    const goals = (data?.goals || []).filter((g) => g.state === "active" || g.id === chosen);
    popup(anchor, l.pick, [
      { label: l.pick_auto, on: !chosen, run: () => chooseGoal(item, null) },
      { label: `🚫 ${l.pick_none}`, on: chosen === "none", run: () => chooseGoal(item, "none") },
      ...goals.map((g) => ({ label: `${g.number || "🧭"} ${g.title}`, on: chosen === g.id, run: () => chooseGoal(item, g.id) })),
    ]);
  }

  // -- the form that writes or edits a goal ----------------------------------------------
  function form(goal) {
    const l = L();
    const title = h("input", { type: "text", class: "goal-in", "aria-label": l.f_title, placeholder: l.f_title, value: goal?.title || "" });
    const done = h("textarea", { class: "goal-in", rows: 2, "aria-label": l.f_done, placeholder: l.f_done });
    done.value = goal?.done_when || "";
    const due = h("input", { type: "text", class: "goal-in due", "aria-label": l.f_due, placeholder: l.f_due, value: goal?.due || "" });
    const save = async () => {
      if (!title.value.trim()) { title.focus(); return; }
      if (!goal) {
        if (data.limit && data.active >= data.limit && !confirm(l.over_limit(data.limit))) return;
        await send({ op: "add", title: title.value, done_when: done.value, due: due.value });
        return;
      }
      const change = { op: "update", id: goal.id };
      if (title.value.trim() !== goal.title) change.title = title.value;
      if (done.value.trim() !== (goal.done_when || "")) change.done_when = done.value;
      if (due.value.trim() !== (goal.due || "")) change.due = due.value;
      if (Object.keys(change).length === 2) { editing = null; render(); return; }
      await send(change);
    };
    const keys = (e) => { if (isSendKey(e)) { e.preventDefault(); save(); } if (e.key === "Escape") { editing = null; render(); } };
    for (const el of [title, done, due]) el.addEventListener("keydown", keys);
    setTimeout(() => title.focus(), 0);
    return h("div", { class: "goal-form" }, title, done, due,
      h("div", { class: "goal-form-buttons" },
        h("button", { type: "button", class: "btn primary", text: l.save, onclick: save }),
        h("button", { type: "button", class: "btn ghost", text: l.cancel, onclick: () => { editing = null; render(); } })));
  }

  function card(goal, index, total) {
    const l = L();
    if (editing === goal.id) return h("li", { class: "goal-card editing" }, form(goal));
    const stateButtons = goal.state === "active"
      ? [h("button", { type: "button", class: "btn ghost", text: l.pause, onclick: () => send({ op: "state", id: goal.id, state: "paused" }) }),
        h("button", { type: "button", class: "btn ghost", text: l.complete, onclick: () => send({ op: "state", id: goal.id, state: "done" }) })]
      : [h("button", { type: "button", class: "btn ghost", text: l.resume, onclick: () => send({ op: "state", id: goal.id, state: "active" }) })];
    const tools = h("details", { class: "goal-tools" }, h("summary", { text: "•••", "aria-label": l.edit }),
      h("div", { class: "goal-tools-body" },
        h("button", { type: "button", class: "btn ghost", text: l.edit, onclick: () => { editing = goal.id; render(); } }),
        h("button", { type: "button", class: "btn ghost", text: l.up, disabled: index === 0,
          onclick: () => send({ op: "move", id: goal.id, index: index - 1 }, { quiet: true }) }),
        h("button", { type: "button", class: "btn ghost", text: l.down, disabled: index === total - 1,
          onclick: () => send({ op: "move", id: goal.id, index: index + 1 }, { quiet: true }) }),
        ...stateButtons));
    const asks = asksFor(goal.items);
    return h("li", { class: `goal-card state-${goal.state}`, "data-goal-id": goal.id },
      h("div", { class: "goal-head" },
        h("span", { class: "goal-num", text: goal.number || (goal.state === "active" ? "🧭" : "") }),
        h("span", { class: "goal-title", text: goal.title }),
        goal.state !== "active" ? h("span", { class: "goal-chip", text: l.state[goal.state] }) : null,
        dueChip(goal), tools),
      goal.done_when ? h("div", { class: "goal-done" }, h("span", { class: "dim", text: `${l.done_when} ` }), goal.done_when) : null,
      ...goal.projects.map(progress).filter(Boolean),
      projectLinks(goal),
      counts(goal.counts, goal.quiet),
      asks.length ? h("div", { class: "goal-asks" }, ...asks) : null,
      deliverables(goal.deliverables),
      goal.items.length ? null : h("div", { class: "dim goal-empty", text: l.none_yet }));
  }

  // The open decision cards of these threads, answered in place like on the secretary home.
  function asksFor(ids) {
    const threads = new Set(ids.map((id) => state.byId.get(id)?.thread_id).filter(Boolean));
    return window.ConsoleSecretary.cardsFor(threads).map((c) => window.ConsoleSecretary.card(c));
  }

  function outsideCard(project) {
    const asks = asksFor(project.items);
    return h("li", { class: "goal-card outside" },
      h("div", { class: "goal-head" },
        h("span", { class: "goal-num", text: "🗂️" }),
        h("a", { class: "goal-title", href: `#note/${encodeURI(`02_Contexts/${project.path}/status.md`)}`, text: project.path })),
      progress(project),
      counts(project.counts, false),
      asks.length ? h("div", { class: "goal-asks" }, ...asks) : null,
      deliverables(project.deliverables));
  }

  function header() {
    const l = L();
    const note = data.note.path;
    return h("li", { class: "goal-header" },
      h("div", { class: "goal-source" }, h("span", { class: "dim", text: `${l.source} ` }),
        h("a", { href: `#note/${encodeURI(note)}`, text: note }),
        h("span", { class: "dim", text: ` · ${l.active(data.active, data.limit)}` })),
      h("div", { class: "goal-bar-buttons" },
        h("button", { type: "button", class: "btn primary goal-add", text: l.add, onclick: () => { editing = "new"; render(); } }),
        h("button", { type: "button", class: "btn ghost", text: showPaused ? l.hide_paused : l.show_paused,
          onclick: () => { showPaused = !showPaused; localStorage.setItem("goalsShowPaused", showPaused ? "1" : "0"); render(); } })),
      editing === "new" ? form(null) : null);
  }

  function setupCard() {
    const l = L();
    if (data.reason === "workspace") return h("li", { class: "goal-setup", text: l.no_workspace });
    const input = h("input", { type: "text", class: "goal-in", placeholder: "02_Contexts/…/goals.md", value: data.path || "" });
    const save = async () => {
      try { await api("/goals/config", { method: "PUT", body: { note: input.value.trim() } }); await load(true); }
      catch (err) { toast(err.message); }
    };
    return h("li", { class: "goal-setup" },
      h("p", { text: data.reason === "note" ? l.missing(data.path) : data.reason === "error" ? data.error : l.setup }),
      input, h("button", { type: "button", class: "btn primary", text: l.setup_save, onclick: save }));
  }

  // -- rows for the list -----------------------------------------------------------------
  // Pinned threads lead their goal; then whose move it is, then the newest activity.
  function ordered(ids, items) {
    const byId = new Map(items.map((i) => [i.id, i]));
    const rank = (i) => (i.pinned_at && i.bucket !== "done" ? 0 : i.bucket === "me" ? 1 : i.bucket === "done" ? 3 : 2);
    return ids.map((id) => byId.get(id)).filter(Boolean)
      .sort((a, b) => rank(a) - rank(b) || (b.last_activity_at || "").localeCompare(a.last_activity_at || ""));
  }

  function rows(items) {
    load();
    const l = L();
    if (!data) return [{ node: h("li", { class: "goal-setup dim", text: l.loading }) }];
    if (!data.available) return [{ node: setupCard() }];
    const out = [{ node: header() }];
    const goals = data.goals.filter((g) => showPaused || g.state === "active");
    goals.forEach((goal) => {
      out.push({ node: card(goal, data.goals.indexOf(goal), data.goals.length) });
      out.push(...ordered(goal.items, items).map((item) => ({ item, depth: 0 })));
    });
    if (data.outside.length) {
      out.push({ group: l.outside, cls: "group goal-group" });
      for (const project of data.outside) {
        out.push({ node: outsideCard(project) });
        out.push(...ordered(project.items, items).map((item) => ({ item, depth: 0 })));
      }
    }
    const loose = ordered(data.unlinked, items);
    if (loose.length) {
      out.push({ group: `${l.unlinked}（${loose.length}）`, cls: "group goal-group" });
      out.push(...loose.map((item) => ({ item, depth: 0 })));
    }
    return out;
  }

  // Under a row: what the thread says it is doing, and the chip that moves it to another goal.
  function note(item) {
    if (!data || !data.available) return null;
    const l = L();
    const goal = data.goals.find((g) => g.items.includes(item.id));
    const why = goal?.why_placed?.[item.id];
    const doing = data.doing?.[item.id];
    const chip = h("button", { type: "button", class: "tag goal-pick",
      title: why ? l.placed[why] : l.pick,
      onclick: (e) => { e.stopPropagation(); goalPicker(item, e.currentTarget); },
      text: `🧭 ${goal ? (goal.number || "") + " " + goal.title : l.pick} ▾` });
    return h("div", { class: "goal-note" }, doing ? h("span", { class: "dim", text: doing }) : null, chip);
  }

  const count = () => (data && data.available ? data.goals.filter((g) => g.state === "active").length : 0);

  return { VIEW, rows, note, onBoard, load, count, strings: L };
})();
