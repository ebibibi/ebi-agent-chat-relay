// Edit a note in the viewer: Markdown source beside a live preview (stacked with tabs on a
// phone). Ctrl+S / ⌘S saves. The save carries the revision the edit started from; if an
// agent or Obsidian changed the file meanwhile, the server refuses (409) and this shows what
// differs, then lets the human reload theirs away or overwrite on purpose.
// No dependency: a plain textarea. (An optional richer editor can replace it later.)
// Loaded after notes.js, which calls start() and supplies the renderer.
"use strict";

window.ConsoleNoteEdit = (() => {
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    save: "💾 保存", cancel: "やめる", log: "作業ログにも記録", edit: "編集", preview: "プレビュー",
    saved: "保存しました", leave: "保存していない変更があります。破棄しますか？",
    conflict: "⚠️ 編集中にこのノートが別の場所で変更されました（AI や Obsidian）。",
    conflictHint: "− は今ディスクにある内容、＋ はあなたの編集です。",
    reload: "ディスクの内容を読み込む（自分の編集は破棄）", overwrite: "自分の編集で上書き保存", back: "編集に戻る",
  } : {
    save: "💾 Save", cancel: "Cancel", log: "Also record in the work log", edit: "Edit", preview: "Preview",
    saved: "Saved", leave: "You have unsaved changes. Discard them?",
    conflict: "⚠️ This note changed elsewhere while you were editing (an agent or Obsidian).",
    conflictHint: "− is what is on disk now, + is your edit.",
    reload: "Load the version on disk (discard my edit)", overwrite: "Overwrite with my edit", back: "Back to editing",
  };

  // A line diff (longest common subsequence); notes are small, and a huge one falls back
  // to showing both sides in full.
  function diff(before, after) {
    const a = before.split("\n"), b = after.split("\n");
    if (a.length * b.length > 4_000_000) return [...a.map((t) => ["-", t]), ...b.map((t) => ["+", t])];
    const n = a.length, m = b.length;
    const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i -= 1) for (let j = m - 1; j >= 0; j -= 1)
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { out.push([" ", a[i]]); i += 1; j += 1; }
      else if (lcs[i + 1][j] >= lcs[i][j + 1]) { out.push(["-", a[i]]); i += 1; }
      else { out.push(["+", b[j]]); j += 1; }
    }
    while (i < n) out.push(["-", a[i++]]);
    while (j < m) out.push(["+", b[j++]]);
    return out;
  }

  // Changed lines with two lines of context; long unchanged runs fold to "…".
  function diffNode(before, after) {
    const lines = diff(before, after);
    const keep = new Set();
    lines.forEach(([k], i) => { if (k !== " ") for (let d = -2; d <= 2; d += 1) keep.add(i + d); });
    const rows = [];
    let skipped = false;
    lines.forEach(([k, t], i) => {
      if (!keep.has(i)) { if (!skipped) rows.push(h("div", { class: "dim note-diff-skip", text: "…" })); skipped = true; return; }
      skipped = false;
      rows.push(h("div", { class: `note-diff-line ${k === "+" ? "add" : k === "-" ? "del" : ""}`, text: `${k} ${t}` }));
    });
    return h("div", { class: "note-diff" }, ...rows);
  }

  /** Replace *host*'s content with an editor for *note*; *render* turns Markdown into nodes. */
  function start(note, host, { render, done }) {
    const l = L();
    let revision = note.revision;
    let saving = false;
    const original = note.text;
    const area = h("textarea", { class: "note-edit-text", spellcheck: "false", "aria-label": l.edit });
    area.value = note.text;
    const preview = h("div", { class: "note-edit-preview note-content" });
    const draw = () => preview.replaceChildren(render(area.value));
    let timer = 0;
    area.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(draw, 250); });
    const log = h("input", { type: "checkbox", class: "note-edit-log" });
    const split = h("div", { class: "note-edit-split show-edit" }, area, preview);
    const tab = (mode, label) => h("button", { type: "button", class: "chip note-edit-tab", "data-mode": mode, text: label,
      onclick: () => { split.className = `note-edit-split show-${mode}`; if (mode === "preview") draw(); } });
    const dirty = () => area.value !== original;

    async function save(force = false) {
      if (saving) return;
      saving = true;
      try {
        const saved = await api("/workspace/note", { method: "PUT",
          body: { path: note.path, revision, text: area.value, log: log.checked } });
        toast(l.saved);
        window.removeEventListener("beforeunload", guard);
        done(saved);
      } catch (err) {
        if (err.status === 409 && err.data && err.data.current) conflict(err.data.current, force);
        else toast(err.message);
      } finally {
        saving = false;
      }
    }

    function conflict(current) {
      const panel = h("div", { class: "note-conflict" },
        h("p", { text: l.conflict }), h("p", { class: "dim", text: l.conflictHint }),
        diffNode(current.text, area.value),
        h("div", { class: "note-edit-bar" },
          h("button", { type: "button", class: "btn", text: l.reload, onclick: () => {
            window.removeEventListener("beforeunload", guard);
            done(current);
          } }),
          h("button", { type: "button", class: "btn primary", text: l.overwrite, onclick: () => {
            revision = current.revision; panel.remove(); save(true);
          } }),
          h("button", { type: "button", class: "btn ghost", text: l.back, onclick: () => panel.remove() })));
      host.prepend(panel);
      panel.scrollIntoView({ block: "start" });
    }

    const guard = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    area.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
    });
    const cancel = () => {
      if (dirty() && !confirm(l.leave)) return;
      window.removeEventListener("beforeunload", guard);
      done(null);
    };
    host.replaceChildren(
      h("div", { class: "note-edit-bar" },
        h("button", { type: "button", class: "btn primary", text: l.save, onclick: () => save() }),
        h("button", { type: "button", class: "btn", text: l.cancel, onclick: cancel }),
        h("label", { class: "note-edit-logl" }, log, ` ${l.log}`),
        h("span", { class: "note-edit-tabs" }, tab("edit", l.edit), tab("preview", l.preview))),
      split);
    draw();
    area.focus();
    return { dirty, cancel };
  }

  return { start, diff };
})();
