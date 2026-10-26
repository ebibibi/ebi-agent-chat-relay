// Ebi AI Console web client. No build step, no dependencies.
// Every piece of server data reaches the page through textContent, never innerHTML
// (message Markdown included: markdown.js builds elements, it does not parse HTML).
"use strict";

const API = "/console/api";
const POLL_MS = 8000;
const POLL_HIDDEN_MS = 30000;
const STALE_RENDER_MS = 60000;
const HISTORY = 100;

const I18N = {
  en: {
    focus: "🎯 Focus", effort: { quick: "Quick", short: "Short", deep: "Deep" }, effort_all: "All", effort_bar: "Your time:", effort_min: (n) => `${n} min`, effort_hint: "AI estimate of your part before the agent can carry on: ⚡ up to 3 min, ⏱ up to 20 min, 🧘 longer", focus_first: "Do this first", focus_next: "Then", focus_waiting: "⏳ Waiting", focus_elsewhere: "📍 Somewhere else", place_new: "New place", add: "Add", delete: "Delete", place_manage: "Manage places", place_hint: "Where this can only be done", place_count: (n) => `${n} open`, place_unlisted: "in use, not listed", place_unlisted_hint: "Used on items (often guessed by AI) but not on your list", place_manage_hint: "Renaming or deleting a place changes it on every item. A deleted place leaves its items doable anywhere.", place_none: "No places yet.", place_delete_confirm: (n, c) => `Remove "${n}" from ${c} open item(s)?`, here: "I am at:", here_hint: "Pick every place you are at; work bound to any of them counts as here", anywhere: "Anywhere", place: "Place", place_ph: "anywhere", no_due: "No due date", bp_backend: "Backend", bp_model: "Model", bp_effort: "Effort",
    bp_model_other: "other model, Enter", bp_default: "Default", bp_title_thread: "Backend for this conversation",
    bp_title_default: "Default for new conversations", bp_note_thread: "Applies from the next turn of this conversation.",
    bp_note_default: "New conversations start with this; a conversation's own choice wins.", pause: "Pause", stop: "Stop", resume: "Resume", first: "First", later: "Later",
    paused: "paused", pause_hint: "Stop this turn now and mark the thread paused; Resume carries on",
    stop_hint: "Stop this turn now", resume_hint: "Ask the agent to carry on from where it was paused",
    first_hint: "Start this one before the others waiting", later_hint: "Let the others waiting go first",
    paused_toast: "Paused", stopping: "Stopping…", resumed_toast: "Resumed",
    slots_hint: "Threads running now / the most that may run at once", slots_up: "Allow one more at once",
    slots_down: "Allow one fewer at once", slots_set: (n) => `Up to ${n} at once`, pinned_group: "📌 Working on now", status_filter: "Status:", status_all: "All", pin: "Pin", unpin: "Pinned",
    pin_hint: "Pin as what you are working on now; pinned threads lead every list (p)", capture_pin: "📌 Pin", capture_pin_hint: "Pin this request as what you are working on now (this one only)", hide_done_off: "✅ Showing done", hide_done_on: "✅ Done hidden",
    hide_done_hint: "Hide or show finished items in every list (h)", hide_done_in_done: "The Done list always shows finished items", context_hint: (d) => `Context window used by the last turn: ${d}`, due_over: "overdue", due_in: (x) => `in ${x}`, u_min: "m", u_hour: "h", u_day: "d", focus_ready: "can do now", ready_now: "Can do now", ready_wait: "Waiting", ready_hint: "Whether you can act on this now (click to switch). Dashed: the AI's guess", ready_yours: "Your call. New activity in the conversation hands it back to the AI", ready_back_ai: "Back to the AI's call", focus_from: (d) => `from ${d}`, ai_guess: "AI guess", ai_set: "set by AI", series: (n) => `×${n}`, series_hint: (n) => `${n} items share this title; Focus shows only the newest`,
    guide_title: "🧭 Your next move", guide_unblocks: "▶ Once you answer, the agent carries on", guide_open: "Open",
    guide_reasons: { overdue: "overdue", due_soon: "due within 24h", priority: "high priority", asked: "the agent asked you", review: "a deliverable to review", action: "a step only you can do", error: "the last turn failed" },
    guide_calm: "🌿 Nothing needs you right now.", guide_calm_more: "You can stop here — what remains is moving on its own.",
    guide_counts: (c) => [c.running && `${c.running} running`, c.waiting && `${c.waiting} waiting`, c.candidate && `${c.candidate} to confirm done`].filter(Boolean).join(" · "),
    guide_elsewhere: (n) => `Nothing to do here; ${n} item(s) need another place.`,
    guide_batches: "🧺 Decide together", guide_batch: (t, n) => `${t} — ${n} decisions`,
    guide_drift: "🔍 Records the Console could not write back", guide_task_open: (t) => `finished, but the Workspace task "${t}" is still open`, guide_record_stale: "finished after its project records were last updated",
    me: "Inbox", candidate: "Done?", ai: "Running", todo: "To do", idle: "Idle", recent: "Recent", tree: "Tree", snoozed: "Snoozed", done: "Done",
    filter: "Filter…", capture: "+ Write down work, Ctrl+Enter to add (c) — lines after the first become a note", capture_ai: "+ Write down work, Ctrl+Enter hands it to AI (c) — lines after the first become a note",
    autostart: "AI starts right away", empty: "Nothing here. 🎉",
    activity: (n) => `⚙ Activity (${n})`, details: "Details",
    activity_hint: "Show or hide tool calls, status lines and automatic prompts",
    notify_on: "🔔 Notifications on", notify_off: "🔕 Notifications off",
    jump: "↓ Latest", jump_new: (n) => `↓ ${n} new`, no_messages: "No messages yet.",
    slots: (r, m, w) => `Running ${r} / limit ${m ?? "∞"}` + (w ? ` · ${w} queued` : ""),
    priority: "Priority", due: "Due", snooze: "Snooze", project: "Project", parent: "Under",
    none: "—", reopen: "Reopen", finish: "Done", finish_confirm: "Click again to finish",
    finish_hint: "Mark this thread done (click twice; shortcut e). Undo right after, or Reopen any time.",
    reopen_hint: "Bring this thread back from Done", finished: "Done", undo: "Undo", dismiss: "Dismiss notification", reopened: "Reopened", open_chat: "Open in chat", start: "Hand to AI",
    reply_ph: "Reply… (Ctrl+Enter to send; paste, drop or 📎 to attach files)",
    attach_failed: "Could not attach the file", attach_hint: "Attach files",
    attach_big: (n, max) => `${n} is larger than ${max}`, attach_many: (n) => `At most ${n} files per reply`,
    paste_wait: "A file is still uploading",
    slash_hint: "Type / for commands", compacting: "Compacting the conversation… (up to a few minutes)", slash_desc: { backend: "Switch the AI backend for this conversation",
      model: "Choose the model for the current backend", effort: "Set the reasoning effort",
      compact: "Summarize the conversation to free up context", context: "Show context window usage", cost: "Show token usage and cost" }, send: "Send", hour: "1h", tomorrow: "Tomorrow", week: "Next week",
    pressed: (l) => `Pressed: ${l}`,
    clear: "Clear", loading: "Loading…", capturing: "Adding task…", sent: "Sent — the agent will pick it up", sent_live: "Sent into the running turn — the agent reads it while it works", started: "Started", captured: "Added to To do",
    today: "Today", yesterday: "Yesterday",
    saved: "Saved", offline: "offline", updated: "updated", no_project: "No project", queued: "queued",
    st: { running: "running", waiting: "waiting for you", review: "to review", action: "your task",
          error: "failed", candidate: "done? (AI thinks so)", done: "done", someday: "someday", todo: "to do", idle: "idle" },
    help: [["j / k", "next / previous"], ["Enter", "open"], ["Esc", "close"], ["e", "done"],
           ["1–4", "priority P0–P3"], ["s", "snooze until tomorrow"], ["c", "write down work"],
           ["r", "reply"], ["/", "filter"], ["h", "hide / show done"], ["p", "pin / unpin (working on now)"], ["z", "focus mode: only this thread (Esc leaves)"], ["n", "work-through queue: skip to the next"], ["g then f/i/c/a/u/t/w/d", "go to a view"], ["?", "this help"]],
  },
  ja: {
    focus: "🎯 今やること", effort: { quick: "すぐ", short: "少し", deep: "じっくり" }, effort_all: "すべて", effort_bar: "かかる時間：", effort_min: (n) => `${n}分`, effort_hint: "AIにターンを返すまでにあなたが使う時間の見積もり：⚡ 3分以内、⏱ 20分以内、🧘 それ以上", focus_first: "まずこれ", focus_next: "次に", focus_waiting: "⏳ 待ち", focus_elsewhere: "📍 別の場所でやること", place_new: "新しい場所", add: "追加", delete: "削除", place_manage: "場所の管理", place_hint: "ここでしかできない場所・端末", place_count: (n) => `未完了 ${n}件`, place_unlisted: "未登録", place_unlisted_hint: "スレッドで使われているが一覧にない場所（多くはAIの推定）", place_manage_hint: "名前の変更・削除はすべてのスレッドに反映されます。削除した場所のスレッドは「どこでも」になります。", place_none: "まだ場所はありません。", place_delete_confirm: (n, c) => `「${n}」を未完了の ${c} 件から外しますか？`, here: "いまいる場所：", here_hint: "いまいる場所を全部選べます。どれかに当てはまるものを「ここでできる」とみなします", anywhere: "どこでも", place: "場所", place_ph: "どこでも", no_due: "期限なし", bp_backend: "バックエンド", bp_model: "モデル", bp_effort: "思考の深さ",
    bp_model_other: "ほかのモデル名（Enter）", bp_default: "既定", bp_title_thread: "この会話のバックエンド",
    bp_title_default: "新しい会話の既定", bp_note_thread: "この会話の次のターンから切り替わります。",
    bp_note_default: "新しく始める会話はこの設定で動きます。会話ごとに選んだものが優先されます。", pause: "一時停止", stop: "停止", resume: "再開", first: "先に", later: "後回し",
    paused: "一時停止中", pause_hint: "いまのターンを止めて一時停止の印を付けます。「再開」で続きから",
    stop_hint: "いまのターンを止めます", resume_hint: "止めたところから続けるようエージェントに頼みます",
    first_hint: "待っているほかのスレッドより先に始めます", later_hint: "待っているほかのスレッドを先にします",
    paused_toast: "一時停止しました", stopping: "停止中…", resumed_toast: "再開しました",
    slots_hint: "いま実行中の数 / 同時に動かせる上限", slots_up: "同時に動かせる数を1つ増やす",
    slots_down: "同時に動かせる数を1つ減らす", slots_set: (n) => `同時実行の上限を ${n} にしました`, pinned_group: "📌 いまやってる", status_filter: "ステータス：", status_all: "すべて", pin: "ピン留め", unpin: "ピン中",
    pin_hint: "いまやってる目印。ピン留めしたスレッドはどの一覧でも一番上に出ます（p）", capture_pin: "📌 ピン留め", capture_pin_hint: "この依頼を「いまやってる」としてピン留めします（この1件だけ）", hide_done_off: "✅ 完了も表示", hide_done_on: "✅ 完了を隠す中",
    hide_done_hint: "完了したタスクを全部の一覧で隠す／表示する（h）", hide_done_in_done: "「完了」の一覧では常に表示します", context_hint: (d) => `直近のターンで使ったコンテキスト：${d}`, due_over: "超過", due_in: (x) => `あと${x}`, u_min: "分", u_hour: "時間", u_day: "日", focus_ready: "今すぐできる", ready_now: "今できる", ready_wait: "待ち", ready_hint: "いま手を付けられるか（クリックで切り替え）。点線はAIの見立て", ready_yours: "あなたの判断。会話に新しい動きがあればAIの見立てに戻ります", ready_back_ai: "AIの見立てに戻す", focus_from: (d) => `${d}〜`, ai_guess: "AIの見立て", ai_set: "AIが推定", series: (n) => `×${n}`, series_hint: (n) => `同じ名前のスレッド${n}件を、いちばん新しいものにまとめています`,
    guide_title: "🧭 今やる一手", guide_unblocks: "▶ 返すとAIが続きを進めます", guide_open: "開く",
    guide_reasons: { overdue: "期限超過", due_soon: "24時間以内に期限", priority: "優先度が高い", asked: "AIが質問中", review: "成果物の確認待ち", action: "本人しかできない作業", error: "直前のターンが失敗" },
    guide_calm: "🌿 今はあなたの操作は要りません。", guide_calm_more: "残りは自動で進んでいます。今日は終えて大丈夫。",
    guide_counts: (c) => [c.running && `AI作業中 ${c.running}件`, c.waiting && `待ち ${c.waiting}件`, c.candidate && `完了確認 ${c.candidate}件`].filter(Boolean).join("・"),
    guide_elsewhere: (n) => `ここでできることはありません。別の場所で ${n} 件。`,
    guide_batches: "🧺 まとめて判断", guide_batch: (t, n) => `${t} — ${n}件の判断`,
    guide_drift: "🔍 記録を自動で直せなかった会話", guide_task_open: (t) => `会話では完了、Workspaceのタスク「${t}」は未完了`, guide_record_stale: "会話のあとにプロジェクトの記録が更新されていません",
    me: "受信箱", candidate: "完了候補", ai: "実行中", todo: "やること", idle: "止まっている", recent: "更新順", tree: "ツリー", snoozed: "スヌーズ中", done: "完了",
    filter: "絞り込み…", capture: "＋ 思いついた仕事を書いて Ctrl+Enter（c）。2行目以降はメモ", capture_ai: "＋ 思いついた仕事を書いて Ctrl+Enter → AIが着手（c）。2行目以降はメモ",
    autostart: "AIがすぐ着手", empty: "ここには何もありません 🎉",
    activity: (n) => `⚙ 内部の動き（${n}）`, details: "詳細",
    activity_hint: "ツール実行・状態表示・自動プロンプトの表示／非表示",
    notify_on: "🔔 通知オン", notify_off: "🔕 通知オフ",
    jump: "↓ 最新へ", jump_new: (n) => `↓ 新着 ${n}件`, no_messages: "まだメッセージはありません",
    slots: (r, m, w) => `実行中 ${r} / 上限 ${m ?? "∞"}` + (w ? `・待ち ${w}` : ""),
    priority: "優先度", due: "期限", snooze: "スヌーズ", project: "案件", parent: "親",
    none: "なし", reopen: "戻す", finish: "完了", finish_confirm: "もう一度押すと完了",
    finish_hint: "このスレッドを完了にする（2回押す。ショートカット e）。直後なら「元に戻す」、あとからでも「戻す」で復活できます",
    reopen_hint: "完了から戻す", finished: "完了にしました", undo: "元に戻す", dismiss: "通知を閉じる", reopened: "戻しました", open_chat: "チャットで開く", start: "AIに頼む",
    reply_ph: "返信…（Ctrl+Enter で送信。ファイルは貼り付け・ドロップ・📎で添付）",
    attach_failed: "ファイルを添付できませんでした", attach_hint: "ファイルを添付",
    attach_big: (n, max) => `${n} は ${max} を超えています`, attach_many: (n) => `添付は1回 ${n} 件までです`,
    paste_wait: "ファイルをアップロード中です",
    slash_hint: "/ でコマンド", compacting: "会話を圧縮しています…（数分かかることがあります）", slash_desc: { backend: "この会話のAIバックエンドを切り替える",
      model: "今のバックエンドのモデルを選ぶ", effort: "推論の強さ（effort）を設定する",
      compact: "ここまでの会話を要約してコンテキストを空ける", context: "コンテキストの使用量を見る", cost: "トークン使用量と料金を見る" }, send: "送信", hour: "1時間", tomorrow: "明日", week: "来週",
    pressed: (l) => `押しました: ${l}`,
    clear: "解除", loading: "読み込み中…", capturing: "タスクを登録中…", sent: "送りました。エージェントが拾います", sent_live: "実行中のエージェントに届けました。作業を続けながら読みます", started: "スレッドを開始しました", captured: "「やること」に追加しました",
    today: "今日", yesterday: "昨日",
    saved: "保存しました", offline: "接続できません", updated: "更新", no_project: "案件なし", queued: "待ち",
    st: { running: "実行中", waiting: "あなたの返事待ち", review: "確認待ち", action: "あなたの作業",
          error: "失敗", candidate: "完了候補（AI判断）", done: "完了", someday: "いつか", todo: "やること", idle: "止まっている" },
    help: [["j / k", "次 / 前"], ["Enter", "開く"], ["Esc", "閉じる"], ["e", "完了"],
           ["1〜4", "優先度 P0〜P3"], ["s", "明日までスヌーズ"], ["c", "仕事を書き留める"],
           ["r", "返信"], ["/", "絞り込み"], ["h", "完了を隠す／表示"], ["p", "ピン留め／解除（いまやってる）"], ["z", "集中モード：このスレッドだけ表示（Escで戻る）"], ["n", "連続処理：スキップして次へ"], ["g → f/i/c/a/u/t/w/d", "画面を移動"], ["?", "このヘルプ"]],
  },
};
// ?lang=ja|en pins the language; otherwise the browser's preference decides.
let LANG = (() => {
  const pinned = new URLSearchParams(location.search).get("lang");
  if (["ja", "en"].includes(pinned)) localStorage.setItem("lang", pinned);
  const chosen = localStorage.getItem("lang");
  if (["ja", "en"].includes(chosen)) return chosen;
  return (navigator.languages || [navigator.language || "en"]).some((l) => l.toLowerCase().startsWith("ja")) ? "ja" : "en";
})();
let T = I18N[LANG];
document.documentElement.lang = LANG;

