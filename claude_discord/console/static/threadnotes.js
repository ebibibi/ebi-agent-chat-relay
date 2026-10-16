// The vault notes a thread's session wrote, under the conversation: folded until opened,
// then each note with its diff since the thread started and a link into the note viewer.
// The server reads the session transcript and the vault's git history; excluded notes never
// appear. Loaded after notes.js and before app.js, which calls render() from renderDetail().
"use strict";

window.ConsoleThreadNotes = (() => {
  const cache = new Map(); // item id -> {at, data}
  const REFRESH_MS = 60_000;
  let open = false;
  let shown = "";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    title: "📝 このスレッドで変更したノート", loading: "読み込み中…", none: "このスレッドが書いたノートはありません",
    open: "開く", since: (s) => `スレッド開始（${s}）以降の差分。他の変更も含みます`,
    status: { added: "新規", modified: "更新" }, truncated: "…（長いので省略）",
  } : {
    title: "📝 Notes this thread changed", loading: "Loading…", none: "This thread wrote no notes",
    open: "Open", since: (s) => `Changes since the thread started (${s}), including any other edits`,
    status: { added: "new", modified: "changed" }, truncated: "… (cut, too long)",
  };

  async function load(item, pane) {
    const hit = cache.get(item.id);
    if (hit && Date.now() - hit.at < REFRESH_MS) return hit.data;
    try {
      const data = await api(`/items/${encodeURIComponent(item.id)}/notes`);
      cache.set(item.id, { at: Date.now(), data });
      return data;
    } catch (err) {
      pane.querySelector(".tnotes-body")?.replaceChildren(h("p", { class: "dim", text: err.message }));
      return null;
    }
  }

  function diffLines(text) {
    return h("div", { class: "note-diff" }, ...text.split("\n").filter((l) => !/^(diff --git|index |--- |\+\+\+ )/.test(l))
      .map((l) => h("div", { class: `note-diff-line ${l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "dim" : ""}`, text: l })));
  }

  function fill(pane, data) {
    const l = L();
    const body = pane.querySelector(".tnotes-body");
    if (!body) return;
    if (!data.notes.length) { body.replaceChildren(h("p", { class: "dim", text: l.none })); return; }
    pane.querySelector(".tnotes-count").textContent = ` (${data.notes.length})`;
    body.replaceChildren(
      data.since ? h("p", { class: "dim tnotes-since", text: l.since(shortDate(data.since)) }) : null,
      ...data.notes.map((n) => h("details", { class: "tnotes-note" },
        h("summary", {},
          h("span", { class: "tag", text: l.status[n.status] || n.status }), " ",
          h("span", { class: "tnotes-path", text: n.path.replace(/\.md$/, "") }), " ",
          h("button", { type: "button", class: "btn ghost tnotes-open", text: l.open,
            onclick: (e) => { e.preventDefault(); window.ConsoleNotes.open(n.path); } })),
        diffLines(n.diff),
        n.truncated ? h("p", { class: "dim", text: l.truncated }) : null)));
  }

  // Called from renderDetail on every redraw; builds once per item and loads when opened.
  function render(item) {
    const pane = document.getElementById("d-notes");
    if (!pane) return;
    const usable = item.thread_id && window.ConsoleWorkspace?.available();
    pane.hidden = !usable;
    if (!usable || shown === item.id) return;
    shown = item.id;
    const l = L();
    const details = h("details", { open, ontoggle: async (e) => {
      open = e.target.open;
      if (!open) return;
      const data = await load(item, pane);
      if (data && state.open === item.id) fill(pane, data);
    } }, h("summary", { class: "tnotes-summary" }, h("span", { text: l.title }), h("span", { class: "dim tnotes-count" })),
      h("div", { class: "tnotes-body" }, h("p", { class: "dim", text: l.loading })));
    pane.replaceChildren(details);
    if (open) load(item, pane).then((data) => { if (data && state.open === item.id) fill(pane, data); });
  }

  return { render };
})();
