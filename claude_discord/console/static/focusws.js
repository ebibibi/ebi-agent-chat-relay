// Ebi Workspace inside the Focus view: today's goodmorning plan, the next actions the
// Workspace plugin ranks highest (same scoring as goodmorning — the server runs the plugin's
// own scanner), the projects that have stalled, and its waiting-for list. A stalled project
// keeps a place in the ranking so that working down the list un-stalls it. The Markdown stays the source of truth: every
// action here is a revision-checked write to the original line, like the Workspace panel.
// Without a Workspace, or without the plugin, nothing here renders.
// Loaded before focus.js, which places these nodes in its rows.
"use strict";

window.ConsoleFocusWorkspace = (() => {
  const REFRESH_MS = 60_000;
  let plan = null;
  let loadedAt = 0;
  let loading = false;
  let busy = false;
  let planOpen = localStorage.getItem("wsPlanOpen") === "1";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    title: "🗂️ Ebi Workspace から", plan: "📋 今日のプラン（goodmorning）", waiting: "⏳ Ebi Workspace の連絡待ち",
    done: "✅ 完了", redate: "📅 期限", run: "▶ AIに実行", delegate: "▶ AIに任せる",
    delegate_hint: "この行をAIに任せる（該当タスクがあればその行を、無ければ新しい仕事として着手）",
    delegate_note: "今日のプラン（goodmorning）からAIに任せた行:", open: "📂", open_hint: "Ebi Workspace で開く",
    more: (n) => `ほか ${n} 件`, follow_up: "📣 催促", lapsed: (n, d) => `期限を${d}日以上過ぎたものが ${n} 件（goodmorning の「期限の見直し」）`,
    over: (d) => `期限${d}日超過`, today: "今日が期限", soon: (d) => `あと${d}日`, on: (s) => `期限 ${s}`,
    since: (s) => `${s}〜`, nudge: (s) => `催促 ${s}`, total: (n, all) => `上位 ${n} 件 / 全 ${all} 件`,
    no_line: "元の行を特定できないため、ここでは操作できません",
    stale: (d) => `😴 ${d}日停滞`, stalled: (n) => `😴 停滞中のプロジェクト（${n}件）`,
    no_next: "次のアクションが未設定 — 1つ決める", stalled_more: (n) => `ほか ${n} 件を表示`,
  } : {
    title: "🗂️ From Ebi Workspace", plan: "📋 Today's plan (goodmorning)", waiting: "⏳ Waiting for (Ebi Workspace)",
    done: "✅ Done", redate: "📅 Due", run: "▶ Run with AI", delegate: "▶ Hand to AI",
    delegate_hint: "Hand this line to an agent (its task if one matches, otherwise as new work)",
    delegate_note: "Handed to an agent from today's plan (goodmorning):", open: "📂", open_hint: "Open in Ebi Workspace",
    more: (n) => `${n} more`, follow_up: "📣 Follow up", lapsed: (n, d) => `${n} item(s) more than ${d} days overdue (goodmorning's review list)`,
    over: (d) => `${d}d overdue`, today: "due today", soon: (d) => `in ${d}d`, on: (s) => `due ${s}`,
    since: (s) => `since ${s}`, nudge: (s) => `follow up ${s}`, total: (n, all) => `top ${n} of ${all}`,
    no_line: "The original line could not be identified, so it cannot be changed here",
    stale: (d) => `😴 stalled ${d}d`, stalled: (n) => `😴 Stalled projects (${n})`,
    no_next: "No next action yet — decide one", stalled_more: (n) => `Show ${n} more`,
  };

  // Task text is Obsidian Markdown; a row shows it as plain text.
  const plain = (text) => String(text || "")
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, (_, target) => target.split("/").pop().replace(/\.md$/, ""))
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1");

  function ensure() {
    if (loading || Date.now() - loadedAt < REFRESH_MS) return;
    load();
  }
  async function load() {
    loading = true;
    try {
      plan = await api("/workspace/plan");
    } catch {
      plan = null;
    } finally {
      loading = false;
      loadedAt = Date.now();
      if (state.view === "focus") render();
    }
  }

  function dueText(item) {
    if (item.days === null || item.days === undefined) return null;
    const l = L();
    if (item.days < 0) return l.over(-item.days);
    if (item.days === 0) return l.today;
    if (item.days <= 7) return l.soon(item.days);
    return l.on(item.due);
  }

  // "📅2026-10-20" replaces the task's own date, or is added before a 🔁 rule / at the end.
  function withDue(raw, iso) {
    if (/📅\d{4}-\d{2}-\d{2}/.test(raw)) return raw.replace(/📅\d{4}-\d{2}-\d{2}/, `📅${iso}`);
    const repeat = raw.indexOf("🔁");
    return repeat >= 0 ? `${raw.slice(0, repeat).trimEnd()} 📅${iso} ${raw.slice(repeat)}`
      : `${raw.trimEnd()} 📅${iso}`;
  }

  async function act(fn) {
    if (busy) return;
    busy = true;
    render();
    try {
      await fn();
    } catch (err) {
      toast(err.message);
    } finally {
      busy = false;
      await load();
    }
  }
  const complete = (item) => act(async () => {
    await api("/workspace/tasks", { method: "PATCH", body: { path: item.path, line: item.line, revision: item.revision, done: true } });
    toast(T.saved);
  });
  const redate = (item, iso) => act(async () => {
    await api("/workspace/tasks", { method: "PATCH", body: { path: item.path, line: item.line, revision: item.revision, text: withDue(item.raw, iso) } });
    toast(T.saved);
  });
  const run = (item) => act(async () => {
    const result = await api("/workspace/run", { method: "POST", body: { path: item.path, line: item.line, revision: item.revision } });
    await refresh();
    openItem(result.item.id);
  });

  function dateButton(item) {
    const input = h("input", { type: "date", class: "ws-focus-date", "aria-label": L().redate,
      value: item.due || "", hidden: true,
      onchange: (e) => { if (e.target.value) redate(item, e.target.value); } });
    const button = h("button", { type: "button", class: "btn", disabled: busy, text: L().redate, onclick: () => {
      input.hidden = false;
      if (input.showPicker) { try { input.showPicker(); } catch { input.focus(); } } else input.focus();
    } });
    return [button, input];
  }

  function itemNode(item) {
    const l = L();
    const usable = item.line !== null && item.line !== undefined && Boolean(item.revision);
    const over = item.days !== null && item.days !== undefined && item.days < 0;
    const stale = item.stale_days !== null && item.stale_days !== undefined;
    const tags = [
      item.kind === "follow_up" ? l.follow_up : null,
      stale ? l.stale(item.stale_days) : null,
      item.priority,
      item.more ? l.more(item.more) : null,
    ].filter(Boolean).map((t) => h("span", { class: "tag" + (stale && t === l.stale(item.stale_days) ? " ws-stale" : ""), text: t }));
    const due = dueText(item);
    if (due) tags.splice(1, 0, h("span", { class: "tag" + (over ? " ws-due-over" : ""), text: due }));
    const actions = usable ? [
      h("button", { type: "button", class: "btn", disabled: busy, text: l.done, onclick: () => complete(item) }),
      ...dateButton(item),
      h("button", { type: "button", class: "btn", disabled: busy, text: l.run, onclick: () => run(item) }),
    ] : [];
    return h("li", { class: "ws-focus-item" },
      h("div", { class: "ws-focus-text" + (item.text ? "" : " dim"), text: item.text ? plain(item.text) : l.no_next }),
      h("div", { class: "ws-focus-meta" },
        h("span", { class: "dim ws-focus-path", text: item.path }),
        ...tags),
      item.milestone ? h("div", { class: "dim ws-focus-milestone", text: `🎯 ${plain(item.milestone)}` }) : null,
      item.status ? h("div", { class: "dim ws-focus-status", text: `📍 ${plain(item.status)}` }) : null,
      h("div", { class: "ws-focus-actions", title: usable ? "" : l.no_line },
        ...actions,
        h("button", { type: "button", class: "btn ghost", title: l.open_hint, "aria-label": l.open_hint,
          text: l.open, onclick: () => window.ConsoleWorkspace.open(item.path) })));
  }

  // Plan lines come in three shapes, each naming its Workspace entry with a wiki link:
  //   "task（[[02_Contexts/<path>/single-actions.md|label]]）"
  //   "[[02_Contexts/<path>/status.md|label]] — P1・期限超過"         (the project's next action)
  //   "[[02_Contexts/<path>/status.md|label]] · P1 · 期限: … — task"
  const LINK = /\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/;
  const META = /^(P[123]\b|期限|週末の読む順|\(?P[123]|due\b)/;
  const strip = (text) => plain(text).replace(/^\[P[123]\]\s*/, "").replace(/📅\d{4}-\d{2}-\d{2}.*$/, "").trim();
  function planEntry(line) {
    const target = (line.match(LINK) || [])[1];
    if (!target) return null;
    const path = target.replace(/^02_Contexts\//, "").replace(/\/(status|single-actions)\.md$/, "");
    return (window.ConsoleWorkspace?.entries() || []).find((e) => e.path === path) || null;
  }
  // The task the line names, or "" when it names only a project.
  function planTitle(line) {
    const bare = line.replace(/[（(]\s*\[\[[^\]]*\]\][^）)]*[）)]/g, "").replace(LINK, "");
    const parts = bare.split(/\s+—\s+/).map((part) => strip(part.replace(/^[\s·]+/, "")));
    return parts.find((part) => part && !META.test(part)) || "";
  }

  // Prefer running the task the line names, so the agent's work stays bound to its Markdown
  // line; a line with no such task becomes new work, started at once like the capture box.
  const delegate = (line) => act(async () => {
    const entry = planEntry(line);
    const own = planTitle(line);
    const task = own && entry && entry.tasks.find((t) => !t.done && strip(t.text) === own);
    const ranked = !own && entry
      && [...plan.items, ...(plan.stalled || [])].find((i) => i.path === entry.path && i.line != null && i.revision);
    let result;
    if (task || ranked) {
      const body = task ? { path: entry.path, line: task.line, revision: entry.revision }
        : { path: ranked.path, line: ranked.line, revision: ranked.revision };
      result = await api("/workspace/run", { method: "POST", body });
    } else {
      const body = { title: (own || plain(line)).trim().slice(0, 200), note: `${L().delegate_note}\n${line}`, start: true };
      if (entry) body.workspace_path = entry.path;
      result = await api("/items", { method: "POST", body });
      if (result.start_error) toast(result.start_error);
    }
    await refresh();
    openItem(result.item.id);
  });

  // List items in source order. The rendered <li>s come out in the same order, so the n-th
  // button hands over the n-th line exactly as goodmorning wrote it.
  const ITEM = /^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$/;
  function sourceLines(markdown) {
    let fence = false;
    return markdown.split("\n").flatMap((line) => {
      if (/^\s*(```|~~~)/.test(line)) fence = !fence;
      const match = !fence && line.match(ITEM);
      return match ? [match[1]] : [];
    });
  }
  // Shown with wiki links as their labels; the buttons keep the original text.
  const readable = (markdown) => markdown
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, (_, target) => target.split("/").pop().replace(/\.md$/, ""));
  function withDelegates(body, lines) {
    const items = [...body.querySelectorAll("li")];
    // Read differently by the renderer: no buttons rather than a button on the wrong line.
    if (items.length !== lines.length) return;
    items.forEach((li, n) => {
      const button = h("button", { type: "button", class: "btn ws-plan-run", disabled: busy,
        title: L().delegate_hint, text: L().delegate, onclick: () => delegate(lines[n]) });
      li.insertBefore(button, li.querySelector(":scope > ul, :scope > ol"));
    });
  }

  // The goodmorning plan, folded by default so it never pushes the work off screen.
  function planCard() {
    ensure();
    if (!plan || !plan.available || !plan.today_plan) return null;
    const body = h("div", { class: "ws-focus-plan-body" });
    body.append(MD.render(readable(plan.today_plan)));
    withDelegates(body, sourceLines(plan.today_plan));
    return h("li", { class: "ws-focus-plan" }, h("details", { open: planOpen,
      ontoggle: (e) => { planOpen = e.target.open; localStorage.setItem("wsPlanOpen", planOpen ? "1" : "0"); } },
      h("summary", { text: L().plan }), body));
  }

  function tasksCard() {
    if (!plan || !plan.available || !plan.items.length) return null;
    const l = L();
    return h("li", { class: "ws-focus" },
      h("div", { class: "group ws-focus-head" }, h("span", { text: l.title }),
        plan.total > plan.items.length ? h("span", { class: "dim", text: ` · ${l.total(plan.items.length, plan.total)}` }) : null),
      h("ul", { class: "ws-focus-list" }, ...plan.items.map(itemNode)),
      plan.lapsed ? h("div", { class: "dim ws-focus-lapsed", text: l.lapsed(plan.lapsed, plan.lapsed_days) }) : null);
  }

  // Every stalled project, most important first; the first few open, the rest folded.
  const STALLED_SHOWN = 5;
  function stalledCard() {
    if (!plan || !plan.available || !plan.stalled || !plan.stalled.length) return null;
    const l = L();
    const head = plan.stalled.slice(0, STALLED_SHOWN).map(itemNode);
    const rest = plan.stalled.slice(STALLED_SHOWN);
    return h("li", { class: "ws-focus ws-focus-stalled" },
      h("div", { class: "group ws-focus-head", text: l.stalled(plan.stalled_total || plan.stalled.length) }),
      h("ul", { class: "ws-focus-list" }, ...head),
      rest.length ? h("details", { class: "ws-focus-stalled-more" },
        h("summary", { class: "dim", text: l.stalled_more(rest.length) }),
        h("ul", { class: "ws-focus-list" }, ...rest.map(itemNode))) : null);
  }

  // Waiting-for entries whose follow-up date has not come; the due ones are already ranked above.
  function waitingCard() {
    if (!plan || !plan.available) return null;
    const waiting = plan.waiting.filter((w) => w.days === null || w.days > 0);
    if (!waiting.length) return null;
    const l = L();
    return h("li", { class: "ws-focus ws-focus-waiting" },
      h("div", { class: "group ws-focus-head", text: l.waiting }),
      h("ul", { class: "ws-focus-list" }, ...waiting.map((w) => h("li", { class: "ws-focus-item" },
        h("div", { class: "ws-focus-text", text: plain(w.who ? `${w.who} / ${w.what}` : w.what) }),
        h("div", { class: "ws-focus-meta" },
          h("button", { type: "button", class: "linkish dim ws-focus-path", title: l.open_hint,
            onclick: () => window.ConsoleWorkspace.open(w.path), text: w.path }),
          w.since ? h("span", { class: "tag", text: l.since(w.since) }) : null,
          w.due ? h("span", { class: "tag", text: l.nudge(w.due) }) : null)))));
  }

  return { planCard, tasksCard, stalledCard, waitingCard, load };
})();
