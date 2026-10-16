// Which Ebi Workspace context/project a thread belongs to, and how that was decided.
// The Console works the binding out in the background; a person only corrects it.
//   👤 pinned    a person chose it (or chose "no Workspace")
//   ✍️ recorded  the session wrote that project's records
//   🤖 inferred  guessed from the conversation
//   🧭 heuristic hinted by the working directory, repository, project name or parent
//   ❔ unresolved nothing decided yet
// Picking an entry pins it; "Confirm" pins the current guess; "Automatic" hands it back.
// Loaded after workspace.js (entries) and before app.js (rows and the detail drawer).
"use strict";

window.ConsoleWsBind = (() => {
  const ICON = { pinned: "👤", recorded: "✍️", inferred: "🤖", heuristic: "🧭" };
  const RECENT_KEY = "wsRecent";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    source: { pinned: "あなたが選択", recorded: "このスレッドが記録したプロジェクト", inferred: "会話の内容からAIが推定", heuristic: "手掛かりから推定" },
    reason: { cwd: "作業フォルダが一致", repository: "overview.md のリポジトリが一致", project: "プロジェクト名が一致", parent: "親スレッドと同じ", task: "Workspace のタスクから開始", inferred: "会話の内容" },
    unresolved: "未判定", none: "Workspace なし", confirm: "✓ 確定", change: "変更", auto: "🔄 自動に戻す",
    search: "コンテキスト・プロジェクトを検索", recent: "最近", all: "すべて", nothing: "見つかりません",
    pick_hint: "選ぶとこのスレッドに固定されます", record: (f) => `${f} を更新`,
  } : {
    source: { pinned: "You chose it", recorded: "This thread recorded work there", inferred: "AI guessed from the conversation", heuristic: "Guessed from hints" },
    reason: { cwd: "working folder matches", repository: "repository in overview.md matches", project: "project name matches", parent: "same as the parent thread", task: "started from a Workspace task", inferred: "conversation content" },
    unresolved: "Unresolved", none: "No Workspace", confirm: "✓ Confirm", change: "Change", auto: "🔄 Automatic",
    search: "Search contexts and projects", recent: "Recent", all: "All", nothing: "Nothing found",
    pick_hint: "Picking one fixes it for this thread", record: (f) => `updated ${f}`,
  };
  const available = () => window.ConsoleWorkspace?.available();
  const entries = () => window.ConsoleWorkspace?.entries() || [];

  function why(item) {
    const l = L();
    const reason = item.workspace_reason || "";
    const text = reason.startsWith("record:") ? l.record(reason.slice(7)) : l.reason[reason] || reason;
    return [l.source[item.workspace_source], text].filter(Boolean).join(" — ");
  }

  // The tag: list rows show only decided or guessed bindings; the drawer also shows ❔.
  function badge(item, { drawer = false } = {}) {
    if (!available()) return item.workspace_path ? h("span", { class: "tag", text: `🗂️ ${item.workspace_path}` }) : null;
    const source = item.workspace_source;
    if (item.workspace_path) {
      return h("span", { class: `tag ws-bind ws-${source || "heuristic"}`, title: why(item),
        text: `${ICON[source] || "🧭"} 📂 ${item.workspace_path}` });
    }
    if (source === "pinned") return drawer ? h("span", { class: "tag ws-bind ws-none", title: why(item), text: `👤 ${L().none}` }) : null;
    return drawer ? h("span", { class: "tag ws-bind ws-unresolved", text: `❔ ${L().unresolved}` }) : null;
  }

  const recent = () => {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter((p) => typeof p === "string"); }
    catch { return []; }
  };
  const remember = (path) => localStorage.setItem(RECENT_KEY,
    JSON.stringify([path, ...recent().filter((p) => p !== path)].slice(0, 6)));

  // Every space-separated term must appear, in order of characters, somewhere in the path.
  function matches(path, query) {
    const hay = path.toLowerCase();
    return query.toLowerCase().split(/\s+/).filter(Boolean).every((term) => {
      let at = 0;
      for (const ch of term) { at = hay.indexOf(ch, at); if (at < 0) return false; at += 1; }
      return true;
    });
  }

  const set = (item, changes) => patch(item.id, changes);
  const pin = (item, path) => { remember(path); return set(item, { workspace_path: path }); };

  function picker(item, anchor) {
    document.querySelector(".ws-bind-pop")?.remove();
    const l = L();
    const input = h("input", { type: "search", class: "ws-bind-search", placeholder: l.search, "aria-label": l.search });
    const list = h("div", { class: "ws-bind-list", role: "listbox" });
    const close = () => { pop.remove(); document.removeEventListener("mousedown", outside, true); };
    const choose = async (fn) => { close(); await fn(); };
    const option = (label, onclick, on) => h("button", { type: "button", role: "option",
      class: "ws-bind-option" + (on ? " on" : ""), "aria-selected": on ? "true" : "false", onclick, text: label });
    function draw() {
      const q = input.value.trim();
      const all = entries();
      const known = new Set(all.map((e) => e.path));
      const icon = (e) => e.kind === "project" ? "📁" : "🗂️";
      const row = (e) => option(`${icon(e)} ${e.path}`, () => choose(() => pin(item, e.path)), e.path === item.workspace_path);
      const found = all.filter((e) => !q || matches(e.path, q));
      const recents = q ? [] : recent().filter((p) => known.has(p)).map((p) => all.find((e) => e.path === p));
      list.replaceChildren(
        ...(q ? [] : [
          option(l.auto, () => choose(() => set(item, { workspace_path: null })), !item.workspace_source),
          option(`👤 ${l.none}`, () => choose(() => set(item, { workspace_path: null, workspace_source: "pinned" })),
            item.workspace_source === "pinned" && !item.workspace_path),
        ]),
        recents.length ? h("div", { class: "ws-bind-head", text: l.recent }) : null,
        ...recents.map(row),
        q ? null : h("div", { class: "ws-bind-head", text: l.all }),
        ...found.map(row),
        found.length ? null : h("div", { class: "dim ws-bind-empty", text: l.nothing }));
    }
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus(); }
      if (e.key === "Enter") { e.preventDefault(); list.querySelector(".ws-bind-option:not(.on)")?.click(); }
    });
    const pop = h("div", { class: "ws-bind-pop", role: "dialog", "aria-label": l.search },
      input, h("div", { class: "dim ws-bind-hint", text: l.pick_hint }), list);
    const outside = (e) => { if (!pop.contains(e.target) && e.target !== anchor) close(); };
    document.addEventListener("mousedown", outside, true);
    anchor.after(pop);
    draw();
    input.focus();
  }

  // In the detail drawer: the badge, why, and the two ways to correct it.
  function control(item) {
    if (!available()) return null;
    const l = L();
    const guessed = item.workspace_path && item.workspace_source !== "pinned";
    const change = h("button", { type: "button", class: "btn ghost ws-bind-change", text: l.change,
      onclick: (e) => picker(item, e.currentTarget) });
    return h("div", { class: "controls ws-bind-control" },
      badge(item, { drawer: true }),
      item.workspace_source && item.workspace_source !== "pinned" ? h("span", { class: "dim", text: why(item) }) : null,
      guessed ? h("button", { type: "button", class: "btn", text: l.confirm,
        onclick: () => pin(item, item.workspace_path) }) : null,
      change);
  }

  return { badge, control, matches };
})();
