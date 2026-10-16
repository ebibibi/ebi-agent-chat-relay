// The thread's Ebi Workspace entry beside the conversation: where the project stands, what is
// next (check off in place), what waits on others, the latest log, and the other threads bound
// to it. Read from the Markdown through /workspace/context, so an agent's edit to status.md
// shows up here within half a minute. Folded by default; the choice is remembered per browser.
// Also folds the "[Ebi Workspace context]" block the Console adds to a prompt, so the
// conversation shows that context was sent, and which, without the wall of instructions.
// Loaded after workspace.js and before app.js.
"use strict";

window.ConsoleWsPane = (() => {
  const REFRESH_MS = 30_000;
  const MARK = "\n\n[Ebi Workspace context]\n";
  const cache = new Map(); // path -> {at, data}
  let shownKey = "";
  let busy = false;
  let force = false;
  let open = localStorage.getItem("wsPaneOpen") === "1";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    current: "📍 現在地", next: "⏭️ 次のアクション", waiting: "⏳ 連絡待ち", log: "📝 最近のログ",
    threads: "💬 同じプロジェクトの会話", open: "📂 Ebi Workspace で開く", none: "なし", sent: "🗂️ Ebi Workspace のコンテキストを渡しました",
  } : {
    current: "📍 Where it stands", next: "⏭️ Next actions", waiting: "⏳ Waiting for", log: "📝 Latest log",
    threads: "💬 Other threads here", open: "📂 Open in Ebi Workspace", none: "None", sent: "🗂️ Ebi Workspace context was sent",
  };

  async function fetchContext(path, force = false) {
    const hit = cache.get(path);
    if (!force && hit && Date.now() - hit.at < REFRESH_MS) return hit.data;
    try {
      const raw = await api(`/workspace/context?path=${encodeURIComponent(path)}`);
      const data = raw && Array.isArray(raw.tasks) ? { current: "", waiting: [], log: "", ...raw } : null;
      cache.set(path, { at: Date.now(), data });
      return data;
    } catch {
      cache.set(path, { at: Date.now(), data: null });
      return null;
    }
  }

  async function check(data, task, done) {
    if (busy) return;
    busy = true;
    try {
      await api("/workspace/tasks", { method: "PATCH", body: { path: data.path, line: task.line, revision: data.revision, done } });
      toast(T.saved);
    } catch (err) {
      toast(err.message);
    } finally {
      busy = false;
      await fetchContext(data.path, true);
      force = true;
      renderDetail();
    }
  }

  function block(title, ...children) {
    const body = children.flat().filter(Boolean);
    return body.length ? h("div", { class: "ws-pane-block" }, h("div", { class: "ws-pane-head", text: title }), ...body) : null;
  }

  function body(item, data) {
    const l = L();
    const open = data.tasks.filter((t) => !t.done);
    const others = state.items.filter((i) => i.id !== item.id && i.workspace_path === data.path && i.bucket !== "done");
    const md = (text) => h("div", { class: "md" }, MD.render(text));
    return [
      data.current ? block(l.current, md(data.current)) : null,
      block(l.next, open.length ? open.map((task) => h("label", { class: "ws-pane-task" },
        h("input", { type: "checkbox", disabled: busy, onchange: (e) => check(data, task, e.target.checked) }),
        h("span", { text: task.text }))) : h("div", { class: "dim", text: l.none })),
      data.waiting.length ? block(l.waiting, h("ul", {}, ...data.waiting.map((w) => h("li", { text: w })))) : null,
      data.log ? h("details", { class: "ws-pane-block ws-pane-log" }, h("summary", { class: "ws-pane-head", text: l.log }), md(data.log)) : null,
      others.length ? block(l.threads, others.slice(0, 8).map((i) => h("button", { type: "button", class: "linkish ws-pane-thread",
        onclick: () => openItem(i.id), text: i.title || i.thread_name || i.id }))) : null,
      h("button", { type: "button", class: "btn ghost", text: l.open, onclick: () => window.ConsoleWorkspace.open(data.path) }),
    ];
  }

  // Called from renderDetail on every redraw; rebuilds only when something it shows changed.
  function render(item) {
    const pane = document.getElementById("d-ws");
    if (!pane) return;
    const path = window.ConsoleWorkspace?.available() ? item.workspace_path : null;
    if (!path) { pane.hidden = true; pane.replaceChildren(); shownKey = ""; return; }
    const hit = cache.get(path);
    const data = hit && hit.data;
    if (!hit || Date.now() - hit.at >= REFRESH_MS) {
      fetchContext(path).then(() => { if (state.open === item.id) renderDetail(); });
    }
    const key = `${item.id}|${path}|${data ? data.revision + data.log.length : "-"}|${LANG}|${others(item, path)}`;
    if (key === shownKey && !force) return;
    if (pane.contains(document.activeElement) && !force) return;
    force = false;
    shownKey = key;
    pane.hidden = !data;
    if (!data) { pane.replaceChildren(); return; }
    const first = (data.current || "").split("\n").find((x) => x.trim()) || "";
    const plainFirst = first.replace(/[*_`>#[\]]/g, "").trim();
    pane.replaceChildren(h("details", { open,
      ontoggle: (e) => { open = e.target.open; localStorage.setItem("wsPaneOpen", open ? "1" : "0"); } },
      h("summary", { class: "ws-pane-summary" }, h("span", { class: "ws-pane-path", text: `🗂️ ${path}` }),
        plainFirst ? h("span", { class: "dim ws-pane-first", text: plainFirst }) : null),
      h("div", { class: "ws-pane-body" }, ...body(item, data).filter(Boolean))));
  }
  const others = (item, path) => state.items.filter((i) => i.id !== item.id && i.workspace_path === path && i.bucket !== "done").length;

  // A human message the Console extended with Workspace context: the text, then a folded note.
  function splitMessage(content) {
    const at = (content || "").indexOf(MARK);
    if (at < 0) return null;
    const context = content.slice(at + MARK.length);
    const path = (context.match(/This work belongs to (.+?)\. /) || [])[1] || "";
    return { text: content.slice(0, at), path, context };
  }
  function sentNote(parts) {
    return h("details", { class: "ws-sent" },
      h("summary", { class: "dim", text: `${L().sent}${parts.path ? `: ${parts.path}` : ""}` }),
      h("pre", { text: parts.context }));
  }

  return { render, splitMessage, sentNote };
})();