const STATUS_ICON = { running: "⚙️", waiting: "❓", review: "👀", action: "📋", error: "⚠️",
  candidate: "🏁", done: "✅", someday: "💤", todo: "○", idle: "·" };
const VIEWS = [
  { key: "focus", hot: true }, { key: "me", hot: true }, { key: "candidate" }, { key: "ai" }, { key: "todo" }, { key: "idle" },
  { sep: true }, { key: "recent" }, { key: "tree" }, { key: "snoozed" }, { key: "done" },
];
const VIEW_KEYS = { f: "focus", i: "me", c: "candidate", a: "ai", u: "recent", t: "tree", w: "todo", d: "done" };

const state = {
  items: [], byId: new Map(), slots: {}, view: localStorage.getItem("view") || "focus",
  selected: null, open: null, filter: "", collapsed: new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")),
  messages: [], lastSync: null, boardSig: "", renderedAt: 0, pendingG: false, drafts: {},
  // The relay's own tool calls, status lines and automatic prompts stay hidden unless asked for.
  showActivity: localStorage.getItem("showActivity") === "1",
  // Finished items left out of every list but Done. Remembered per browser.
  hideDone: localStorage.getItem("hideDone") === "1",
  // The triage fields (priority, due, project, …) stay folded away unless asked for.
  drawerOpen: localStorage.getItem("drawerOpen") === "1",
  unseen: 0,
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
  showToast(t);
  setTimeout(() => t.remove(), 2600);
}
// Every toast carries its own ✕: one can sit over a button the human wants right now,
// and waiting out its timer is not an option.
function showToast(t) {
  t.append(h("button", { class: "toast-x", type: "button", text: "✕", title: T.dismiss, "aria-label": T.dismiss,
    onclick: (e) => { e.stopPropagation(); t.remove(); } }));
  document.body.append(t);
  placeToasts();
}
// Toasts open below the detail header, never over its buttons (Finish holds its top-right
// corner): at the top of the messages when that is in the upper half, else right under the
// header. renderDetail() re-places them, so the Undo toast that outlives a finished item
// moves out of the way of the next item's Finish. With nothing open the CSS default holds.
function placeToasts() {
  const box = (id) => { const el = $(id); return el && el.offsetParent ? el.getBoundingClientRect() : null; };
  const head = box("d-head"), msgs = box("msgs");
  let top = null;
  if (msgs && msgs.top > 0 && msgs.top < window.innerHeight / 2) top = msgs.top;
  else if (head && head.bottom > 0) top = head.bottom;
  for (const t of document.querySelectorAll("body > .toast")) {
    t.style.top = top === null ? "" : `${Math.round(top) + 8}px`;
  }
}

