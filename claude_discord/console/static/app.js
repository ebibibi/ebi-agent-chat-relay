// Relay Console web client. No build step, no dependencies.
// Every piece of server data reaches the page through textContent, never innerHTML.
"use strict";

const API = "/console/api";
const POLL_MS = 8000;

const I18N = {
  en: {
    me: "Inbox", ai: "Running", todo: "To do", idle: "Idle", tree: "Tree", snoozed: "Snoozed", done: "Done",
    filter: "Filter…", capture: "+ Write down work, press Enter (c)", capture_ai: "+ Write down work, Enter hands it to AI (c)",
    autostart: "AI starts right away", empty: "Nothing here. 🎉",
    slots: (r, m, w) => `Slots ${r}/${m ?? "∞"}` + (w ? ` · ${w} queued` : ""),
    priority: "Priority", due: "Due", snooze: "Snooze", project: "Project", parent: "Under",
    none: "—", done_btn: "Done (e)", reopen: "Reopen", open_chat: "Open in chat", start: "Hand to AI",
    reply_ph: "Reply… (Ctrl+Enter to send)", send: "Send", hour: "1h", tomorrow: "Tomorrow", week: "Next week",
    clear: "Clear", loading: "Loading…", token: "Console token",
    login: "Sign in with the console token (CCDB_CONSOLE_TOKEN).", sent: "Sent — the agent will pick it up", started: "Started", captured: "Added to To do",
    saved: "Saved", offline: "offline", updated: "updated", no_project: "No project", queued: "queued",
    st: { running: "running", waiting: "waiting for you", review: "to review", action: "your task",
          error: "failed", done: "done", someday: "someday", todo: "to do", idle: "idle" },
    help: [["j / k", "next / previous"], ["Enter", "open"], ["Esc", "close"], ["e", "done"],
           ["1–4", "priority P0–P3"], ["s", "snooze until tomorrow"], ["c", "write down work"],
           ["r", "reply"], ["/", "filter"], ["g then i/a/t/w/d", "go to a view"], ["?", "this help"]],
  },
  ja: {
    me: "受信箱", ai: "実行中", todo: "やること", idle: "止まっている", tree: "ツリー", snoozed: "スヌーズ中", done: "完了",
    filter: "絞り込み…", capture: "＋ 思いついた仕事を書いて Enter（c）", capture_ai: "＋ 思いついた仕事を書いて Enter → AIが着手（c）",
    autostart: "AIがすぐ着手", empty: "ここには何もありません 🎉",
    slots: (r, m, w) => `枠 ${r}/${m ?? "∞"}` + (w ? `・待ち ${w}` : ""),
    priority: "優先度", due: "期限", snooze: "スヌーズ", project: "案件", parent: "親",
    none: "なし", done_btn: "完了（e）", reopen: "戻す", open_chat: "チャットで開く", start: "AIに頼む",
    reply_ph: "返信…（Ctrl+Enter で送信）", send: "送信", hour: "1時間", tomorrow: "明日", week: "来週",
    clear: "解除", loading: "読み込み中…", token: "コンソールのトークン",
    login: "コンソールのトークン（CCDB_CONSOLE_TOKEN）でサインインします。", sent: "送りました。エージェントが拾います", started: "スレッドを開始しました", captured: "「やること」に追加しました",
    saved: "保存しました", offline: "接続できません", updated: "更新", no_project: "案件なし", queued: "待ち",
    st: { running: "実行中", waiting: "あなたの返事待ち", review: "確認待ち", action: "あなたの作業",
          error: "失敗", done: "完了", someday: "いつか", todo: "やること", idle: "止まっている" },
    help: [["j / k", "次 / 前"], ["Enter", "開く"], ["Esc", "閉じる"], ["e", "完了"],
           ["1〜4", "優先度 P0〜P3"], ["s", "明日までスヌーズ"], ["c", "仕事を書き留める"],
           ["r", "返信"], ["/", "絞り込み"], ["g → i/a/t/w/d", "画面を移動"], ["?", "このヘルプ"]],
  },
};
// ?lang=ja|en pins the language; otherwise the browser's preference decides.
const LANG = (() => {
  const pinned = new URLSearchParams(location.search).get("lang");
  if (pinned && I18N[pinned]) localStorage.setItem("lang", pinned);
  const chosen = localStorage.getItem("lang");
  if (chosen && I18N[chosen]) return chosen;
  return (navigator.languages || [navigator.language || "en"]).some((l) => l.toLowerCase().startsWith("ja")) ? "ja" : "en";
})();
const T = I18N[LANG];
document.documentElement.lang = LANG;

const STATUS_ICON = { running: "⚙️", waiting: "❓", review: "👀", action: "📋", error: "⚠️",
  done: "✅", someday: "💤", todo: "○", idle: "·" };
const VIEWS = [
  { key: "me", hot: true }, { key: "ai" }, { key: "todo" }, { key: "idle" },
  { sep: true }, { key: "tree" }, { key: "snoozed" }, { key: "done" },
];
const VIEW_KEYS = { i: "me", a: "ai", t: "tree", w: "todo", d: "done" };

const state = {
  items: [], byId: new Map(), slots: {}, view: localStorage.getItem("view") || "me",
  selected: null, open: null, filter: "", collapsed: new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")),
  messages: [], lastSync: null, pendingG: false, drafts: {},
};

// ---------------------------------------------------------------- dom helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    // CSSOM, not a style attribute: the CSP forbids inline style attributes.
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
function toast(msg) {
  const t = h("div", { class: "toast", role: "status", text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

// ---------------------------------------------------------------- api
async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  // Token mode (no Cloudflare Access in front): the token the human entered once.
  const token = localStorage.getItem("token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (method !== "GET") { headers["X-Console-Request"] = "1"; headers["Content-Type"] = "application/json"; }
  const res = await fetch(API + path, { method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401) { showLogin(); throw new Error((data && data.error) || "HTTP 401"); }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

async function refresh() {
  try {
    const data = await api("/board");
    state.items = data.items;
    state.byId = new Map(data.items.map((i) => [i.id, i]));
    state.slots = data.slots || {};
    state.lastSync = new Date();
    $("sync").className = "sync";
    $("sync").textContent = `${T.updated} ${state.lastSync.toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}`;
    if (state.open && !state.byId.has(state.open)) state.open = null;
    render();
  } catch (err) {
    $("sync").className = "sync bad";
    $("sync").textContent = `${T.offline}: ${err.message}`;
  }
}

// ---------------------------------------------------------------- time
function rel(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const fmt = new Intl.RelativeTimeFormat(document.documentElement.lang, { numeric: "auto", style: "narrow" });
  const abs = Math.abs(s);
  if (abs < 3600) return fmt.format(-Math.round(s / 60), "minute");
  if (abs < 86400) return fmt.format(-Math.round(s / 3600), "hour");
  return fmt.format(-Math.round(s / 86400), "day");
}
function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(LANG, { month: "numeric", day: "numeric" }) +
    (d.getHours() || d.getMinutes() ? " " + d.toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "");
}
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function snoozeTarget(kind) {
  const d = new Date();
  if (kind === "hour") return new Date(d.getTime() + 3600e3);
  const t = new Date(d.getFullYear(), d.getMonth(), d.getDate() + (kind === "week" ? 7 - ((d.getDay() + 6) % 7) : 1), 9, 0);
  return t;
}

// ---------------------------------------------------------------- selection of rows
function matches(item) {
  if (!state.filter) return true;
  const q = state.filter.toLowerCase();
  return [item.title, item.project, item.thread_name, item.working_dir].some((v) => v && v.toLowerCase().includes(q));
}
function visibleRows() {
  const items = state.items.filter(matches);
  if (state.view === "tree") return treeRows(items.filter((i) => i.bucket !== "done"));
  return items.filter((i) => i.bucket === state.view).map((item) => ({ item, depth: 0 }));
}
function treeRows(items) {
  const ids = new Set(items.map((i) => i.id));
  const kids = new Map();
  for (const i of items) {
    const p = i.parent_id && ids.has(i.parent_id) ? i.parent_id : null;
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(i);
  }
  const rows = [];
  const walk = (parent, depth) => {
    for (const item of kids.get(parent) || []) {
      const children = kids.get(item.id) || [];
      rows.push({ item, depth, children: children.length });
      if (children.length && !state.collapsed.has(item.id)) walk(item.id, depth + 1);
    }
  };
  // Roots grouped by project, so a tree reads as "case → work → sub-work".
  const roots = kids.get(null) || [];
  const groups = new Map();
  for (const r of roots) {
    const k = r.project || "";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  const keys = [...groups.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
  for (const k of keys) {
    rows.push({ group: k || T.no_project });
    kids.set(`__${k}`, groups.get(k));
    walk(`__${k}`, 0);
  }
  return rows;
}

// ---------------------------------------------------------------- render
function render() {
  syncHash();
  renderNav();
  renderList();
  renderDetail();
}

function renderNav() {
  const counts = {};
  for (const i of state.items) counts[i.bucket] = (counts[i.bucket] || 0) + 1;
  counts.tree = state.items.filter((i) => i.bucket !== "done").length;
  const nav = $("nav");
  nav.replaceChildren(
    h("div", { class: "brand", text: "Relay Console" }),
    ...VIEWS.map((v) => v.sep ? h("div", { class: "sep" }) : h("button", {
      class: state.view === v.key ? "active" : "",
      onclick: () => setView(v.key),
    }, h("span", { text: T[v.key] }), h("span", {
      class: "count" + (v.hot && counts[v.key] ? " hot" : ""), text: counts[v.key] || "",
    }))),
    h("div", { class: "slots", text: T.slots(state.slots.running ?? 0, state.slots.max, state.slots.waiting) }),
  );
  $("view-title").textContent = T[state.view];
  const hot = counts.me || 0;
  document.title = hot ? `(${hot}) Relay Console` : "Relay Console";
}

function renderList() {
  const rows = visibleRows();
  const list = $("list");
  const nodes = [];
  for (const row of rows) {
    if (row.group !== undefined) { nodes.push(h("li", { class: "group", text: row.group })); continue; }
    nodes.push(rowNode(row));
  }
  list.replaceChildren(...nodes);
  const empty = $("empty");
  empty.hidden = rows.length > 0;
  empty.textContent = T.empty;
  const sel = list.querySelector(".row.sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
}

function rowNode({ item, depth, children }) {
  const meta = [];
  if (state.view !== "tree" && item.project) meta.push(h("span", { class: "tag", text: item.project }));
  meta.push(h("span", { class: item.running ? "live" : "", text: T.st[item.status] || item.status }));
  if (item.queued) meta.push(h("span", { text: T.queued }));
  if (item.last_activity_at) meta.push(h("span", { text: rel(item.last_activity_at) }));
  if (item.working_dir) meta.push(h("span", { text: item.working_dir.split("/").slice(-1)[0] }));
  const side = [h("span", { class: `pri p${item.priority}`, text: `P${item.priority}` })];
  if (item.due_at) side.push(h("span", { class: "due" + (item.overdue ? " over" : ""), text: shortDate(item.due_at) }));
  const twisty = children
    ? h("span", { class: "tw", onclick: (e) => { e.stopPropagation(); toggleCollapse(item.id); },
        text: state.collapsed.has(item.id) ? "▸" : "▾" })
    : (depth ? h("span", { class: "tw", text: "" }) : null);
  return h("li", {
    class: "row" + (state.selected === item.id ? " sel" : "") + (item.bucket === "done" ? " done" : ""),
    role: "option", "aria-selected": state.selected === item.id ? "true" : "false",
    onclick: () => { state.selected = item.id; openItem(item.id); },
  },
    h("span", { class: "st", title: T.st[item.status], text: STATUS_ICON[item.status] || "·" }),
    h("div", { class: "main", style: depth ? { paddingLeft: `${depth * 18}px` } : null },
      h("div", { class: "title" }, twisty, item.title || item.thread_name || item.id),
      h("div", { class: "meta" }, ...meta)),
    h("div", { class: "side" }, ...side),
  );
}

function renderDetail() {
  const pane = $("detail");
  const item = state.open && state.byId.get(state.open);
  $("app").classList.toggle("no-detail", !item);
  pane.hidden = !item;
  if (!item) { pane.replaceChildren(); return; }
  // Re-rendering while the human types would eat their input: keep the pane
  // and only refresh what the server owns.
  if (pane.dataset.item === item.id && pane.contains(document.activeElement) &&
      document.activeElement.matches("input, textarea, select")) return;
  pane.dataset.item = item.id;

  const pri = h("select", { onchange: (e) => patch(item.id, { priority: Number(e.target.value) }) },
    [0, 1, 2, 3].map((p) => h("option", { value: p, selected: item.priority === p, text: `P${p}` })));
  const due = h("input", { type: "datetime-local", value: toLocalInput(item.due_at),
    onchange: (e) => patch(item.id, { due_at: e.target.value ? new Date(e.target.value).toISOString() : null }) });
  const project = h("input", { type: "text", value: item.project || "", placeholder: T.project, size: 14,
    onchange: (e) => patch(item.id, { project: e.target.value || null }) });
  const parents = state.items.filter((i) => i.id !== item.id && i.bucket !== "done");
  const parent = h("select", { onchange: (e) => patch(item.id, { parent_id: e.target.value || null }) },
    h("option", { value: "", text: T.none }),
    parents.map((p) => h("option", { value: p.id, selected: item.parent_id === p.id, text: (p.title || p.id).slice(0, 60) })));
  const snooze = ["hour", "tomorrow", "week"].map((k) =>
    h("button", { class: "btn", onclick: () => patch(item.id, { snoozed_until: snoozeTarget(k).toISOString() }), text: T[k] }));
  if (item.snoozed_until) snooze.push(h("button", { class: "btn", onclick: () => patch(item.id, { snoozed_until: null }), text: T.clear }));

  const actions = [];
  if (item.bucket === "done") actions.push(h("button", { class: "btn", onclick: () => act(item.id, "reopen"), text: T.reopen }));
  else actions.push(h("button", { class: "btn good", onclick: () => act(item.id, "done"), text: T.done_btn }));
  if (!item.thread_id) actions.push(h("button", { class: "btn primary", onclick: () => startItem(item.id), text: T.start }));
  if (item.url) actions.push(h("a", { class: "btn", href: item.url, target: "_blank", rel: "noopener", text: T.open_chat }));

  const title = h("input", { value: item.title || "", "aria-label": "title",
    onchange: (e) => patch(item.id, { title: e.target.value || null }) });
  const head = h("div", { class: "d-head" },
    h("div", { class: "d-title" },
      h("button", { class: "icon-btn", "aria-label": "close", onclick: closeItem, text: "←" }),
      h("span", { text: STATUS_ICON[item.status] }), title),
    h("div", { class: "controls" },
      h("label", {}, T.priority, pri), h("label", {}, T.due, due),
      h("label", {}, T.project, project), h("label", {}, T.parent, parent)),
    h("div", { class: "controls" }, h("label", {}, T.snooze), ...snooze),
    h("div", { class: "controls" }, ...actions));

  const msgs = h("div", { class: "msgs", id: "msgs" });
  fillMessages(msgs);
  const nodes = [head, msgs];
  if (item.thread_id) {
    // Drafts survive the 8-second refresh and switching between items.
    const box = h("textarea", { id: "reply", placeholder: T.reply_ph, rows: 2,
      oninput: (e) => { state.drafts[item.id] = e.target.value; },
      onkeydown: (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendReply(item.id, box); } } });
    nodes.push(h("form", { class: "reply", onsubmit: (e) => { e.preventDefault(); sendReply(item.id, box); } },
      box, h("button", { class: "btn primary", type: "submit", text: T.send })));
    box.value = state.drafts[item.id] || "";
  } else if (item.note) {
    msgs.replaceChildren(h("div", { class: "msg" }, h("div", { class: "body", text: item.note })));
  }
  pane.replaceChildren(...nodes);
}

function fillMessages(container) {
  if (state.messagesFor !== state.open) { container.replaceChildren(h("div", { class: "empty", text: T.loading })); return; }
  container.replaceChildren(...state.messages.map((m) => h("div", { class: "msg" + (m.is_bot ? "" : " human") },
    h("div", { class: "who" }, h("span", { text: m.author }), h("span", { text: m.created_at ? shortDate(m.created_at) : "" })),
    h("div", { class: "body", text: m.content + (m.truncated ? " …" : "") }))));
  container.scrollTop = container.scrollHeight;
}

// ---------------------------------------------------------------- actions
function syncHash() {
  const target = "#" + state.view + (state.open ? "/" + state.open : "");
  if (location.hash !== target) history.replaceState(null, "", target);
}
function readHash() {
  const [view, item] = location.hash.slice(1).split("/");
  if (VIEWS.some((v) => v.key === view)) state.view = view;
  if (item) { state.open = item; state.selected = item; }
}
function setView(view) {
  state.view = view;
  localStorage.setItem("view", view);
  $("nav").classList.remove("open");
  const rows = visibleRows().filter((r) => r.item);
  state.selected = rows.length ? rows[0].item.id : null;
  render();
}
function toggleCollapse(id) {
  state.collapsed.has(id) ? state.collapsed.delete(id) : state.collapsed.add(id);
  localStorage.setItem("collapsed", JSON.stringify([...state.collapsed]));
  renderList();
}
async function openItem(id) {
  state.open = id;
  state.selected = id;
  state.messagesFor = null;
  render();
  const item = state.byId.get(id);
  if (!item || !item.thread_id) { state.messagesFor = id; state.messages = []; return; }
  await loadMessages(id);
}
async function loadMessages(id) {
  try {
    const data = await api(`/items/${encodeURIComponent(id)}/messages?limit=50`);
    if (state.open !== id) return;
    state.messages = data.messages;
    state.messagesFor = id;
    const box = $("msgs");
    if (box) fillMessages(box);
  } catch (err) { toast(err.message); }
}
function closeItem() { state.open = null; render(); }
async function patch(id, changes) {
  try { await api(`/items/${encodeURIComponent(id)}`, { method: "PATCH", body: changes }); toast(T.saved); await refresh(); }
  catch (err) { toast(err.message); }
}
async function act(id, action) {
  try { await api(`/items/${encodeURIComponent(id)}/${action}`, { method: "POST", body: {} }); await refresh(); }
  catch (err) { toast(err.message); }
}
async function sendReply(id, box) {
  const text = box.value.trim();
  if (!text) return;
  box.disabled = true;
  try {
    await api(`/items/${encodeURIComponent(id)}/reply`, { method: "POST", body: { text } });
    box.value = "";
    delete state.drafts[id];
    toast(T.sent);
    await refresh();
    setTimeout(() => loadMessages(id), 1500);
  } catch (err) { toast(err.message); }
  finally { box.disabled = false; }
}
async function startItem(id) {
  try {
    const data = await api(`/items/${encodeURIComponent(id)}/start`, { method: "POST", body: {} });
    toast(T.started);
    await refresh();
    openItem(data.item.id);
  } catch (err) { toast(err.message); }
}
async function capture(title) {
  const start = autostart();
  try {
    const data = await api("/items", { method: "POST", body: { title, start } });
    if (data.start_error) toast(data.start_error);
    else if (start) toast(T.started);
    await refresh();
    // A capture lands in To do; leave Inbox and the like so it is not invisible.
    if (state.view !== "todo" && state.view !== "tree") setView("todo");
    state.selected = data.item.id;
    renderList();
    toast(T.captured);
  } catch (err) { toast(err.message); }
}
// Captured work goes straight to an agent unless the user turned that off.
function autostart() { return localStorage.getItem("autostart") !== "0"; }
function syncCapture() {
  $("capture-start").checked = autostart();
  $("capture-input").placeholder = autostart() ? T.capture_ai : T.capture;
}

function move(delta) {
  const rows = visibleRows().filter((r) => r.item);
  if (!rows.length) return;
  let i = rows.findIndex((r) => r.item.id === state.selected);
  i = i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + delta));
  state.selected = rows[i].item.id;
  if (state.open) openItem(state.selected); else renderList();
}
function showLogin() {
  const help = $("help");
  if (!help.hidden && help.dataset.mode === "login") return;
  help.dataset.mode = "login";
  help.hidden = false;
  const input = h("input", { type: "password", autocomplete: "current-password", placeholder: T.token });
  help.replaceChildren(h("form", { class: "card", onclick: (e) => e.stopPropagation(), onsubmit: (e) => {
    e.preventDefault();
    if (!input.value.trim()) return;
    localStorage.setItem("token", input.value.trim());
    help.hidden = true;
    help.dataset.mode = "";
    refresh();
  } }, h("p", { text: T.login }), input, " ", h("button", { class: "btn primary", type: "submit", text: "OK" })));
  input.focus();
}
function showHelp(show) {
  $("help").dataset.mode = "help";
  const help = $("help");
  help.hidden = !show;
  if (show) help.replaceChildren(h("div", { class: "card" }, h("table", {},
    T.help.map(([k, v]) => h("tr", {}, h("td", {}, h("kbd", { text: k })), h("td", { text: v }))))));
}

// ---------------------------------------------------------------- wiring
function wire() {
  $("filter").placeholder = T.filter;
  $("capture-start-label").textContent = T.autostart;
  syncCapture();
  $("capture-start").addEventListener("change", (e) => {
    localStorage.setItem("autostart", e.target.checked ? "1" : "0");
    syncCapture();
  });
  $("filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); });
  $("capture").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("capture-input");
    const title = input.value.trim();
    if (title) { capture(title); input.value = ""; }
  });
  $("nav-toggle").addEventListener("click", () => $("nav").classList.toggle("open"));
  $("help").addEventListener("click", () => { if ($("help").dataset.mode !== "login") showHelp(false); });

  document.addEventListener("keydown", (e) => {
    const typing = e.target.matches("input, textarea, select");
    if (e.key === "Escape") {
      if (typing) { e.target.blur(); return; }
      if (!$("help").hidden) showHelp(false); else closeItem();
      return;
    }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (state.pendingG) {
      state.pendingG = false;
      if (VIEW_KEYS[e.key]) { setView(VIEW_KEYS[e.key]); e.preventDefault(); }
      return;
    }
    const id = state.selected;
    switch (e.key) {
      case "j": case "ArrowDown": move(1); break;
      case "k": case "ArrowUp": move(-1); break;
      case "Enter": if (id) openItem(id); break;
      case "e": if (id) act(id, "done"); break;
      case "1": case "2": case "3": case "4": if (id) patch(id, { priority: Number(e.key) - 1 }); break;
      case "s": if (id) patch(id, { snoozed_until: snoozeTarget("tomorrow").toISOString() }); break;
      case "c": $("capture-input").focus(); break;
      case "r": if ($("reply")) $("reply").focus(); break;
      case "/": $("filter").focus(); break;
      case "g": state.pendingG = true; setTimeout(() => { state.pendingG = false; }, 1200); break;
      case "?": showHelp(true); break;
      default: return;
    }
    e.preventDefault();
  });

  let timer = setInterval(refresh, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    clearInterval(timer);
    if (!document.hidden) { refresh(); timer = setInterval(refresh, POLL_MS); }
  });
}

readHash();
wire();
render();
refresh().then(() => { if (state.open) openItem(state.open); });