// ---------------------------------------------------------------- api
async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  // Token mode: the token the human entered once (passkey and Access sign-ins use cookies).
  const token = localStorage.getItem("token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (method !== "GET") { headers["X-Console-Request"] = "1"; headers["Content-Type"] = "application/json"; }
  const res = await fetch(API + path, { method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401) { showLogin(); throw new Error((data && data.error) || "HTTP 401"); }
  if (!res.ok) {
    // Callers that care (a 409 carrying the current version) read status and data.
    const err = new Error((data && data.error) || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function refresh() {
  try {
    const data = await api("/board");
    // An unchanged poll redraws nothing: rebuilding hundreds of rows every few
    // seconds is what makes a phone stutter. Relative times still tick once a minute.
    const sig = JSON.stringify([data.items, data.slots, data.places]);
    // A search hit pushed into state.items locally still gets reconciled as before.
    const unchanged = sig === state.boardSig && state.items.length === data.items.length
      && Date.now() - state.renderedAt < STALE_RENDER_MS;
    state.boardSig = sig;
    state.lastSync = new Date();
    $("sync").className = "sync";
    $("sync").textContent = `${T.updated} ${state.lastSync.toLocaleTimeString(LANG, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}`;
    if (unchanged) return;
    state.renderedAt = Date.now();
    state.items = data.items;
    state.byId = new Map(data.items.map((i) => [i.id, i]));
    window.ConsoleSlots.onBoard(data.items);
    state.slots = data.slots || {};
    state.places = data.places || [];
    window.ConsoleFocus.setGuide(data.guide || null);
    if (!state.defaultsLoaded) { state.defaultsLoaded = true; window.ConsoleBackend.loadDefaults(); }
    if (state.open && !state.byId.has(state.open)) state.open = null;
    // Whatever is on screen is being read.
    if (state.open && !document.hidden) markSeen(state.open);
    pruneSeen();
    render();
    window.ConsoleZen.onBoard();
    if (window.ConsoleNotify) window.ConsoleNotify.onBoard(data.items);
    const open = state.open && state.byId.get(state.open);
    if (open && open.thread_id && (open.running || open.queued)) loadMessages(open.id);
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
// "Due 10/14 18:00 (in 2d)" on every open row, "No due date" when there is none, so the
// list answers "when is it due?" without opening anything.
// How full the thread's context window is after its last turn: "🧠 45%" in a row,
// "🧠 45% · 90k / 200k" in the detail header. Amber from 70%, red from 90%.
function tokens(n) {
  return n >= 1e6 ? `${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : `${Math.round(n / 1000)}k`;
}
function contextTag(item, full) {
  const c = item.context;
  if (!c) return null;
  const level = c.pct >= 90 ? " full" : c.pct >= 70 ? " warm" : "";
  const detail = `${tokens(c.used)} / ${tokens(c.window)}`;
  return h("span", { class: (full ? "btn d-ctx" : "ctx") + level, title: T.context_hint(detail),
    text: full ? `🧠 ${c.pct}% · ${detail}` : `🧠 ${c.pct}%` });
}
function dueLeft(iso) {
  const min = (new Date(iso).getTime() - Date.now()) / 60000;
  if (min < 0) return T.due_over;
  if (min < 60) return T.due_in(`${Math.max(1, Math.round(min))}${T.u_min}`);
  if (min < 1440) return T.due_in(`${Math.round(min / 60)}${T.u_hour}`);
  return T.due_in(`${Math.round(min / 1440)}${T.u_day}`);
}
function dueLabel(item) {
  if (!item.due_at) {
    return item.bucket === "done" ? null : h("span", { class: "due none", text: T.no_due });
  }
  const soon = !item.overdue && new Date(item.due_at).getTime() - Date.now() < 86400000;
  return h("span", { class: "due" + (item.overdue ? " over" : soon ? " soon" : "") + (item.due_by === "ai" ? " ai" : ""),
    title: item.due_by === "ai" ? T.ai_set : null,
    text: `${T.due} ${shortDate(item.due_at)}` + (item.bucket === "done" ? "" : `（${dueLeft(item.due_at)}）`) });
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
  return [item.title, item.project, item.workspace_path, item.thread_name, item.working_dir].some((v) => v && v.toLowerCase().includes(q));
}
// Pinned items ("working on now") open every view but Done, newest pin first, and leave
// their usual place so nothing is listed twice — except in the tree, whose shape needs them.
const isPinned = (i) => Boolean(i.pinned_at) && i.bucket !== "done";
function visibleRows() {
  return window.ConsoleFamily.groupRows(ungroupedRows());
}
// The status chips sit on top of every view and narrow it; they count the view as it is
// before narrowing, so switching one on never hides the others it offers.
function ungroupedRows() {
  const shown = state.items.filter((i) => matches(i) && !(hidingDone() && i.bucket === "done"));
  // The Workspace chips count the view before either filter; the status chips, after it.
  const WF = window.ConsoleWsFilter;
  const inView = state.view === "focus" || state.view === "tree" || state.view === "recent"
    ? shown : shown.filter((i) => i.bucket === state.view);
  const wsBar = WF.bar(inView);
  const items = WF.apply(shown);
  const SF = window.ConsoleStatusFilter;
  const all = pinnedFirst(items);
  const bar = SF.bar(all);
  const rows = SF.active() ? pinnedFirst(SF.apply(items)) : all;
  return [wsBar && { node: wsBar }, bar && { node: bar }, ...rows].filter(Boolean);
}
function pinnedFirst(items) {
  const pinned = state.view === "done" ? [] : items.filter(isPinned)
    .sort((a, b) => (b.pinned_at || "").localeCompare(a.pinned_at || ""));
  if (!pinned.length) return viewRows(items);
  const rest = state.view === "tree" ? viewRows(items) : dropEmptyGroups(viewRows(items.filter((i) => !isPinned(i))));
  return [{ group: T.pinned_group, cls: "group pinned-group" }, ...pinned.map((item) => ({ item, depth: 0 })), ...rest];
}
// A heading whose rows all moved to the pinned strip has nothing left under it.
function dropEmptyGroups(rows) {
  return rows.filter((r, i) => r.group === undefined ||
    rows.slice(i + 1).find((n) => n.group !== undefined || n.item)?.item !== undefined);
}
function viewRows(items) {
  if (state.view === "tree") return treeRows(items.filter((i) => i.bucket !== "done"));
  if (state.view === "recent") return window.ConsoleRecent.rows(items);
  if (state.view === "focus") return window.ConsoleFocus.rows(items);
  return window.ConsoleWsFilter.rows(items.filter((i) => i.bucket === state.view));
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

// The Done view always shows its items; everywhere else the switch decides.
function hidingDone() { return state.hideDone && state.view !== "done"; }
async function togglePin(id) {
  const item = state.byId.get(id);
  if (!item) return;
  await patch(id, { pinned: !item.pinned_at });
}
function setHideDone(on) {
  state.hideDone = on;
  localStorage.setItem("hideDone", on ? "1" : "0");
  render();
}
function syncHideDone() {
  const btn = $("hide-done");
  if (!btn) return;
  const inDone = state.view === "done";
  btn.textContent = state.hideDone ? T.hide_done_on : T.hide_done_off;
  btn.classList.toggle("on", state.hideDone);
  btn.setAttribute("aria-pressed", state.hideDone ? "true" : "false");
  btn.disabled = inDone;
  btn.title = inDone ? T.hide_done_in_done : T.hide_done_hint;
}

// ---------------------------------------------------------------- render
function render() {
  syncHash();
  syncHideDone();
  renderNav();
  renderList();
  renderDetail();
  window.ConsoleZen.paint();
}

function renderNav() {
  const counts = {};
  for (const i of state.items) counts[i.bucket] = (counts[i.bucket] || 0) + 1;
  counts.tree = state.items.filter((i) => i.bucket !== "done").length;
  counts.focus = window.ConsoleFocus.count(state.items);
  const nav = $("nav-views");
  nav.replaceChildren(
    ...VIEWS.map((v) => v.sep ? h("div", { class: "sep" }) : h("button", {
      class: state.view === v.key ? "active" : "",
      "aria-current": state.view === v.key ? "page" : null,
      onclick: () => setView(v.key),
    }, h("span", { text: T[v.key] }), h("span", {
      class: "count" + (v.hot && counts[v.key] ? " hot" : ""), text: counts[v.key] || "",
    }))),
    window.ConsoleSlots.nav(),
    window.ConsoleWorkspace.nav(),
    window.ConsoleNoteSearch.nav(),
    window.ConsoleBackend.nav(),
    window.ConsoleBackend.childNav(),
    window.ConsoleNotify ? h("button", { class: "nav-notify" + (window.ConsoleNotify.isOn() ? " on" : ""),
      onclick: () => window.ConsoleNotify.toggle() },
      h("span", { text: window.ConsoleNotify.isOn() ? T.notify_on : T.notify_off })) : null,
    h("button", { class: "nav-auth", onclick: () => window.ConsoleAuth.showDevices() },
      h("span", { text: `🔑 ${window.ConsoleAuth.label}` })),
  );
  $("view-title").textContent = T[state.view];
  const hot = counts.me || 0;
  document.title = hot ? `(${hot}) Ebi AI Console` : "Ebi AI Console";
}

function renderList() {
  const rows = visibleRows();
  window.ConsoleBulk?.sync(rows);
  const list = $("list");
  const top = list.scrollTop;
  const edge = list.getBoundingClientRect().top;
  // Anchor what the reader sees, not the selection (which may be far off-screen).
  // Keeping only scrollTop would still jump when polling inserts/reorders rows above it.
  const anchor = top > 0 && [...list.querySelectorAll(".row")]
    .find((row) => row.getBoundingClientRect().bottom > edge);
  const offset = anchor ? anchor.getBoundingClientRect().top - edge : 0;
  const nodes = [];
  const occurrences = new Map();
  let group = null;
  for (const row of rows) {
    if (row.group !== undefined) { group = null; nodes.push(h("li", { class: row.cls || "group", text: row.group })); continue; }
    if (row.node) { group = null; nodes.push(row.node); continue; }
    const occurrence = occurrences.get(row.item.id) || 0;
    const node = rowNode({...row, rowKey: `${row.item.id}:${occurrence}`});
    node.dataset.occurrence = String(occurrence);
    occurrences.set(row.item.id, occurrence + 1);
    if (row.family) {
      if (group?.id !== row.family.root.id) {
        const shown = rows.filter(r => r.family?.root.id === row.family.root.id && r.depth > 0).length;
        const card = window.ConsoleFamily.listGroup(row.family, shown);
        nodes.push(card);
        group = {id:row.family.root.id, list:card.querySelector('.thread-group-rows')};
      }
      group.list.append(node);
    } else { group = null; nodes.push(node); }
  }
  list.replaceChildren(...nodes);
  window.ConsoleBulk?.toolbar();
  const empty = $("empty");
  empty.hidden = rows.some((r) => r.item);
  empty.textContent = T.empty;
  const restored = anchor && [...list.querySelectorAll('.row')].find((row) => row.dataset.itemId === anchor.dataset.itemId && row.dataset.occurrence === anchor.dataset.occurrence);
  list.scrollTop = restored
    ? list.scrollTop + restored.getBoundingClientRect().top - edge - offset
    : top;
}

// Only intentional navigation may bring an off-screen selection into view.
function revealSelection() {
  const sel = $("list").querySelector(".row.sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
}

function rowNode({ item, depth, children, rowKey, family }) {
  const meta = [];
  const wsTag = window.ConsoleWsBind.badge(item);
  if (wsTag) meta.push(wsTag);
  const familyTag = family && depth > 1 ? h('span', {class:'tag', text:`↳ ${state.byId.get(item.parent_id)?.title || item.parent_id}`}) : null;
  if (familyTag) meta.push(familyTag);
  // In Recent, name the list the item lives in now, unless its status already says so.
  if (state.view === "recent" && T[item.bucket] && T[item.bucket] !== T.st[item.status]) {
    meta.push(h("span", { class: "tag where", text: T[item.bucket] }));
  }
  if (state.view !== "tree" && item.project) meta.push(h("span", { class: "tag", text: item.project }));
  const place = window.ConsoleFocus.placeTag(item);
  if (place) meta.push(place);
  const effort = item.focus !== null && item.focus !== undefined ? window.ConsoleFocus.effortTag(item) : null;
  if (effort) meta.push(effort);
  if (state.view === "focus" && item.series > 1) {
    meta.push(h("span", { class: "tag", title: T.series_hint(item.series), text: T.series(item.series) }));
  }
  meta.push(h("span", { class: item.running ? "live" : "", text: T.st[item.status] || item.status }));
  if (item.queued) meta.push(h("span", { text: T.queued }));
  if (item.paused_at && !item.running) meta.push(h("span", { class: "tag paused", text: `⏸ ${T.paused}` }));
  if (item.last_activity_at) meta.push(h("span", { text: rel(item.last_activity_at) }));
  if (item.working_dir) meta.push(h("span", { text: item.working_dir.split("/").slice(-1)[0] }));
  const ctx = contextTag(item, false);
  if (ctx) meta.push(ctx);
  // A value the model filled in is marked, so a guess never passes for a decision.
  const controls = window.ConsoleSlots.controls(item, true);
  const side = [controls.length ? h("span", { class: "row-slot" }, ...controls) : null,
    h("span", { class: `pri p${item.priority}` + (item.priority_by === "ai" ? " ai" : ""),
    title: item.priority_by === "ai" ? T.ai_set : null, text: `P${item.priority}` })];
  side.push(dueLabel(item));
  const twisty = children
    ? h("span", { class: "tw", onclick: (e) => { e.stopPropagation(); toggleCollapse(item.id); },
        text: state.collapsed.has(item.id) ? "▸" : "▾" })
    : (depth ? h("span", { class: "tw", text: "" }) : null);
  return h("li", {
    class: "row" + (family ? (depth ? " family-child" : " family-root") : "") + (window.ConsoleBulk?.has(item.id) ? " bulk-selected" : "") + (state.selected === item.id ? " sel" : "") + (item.bucket === "done" ? " done" : "") +
      (isUnread(item) ? " fresh" : "") + (isPinned(item) ? " pinned" : ""),
    role: "option", "data-item-id": item.id, "aria-selected": state.selected === item.id ? "true" : "false",
    onclick: (event) => {
      if (window.ConsoleBulk?.click(event, item.id, rowKey)) return;
      // Open the row that was clicked; a child's detail links back to its parent.
      openItem(item.id);
    },
  },
    h("span", {class:"row-select"}, window.ConsoleBulk?.checkbox(item,rowKey),
      h("span", { class: "st", title: T.st[item.status], text: STATUS_ICON[item.status] || "·" })),
    h("div", { class: "main", style: depth ? { paddingLeft: `${family ? Math.min(depth - 1, 2) * 10 + 12 : depth * 18}px` } : null },
      h("div", { class: "title" }, twisty,
        family ? window.ConsoleFamily.roleTag(depth) : null,
        state.selected === item.id ? h("span", {class:"selection-label",text: document.documentElement.lang === "ja" ? "▶ 選択中" : "▶ Selected"}) : null,
        item.title || item.thread_name || item.id),
      h("div", { class: "meta" }, ...meta),
      state.view === "focus" ? window.ConsoleFocus.note(item) : null),
    h("div", { class: "side" }, ...side),
  );
}

// The detail pane keeps one skeleton per item: header, a collapsible drawer for the
// triage fields, the conversation, the live panel slot and the reply box. A board
// refresh rewrites only the header and drawer, so the conversation never loses its
// scroll position and the reply box never loses what is being typed.
function renderDetail() {
  const pane = $("detail");
  const item = state.open && state.byId.get(state.open);
  $("app").classList.toggle("no-detail", !item);
  pane.hidden = !item;
  if (!item) { pane.replaceChildren(); delete pane.dataset.item; placeToasts(); return; }
  const key = `${item.id}|${item.thread_id ? 1 : 0}|${LANG}`;
  if (pane.dataset.item !== key || !$("msgs")) buildDetail(pane, item, key);

  // Leave the header alone while one of its fields is being edited.
  const active = document.activeElement;
  const editing = active && active.matches("input:not([type=checkbox]), select") &&
    ($("d-head").contains(active) || $("d-drawer").contains(active));
  if (!editing) {
    // Finish sits outside the wrapping row so it keeps the top-right corner
    // however many optional controls a thread has. An unchanged Finish stays the same
    // node: swapping it between mousedown and mouseup would swallow the click.
    const head = $("d-head"), finish = window.ConsoleFinish.button(item);
    const row = h("div", { class: "d-head-row" }, ...headParts(item).filter(Boolean));
    const oldRow = head.querySelector(":scope > .d-head-row");
    if (oldRow) oldRow.replaceWith(row); else head.prepend(row);
    const oldFinish = head.querySelector(":scope > .d-finish");
    if (oldFinish !== finish) { if (oldFinish) oldFinish.replaceWith(finish); else head.append(finish); }
    $("d-drawer").replaceChildren(...drawerParts(item).filter(Boolean));
  }
  $("d-drawer").hidden = !state.drawerOpen;
  window.ConsoleMobile.layout(item, !editing);
  window.ConsoleFamily.render(item);
  window.ConsoleWsPane.render(item);
  window.ConsoleThreadNotes.render(item);
  window.ConsoleZen.bar(pane);
  placeToasts();
  const msgs = $("msgs");
  if (item.thread_id) fillMessages(msgs);
  else msgs.replaceChildren(item.note
    ? h("div", { class: "msg" }, h("div", { class: "body md" }, MD.render(item.note)))
    : h("div", { class: "empty", text: T.no_messages }));
}

function buildDetail(pane, item, key) {
  pane.dataset.item = key;
  const msgs = h("div", { class: "msgs", id: "msgs", onscroll: updateJump });
  const jump = h("button", { class: "jump", id: "jump", type: "button", hidden: true,
    onclick: () => { msgs.scrollTop = msgs.scrollHeight; updateJump(); } });
  const nodes = [
    h("div", { class: "d-head", id: "d-head" }),
    h("div", { class: "d-drawer", id: "d-drawer", hidden: true }),
    window.ConsoleBackend.controls(item),
    h("section", { class: "family", id: "d-family", hidden: true, "aria-label": LANG === "ja" ? "親子スレッド" : "Thread family" }),
    h("section", { class: "ws-pane", id: "d-ws", hidden: true, "aria-label": "Ebi Workspace" }),
    h("section", { class: "tnotes", id: "d-notes", hidden: true }),
    h("div", { class: "msgs-wrap" }, msgs, jump),
    h("div", { class: "live-slot", id: "live-slot" }),
  ];
  if (item.thread_id) {
    // Drafts survive the refresh and switching between items.
    const box = h("textarea", { id: "reply", placeholder: T.reply_ph, rows: 3,
      oninput: (e) => { state.drafts[item.id] = e.target.value; grow(e.target); },
      onkeydown: (e) => { if (isSendKey(e)) { e.preventDefault(); sendReply(item.id, box); } } });
    const tray = h("div", { class: "paste-tray", hidden: true });
    const meta = h("div", { class: "reply-meta", hidden: true });
    const form = h("form", { class: "reply", onsubmit: (e) => { e.preventDefault(); sendReply(item.id, box); } },
      h("div", { class: "reply-main" }, meta, tray, box),
      h("div", { class: "reply-side" }, window.ConsolePaste.button(item.id),
        h("button", { class: "btn primary", type: "submit", text: T.send })));
    nodes.push(form);
    box.value = state.drafts[item.id] || "";
    window.ConsolePaste.attach(box, item.id, tray);
    window.ConsoleSlash.attach(box, item.id, meta);
    window.ConsoleBoxSize.attach(form, box, grow);
    requestAnimationFrame(() => grow(box));
  }
  pane.replaceChildren(...nodes.filter(Boolean));
}

function headParts(item) {
  const title = h("input", { class: "d-title-input", value: item.title || item.thread_name || "", "aria-label": "title",
    onchange: (e) => patch(item.id, { title: e.target.value || null }) });
  const parts = [
    h("button", { class: "icon-btn", "aria-label": "close", onclick: closeItem, text: "←" }),
    h("span", { class: "d-status", title: T.st[item.status], text: STATUS_ICON[item.status] || "·" }),
    title,
    h("button", { class: "icon-btn only-narrow detail-nav-toggle", "aria-label": "Menu", onclick: () => $("nav").classList.toggle("open"), text: "☰" }),
    window.ConsoleLinks.button(item),
    window.ConsoleZen.button(item),
  ];
  if (!item.thread_id) parts.push(h("button", { class: "btn primary", onclick: () => startItem(item.id), text: T.start }));
  const ctx = contextTag(item, true);
  if (ctx) parts.push(ctx);
  const pinned = Boolean(item.pinned_at);
  parts.push(h("button", { class: "btn d-pin" + (pinned ? " on" : ""), type: "button", title: T.pin_hint,
    "aria-pressed": pinned ? "true" : "false", onclick: () => togglePin(item.id),
    text: pinned ? `📌 ${T.unpin}` : `📌 ${T.pin}` }));
  parts.push(window.ConsolePlaces.picker(item));
  const ready = window.ConsoleFocus.readyToggle(item);
  if (ready) parts.push(ready);
  parts.push(window.ConsoleWsBind.control(item));
  // Tool calls and status lines: one click away, never folded into the drawer.
  if (item.thread_id) parts.push(activityToggle());
  parts.push(h("button", { class: "btn d-more" + (state.drawerOpen ? " on" : ""), type: "button",
    "aria-expanded": state.drawerOpen ? "true" : "false", onclick: toggleDrawer,
    text: `${state.drawerOpen ? "▴" : "▾"} ${T.details}` }));
  parts.push(...window.ConsoleSlots.controls(item, false));
  return parts;
}

function drawerParts(item) {
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
  if (item.url) actions.push(h("a", { class: "btn", href: item.url, target: "_blank", rel: "noopener", text: T.open_chat }));
  return [
    h("div", { class: "controls" },
      h("label", {}, T.priority, pri), h("label", {}, T.due, due),
      h("label", {}, T.project, project), h("label", {}, T.parent, parent)),
    window.ConsoleFocus.drawerLine(item),
    h("div", { class: "controls" }, h("span", { class: "dim", text: T.snooze }), ...snooze),
    h("div", { class: "controls" }, ...actions),
  ];
}

function toggleDrawer() {
  state.drawerOpen = !state.drawerOpen;
  localStorage.setItem("drawerOpen", state.drawerOpen ? "1" : "0");
  const box = $("msgs");
  const pinned = box && nearBottom(box);
  renderDetail();
  if (pinned) box.scrollTop = box.scrollHeight;
}

// "Jump to latest" appears when the reader has scrolled up; it says so when new
// messages arrived below.
function nearBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 80; }
function updateJump() {
  const box = $("msgs");
  const jump = $("jump");
  if (!box || !jump) return;
  if (nearBottom(box)) state.unseen = 0;
  jump.hidden = nearBottom(box);
  jump.textContent = state.unseen ? T.jump_new(state.unseen) : T.jump;
  jump.classList.toggle("hot", state.unseen > 0);
}

function activityCount() {
  return state.messagesFor === state.open ? state.messages.filter((m) => m.kind === "activity").length : 0;
}
function activityToggle() {
  const n = activityCount();
  return h("button", { class: "btn d-activity" + (state.showActivity ? " on" : ""), id: "d-activity", type: "button",
    "aria-pressed": state.showActivity ? "true" : "false", hidden: !n && !state.showActivity,
    title: T.activity_hint, onclick: () => setShowActivity(!state.showActivity), text: T.activity(n) });
}
// The count is known once the messages arrive, after the header was drawn.
function syncActivityToggle() {
  const old = $("d-activity");
  if (old) old.replaceWith(activityToggle());
}
function setShowActivity(on) {
  state.showActivity = on;
  localStorage.setItem("showActivity", on ? "1" : "0");
  const box = $("msgs");
  if (box) fillMessages(box, { force: true, toBottom: true });
  renderDetail();
}
// Textareas grow with what is typed, up to the CSS max-height. A reply box the user has
// dragged keeps exactly that height, however long the text (boxsize.js).
function grow(box) {
  const fixed = window.ConsoleBoxSize.fixed(box);
  if (fixed) { box.style.height = `${fixed}px`; return; }
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight + 2}px`;
}

function messageNode(m) {
  const kind = m.kind || (m.is_bot ? "agent" : "human");
  const body = h("div", { class: "body md" });
  const sent = window.ConsoleWsPane.splitMessage(m.content);
  const md = (text) => window.ConsoleNotes.linkify(MD.render(text));
  if (sent) body.append(md(sent.text), window.ConsoleWsPane.sentNote(sent));
  else if (m.content) body.append(md(m.content + (m.truncated ? " …" : "")));
  for (const e of m.embeds || []) {
    const card = h("div", { class: "embed" });
    if (e.color != null) card.style.borderLeftColor = `#${e.color.toString(16).padStart(6, "0")}`;
    if (e.title) card.append(h("div", { class: "embed-title", text: e.title }));
    if (e.description) card.append(h("div", { class: "md" }, MD.render(e.description)));
    body.append(card);
  }
  for (const a of m.attachments || []) {
    const remote = /^https:\/\//.test(a.url);
    if (remote && isImage(a)) body.append(chatImage(a));
    const P = window.ConsolePreview;
    const look = remote && P && P.kind(a.filename)
      ? h("button", { class: "btn file-look", type: "button", text: `👁 ${P.label}`,
          onclick: () => P.open(a.filename, () => P.fetchBlob(a.url),
            () => window.open(a.url, "_blank", "noopener,noreferrer")) })
      : null;
    body.append(h("div", { class: "file" }, remote
      ? h("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", text: `📎 ${a.filename}` })
      : h("span", { text: `📎 ${a.filename}` }), look, look ? P.copyButton(() => P.fetchBlob(a.url)) : null));
  }
  if ((m.actions || []).length) body.append(actionRow(m));
  return h("div", { class: `msg ${kind}` },
    h("div", { class: "who" }, h("span", { text: m.model ? `${m.author} · ${m.model}` : m.author }),
      h("span", { text: m.created_at ? shortDate(m.created_at) : "" })),
    body);
}

// Buttons a Discord message is waiting on (restart approval, choices, questions).
function actionRow(m) {
  const id = state.open;
  const row = h("div", { class: "msg-actions" });
  for (const a of m.actions) {
    row.append(h("button", { class: `btn act-${a.style}`, type: "button", text: a.label, onclick: async () => {
      for (const b of row.querySelectorAll("button")) b.disabled = true;
      try {
        await api(`/items/${encodeURIComponent(id)}/messages/${m.id}/actions/${encodeURIComponent(a.id)}`, { method: "POST" });
        toast(T.pressed(a.label));
      } catch (err) { toast(err.message); }
      await loadMessages(id);
    } }));
  }
  return row;
}

// Images attached in a chat thread load straight from Discord's CDN (allowed by the CSP for img only).
function isImage(a) {
  if (a.content_type) return a.content_type.startsWith("image/");
  return /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(a.filename || "");
}
function chatImage(a) {
  const open = () => window.ConsoleLightbox && window.ConsoleLightbox(a.filename, a.url,
    () => window.open(a.url, "_blank", "noopener,noreferrer"));
  const img = h("img", { src: a.url, alt: a.filename, loading: "lazy" });
  const btn = h("button", { class: "file-thumb", type: "button", title: a.filename, onclick: open }, img);
  // The image's height arrives after the messages rendered; a reader at the end stays there.
  img.addEventListener("load", () => {
    const box = $("msgs");
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight - img.offsetHeight < 80) box.scrollTop = box.scrollHeight;
  }, { once: true });
  img.addEventListener("error", () => btn.remove(), { once: true });
  return btn;
}

function fillMessages(container, { force = false, toBottom = false } = {}) {
  if (state.messagesFor !== state.open) {
    if (!container.dataset.sig) container.replaceChildren(h("div", { class: "empty", text: T.loading }));
    return;
  }
  const shown = state.showActivity ? state.messages : state.messages.filter((m) => m.kind !== "activity");
  // Re-render only when something changed, so a poll does not drop a text selection.
  const sig = `${state.open}|${state.showActivity}|${state.messages.length}|${state.messages.map((m) => m.id).slice(-1)[0]}|` +
    state.messages.map((m) => (m.content || "").length).reduce((a, b) => a + b, 0) + "|" +
    state.messages.map((m) => (m.actions || []).length).join("");
  if (!force && container.dataset.sig === sig && container.childElementCount) return;
  const first = !container.dataset.sig || !container.dataset.sig.startsWith(`${state.open}|`);
  const follow = toBottom || first || nearBottom(container);
  const before = container.dataset.count ? Number(container.dataset.count) : shown.length;
  const top = container.scrollTop;
  container.dataset.sig = sig;
  container.dataset.count = String(shown.length);
  container.replaceChildren(...(shown.length ? shown.map(messageNode) : [h("div", { class: "empty", text: T.no_messages })]));
  if (follow) container.scrollTop = container.scrollHeight;
  else {
    // Replacing every child can reset the scroll position; put the reader back where they were.
    container.scrollTop = top;
    if (shown.length > before) state.unseen = (state.unseen || 0) + shown.length - before;
  }
  updateJump();
}

// ---------------------------------------------------------------- actions
function syncHash() {
  // The note viewer owns #note/… while it is open (notes.js).
  if (location.hash.startsWith("#note/")) return;
  const target = "#" + state.view + (state.open ? "/" + state.open : "");
  if (location.hash !== target) history.replaceState(null, "", target);
}
function readHash() {
  const [view, item] = location.hash.slice(1).split("/");
  if (VIEWS.some((v) => v.key === view)) state.view = view;
  if (item) { state.open = item; state.selected = item; }
}
function setView(view) {
  if (matchMedia("(max-width: 900px)").matches) {
    markSeen(state.open); state.open = null; state.drawerOpen = false;
  }
  state.view = view;
  localStorage.setItem("view", view);
  $("nav").classList.remove("open");
  const rows = visibleRows().filter((r) => r.item);
  state.selected = rows.length ? rows[0].item.id : null;
  render();
  revealSelection();
}
// Unread, the way a mailbox means it: something happened in the thread after the
// human last looked at it. "Last looked" is kept per item in this browser; items never
// opened count from a baseline set on first use (a day back, so recent work shows).
const seenAt = JSON.parse(localStorage.getItem("seenAt") || "{}");
const seenBaseline = localStorage.getItem("seenBaseline") || (() => {
  const b = new Date(Date.now() - 864e5).toISOString();
  localStorage.setItem("seenBaseline", b);
  return b;
})();
function isUnread(item) {
  // A running turn keeps moving its activity time; it becomes news when it stops.
  if (item.running || item.queued || !item.last_activity_at || item.id === state.open) return false;
  if (item.bucket === "done" || item.bucket === "snoozed") return false;
  return new Date(item.last_activity_at) > new Date(seenAt[item.id] || seenBaseline);
}
function markSeen(id) {
  if (!id) return;
  seenAt[id] = new Date().toISOString();
  localStorage.setItem("seenAt", JSON.stringify(seenAt));
}
function pruneSeen() {
  let changed = false;
  for (const id of Object.keys(seenAt)) if (!state.byId.has(id)) { delete seenAt[id]; changed = true; }
  if (changed) localStorage.setItem("seenAt", JSON.stringify(seenAt));
}
function toggleCollapse(id) {
  state.collapsed.has(id) ? state.collapsed.delete(id) : state.collapsed.add(id);
  localStorage.setItem("collapsed", JSON.stringify([...state.collapsed]));
  renderList();
}
async function openItem(id) {
  if (state.open !== id) state.directReply = null;
  if (state.open !== id) state.unseen = 0;
  state.open = id;
  state.selected = id;
  if (state.open && state.open !== id) markSeen(state.open);
  markSeen(id);
  state.messagesFor = null;
  render();
  revealSelection();
  const item = state.byId.get(id);
  if (!item || !item.thread_id) { state.messagesFor = id; state.messages = []; return; }
  await loadMessages(id);
}
async function loadMessages(id) {
  try {
    const data = await api(`/items/${encodeURIComponent(id)}/messages?limit=${HISTORY}`);
    if (state.open !== id) return;
    state.messages = data.messages;
    state.messagesFor = id;
    const box = $("msgs");
    if (box) fillMessages(box);
    syncActivityToggle();
  } catch (err) { toast(err.message); }
}
function closeItem() {
  // Working through a queue, leaving a thread means "next one" (zen.js).
  if (window.ConsoleZen.onClose(state.open)) return;
  markSeen(state.open); state.open = null; render();
}
async function patch(id, changes) {
  try { await api(`/items/${encodeURIComponent(id)}`, { method: "PATCH", body: changes }); toast(T.saved); await refresh(); }
  catch (err) { toast(err.message); }
}
// Every text box sends on Ctrl/Cmd+Enter; a plain Enter is always a newline.
const isSendKey = (e) => e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing;
// Items with a send in flight: repeated Ctrl+Enter or clicks must not post the text twice.
const sending = new Set();
async function sendReply(id, box) {
  if (sending.has(id)) return;
  const text = box.value.trim();
  const files = window.ConsolePaste.ready(id);
  if (files === null) { toast(T.paste_wait); return; }
  if (!text && !files.length) return;
  sending.add(id);
  box.disabled = true;
  try {
    // "/backend codex", "/model opus", "/kb-save …": a command, not a reply.
    if (!files.length && await window.ConsoleSlash.run(id, text)) {
      box.value = "";
      delete state.drafts[id];
      grow(box);
      if (!/^\/(backend|model|effort)\b/.test(text)) { await refresh(); setTimeout(() => loadMessages(id), 1500); }
      return;
    }
    const body = await window.ConsolePaste.prepareText(text, files);
    const sent = await api(`/items/${encodeURIComponent(id)}/reply`, { method: "POST", body });
    box.value = "";
    delete state.drafts[id];
    window.ConsolePaste.clear(id);
    toast(sent && sent.delivery === "injected" ? T.sent_live : T.sent);
    window.ConsoleZen.sent(id);
    await refresh();
    setTimeout(() => loadMessages(id), 1500);
  } catch (err) { toast(err.message); }
  finally { sending.delete(id); box.disabled = false; box.focus(); }
}
async function startItem(id) {
  try {
    const data = await api(`/items/${encodeURIComponent(id)}/start`, { method: "POST", body: {} });
    toast(T.started);
    await refresh();
    openItem(data.item.id);
  } catch (err) { toast(err.message); }
}
// Capture submission is locked before the first await, including attachment preparation.
let capturePending = false;
function setCapturePending(pending) {
  capturePending = pending;
  $("capture").setAttribute("aria-busy", String(pending));
  $("capture-input").readOnly = pending;
  $("capture-status").textContent = pending ? T.capturing : "";
  for (const control of $("capture").querySelectorAll("button, input, select")) control.disabled = pending;
}
async function capture(text) {
  if (capturePending) return false;
  setCapturePending(true);
  let accepted = false;
  try {
    const start = autostart();
    // First line is the title; anything after it is the note.
    const [first, ...rest] = text.split("\n");
    const body = { title: first.trim().slice(0, 200), start };
    if ($("capture-pin").checked) body.pinned = true;
    const workspacePath = window.ConsoleWorkspace?.path();
    if (workspacePath) body.workspace_path = workspacePath;
    const note = first.trim().length > 200 ? text : rest.join("\n").trim();
    if (note) body.note = note;
    const files = window.ConsolePaste.ready(CAPTURE);
    if (files === null) { toast(T.paste_wait); return false; }
    if (files.length) body.files = files;
    const prepared = await window.ConsolePaste.prepareText(text, files);
    if (prepared.text !== text) body.note = prepared.text;
    if (prepared.files.length) body.files = prepared.files;
    const data = await api("/items", { method: "POST", body });
    accepted = true;
    // A Workspace picked for this capture applies to it only; the next one starts unresolved.
    window.ConsoleWorkspace?.consumed();
    window.ConsolePaste.clear(CAPTURE);
    // The pin is for this request only, like the Workspace pick.
    $("capture-pin").checked = false;
    const started = Boolean(data.item.thread_id);
    await refresh();
    // Show where it landed: Running once an agent has it, otherwise To do.
    if (started) { if (state.view !== "ai" && state.view !== "tree") setView("ai"); }
    else if (state.view !== "todo" && state.view !== "tree") setView("todo");
    state.selected = data.item.id;
    renderList();
    revealSelection();
    toast(data.start_error || (started ? T.started : T.captured));
    return true;
  } catch (err) { toast(err.message); return accepted; }
  finally { setCapturePending(false); }
}
// The capture box's attachments are kept under this key, apart from each item's reply.
const CAPTURE = "__capture__";
// Captured work goes straight to an agent unless the user turned that off.
function autostart() { return localStorage.getItem("autostart") !== "0"; }
function syncCapture() {
  $("capture-start").checked = autostart();
  $("capture-input").placeholder = autostart() ? T.capture_ai : T.capture;
  $("capture-send").textContent = T.send;
  $("capture-pin-label").textContent = T.capture_pin;
  $("capture-pin-label").parentElement.title = T.capture_pin_hint;
}

function move(delta) {
  const rows = visibleRows().filter((r) => r.item);
  if (!rows.length) return;
  let i = rows.findIndex((r) => r.item.id === state.selected);
  i = i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + delta));
  state.selected = rows[i].item.id;
  if (state.open) openItem(state.selected);
  else { renderList(); revealSelection(); }
}
function showLogin() {
  window.ConsoleAuth.showLogin(refresh);
}
function showHelp(show) {
  $("help").dataset.mode = "help";
  const help = $("help");
  help.hidden = !show;
  if (show) help.replaceChildren(h("div", { class: "card" }, h("table", {},
    T.help.map(([k, v]) => h("tr", {}, h("td", {}, h("kbd", { text: k })), h("td", { text: v }))))));
}

// ---------------------------------------------------------------- wiring
function setLanguage(language) {
  if (!["ja", "en"].includes(language) || language === LANG) return;
  const messageScroll = $("msgs")?.scrollTop || 0;
  $("nav").classList.remove("open");
  LANG = language;
  T = I18N[LANG];
  localStorage.setItem("lang", LANG);
  document.documentElement.lang = LANG;
  const url = new URL(location.href);
  url.searchParams.set("lang", LANG);
  history.replaceState(null, "", url);
  $("filter").placeholder = T.filter;
  $("capture-start-label").textContent = T.autostart;
  syncCapture();
  const attach = $("capture").querySelector(".attach");
  if (capturePending) $("capture-status").textContent = T.capturing;
  attach.title = T.attach_hint;
  attach.setAttribute("aria-label", T.attach_hint);
  render();
  if ($("msgs")) $("msgs").scrollTop = messageScroll;
  window.dispatchEvent(new Event("console-language-change"));
}
function wire() {
  $("language-select").value = LANG;
  $("language-select").addEventListener("change", e => setLanguage(e.target.value));
  $("filter").placeholder = T.filter;
  window.ConsoleSearch?.wire();
  $("capture-start-label").textContent = T.autostart;
  syncCapture();
  $("capture-start").addEventListener("change", (e) => {
    localStorage.setItem("autostart", e.target.checked ? "1" : "0");
    syncCapture();
  });
  $("filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); });
  $("hide-done").addEventListener("click", () => setHideDone(!state.hideDone));
  const submitCapture = () => {
    const input = $("capture-input");
    const text = input.value.trim();
    if (!text) return;
    capture(text).then((ok) => { if (ok) { input.value = ""; grow(input); } });
  };
  window.ConsolePaste.attach($("capture-input"), CAPTURE, $("capture-tray"));
  $("capture-attach").replaceWith(window.ConsolePaste.button(CAPTURE));
  $("capture").addEventListener("submit", (e) => { e.preventDefault(); submitCapture(); });
  // Do not let a drop/paste add files to the pending submission's attachment tray.
  for (const type of ["drop", "paste"]) $("capture").addEventListener(type, (e) => {
    if (capturePending) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  $("capture-input").addEventListener("input", (e) => grow(e.target));
  $("capture-input").addEventListener("keydown", (e) => {
    if (isSendKey(e)) { e.preventDefault(); submitCapture(); }
  });
  $("nav-toggle").addEventListener("click", () => $("nav").classList.toggle("open"));
  $("help").addEventListener("click", () => { if ($("help").dataset.mode !== "login") showHelp(false); });

  document.addEventListener("keydown", (e) => {
    // A rich editor (contenteditable, possibly inside a shadow root) is typing too.
    const typing = e.target.matches("input, textarea, select, [contenteditable]")
      || e.composedPath().some((n) => n.isContentEditable);
    if (e.key === "Escape") {
      if (typing) { e.target.blur(); return; }
      if (!$("help").hidden) showHelp(false); else if (!window.ConsoleZen.escape()) closeItem();
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
      case "e": if (id && state.byId.get(id)?.bucket !== "done") window.ConsoleFinish.finish(id); break;
      case "p": if (id) togglePin(id); break;
      case "1": case "2": case "3": case "4": if (id) patch(id, { priority: Number(e.key) - 1 }); break;
      case "s": if (id) patch(id, { snoozed_until: snoozeTarget("tomorrow").toISOString() }); break;
      case "c": $("capture-input").focus(); break;
      case "h": setHideDone(!state.hideDone); break;
      case "r": if ($("reply")) $("reply").focus(); break;
      case "z": window.ConsoleZen.toggle(); break;
      case "n": if (window.ConsoleZen.active() === "queue") window.ConsoleZen.skip(); else return; break;
      case "/": $("filter").focus(); break;
      case "g": state.pendingG = true; setTimeout(() => { state.pendingG = false; }, 1200); break;
      case "?": showHelp(true); break;
      default: return;
    }
    e.preventDefault();
  });

  // A hidden tab keeps polling, more slowly, so a new notification still reaches the human.
  let timer = setInterval(refresh, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    clearInterval(timer);
    // Coming back always redraws, so the item on screen is marked read again.
    if (!document.hidden) { state.boardSig = ""; refresh(); }
    timer = setInterval(refresh, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
  });
}

readHash();
wire();
window.ConsoleLinks.wire();
window.ConsoleWorkspace.load();
render();
refresh().then(() => { if (state.open) openItem(state.open); });
