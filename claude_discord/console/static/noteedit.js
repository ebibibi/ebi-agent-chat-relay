// Edit a note in the viewer: Markdown source beside a live preview that scrolls with it
// (stacked with tabs on a phone). Ctrl+S / ⌘S saves. The save carries the revision the edit started from; if an
// agent or Obsidian changed the file meanwhile, the server refuses (409) and this shows what
// differs, then lets the human reload theirs away or overwrite on purpose.
// It starts as a plain textarea, which always works; CodeMirror 6 (vendored single file,
// loaded on the first edit only) then takes its place with Markdown highlighting, list
// continuation, search, [[wikilink]] completion and an Obsidian-style live preview. If it
// cannot load, the textarea stays.
// Loaded after notes.js, which calls start() and supplies the renderer.
"use strict";

window.ConsoleNoteEdit = (() => {
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    save: "💾 保存", cancel: "やめる", log: "作業ログにも記録", edit: "編集", preview: "プレビュー",
    toLive: "👁 ライブプレビュー", toSplit: "⇆ ソースとプレビュー",
    saved: "保存しました", leave: "保存していない変更があります。破棄しますか？",
    conflict: "⚠️ 編集中にこのノートが別の場所で変更されました（AI や Obsidian）。",
    conflictHint: "− は今ディスクにある内容、＋ はあなたの編集です。",
    reload: "ディスクの内容を読み込む（自分の編集は破棄）", overwrite: "自分の編集で上書き保存", back: "編集に戻る",
  } : {
    save: "💾 Save", cancel: "Cancel", log: "Also record in the work log", edit: "Edit", preview: "Preview",
    toLive: "👁 Live preview", toSplit: "⇆ Source and preview",
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

  let library = null;
  function loadLibrary() {
    library ||= new Promise((resolve, reject) => {
      if (window.ConsoleCodeMirror) { resolve(window.ConsoleCodeMirror); return; }
      const s = document.createElement("script");
      s.src = "/static/vendor-codemirror.min.js";
      s.onload = () => (window.ConsoleCodeMirror ? resolve(window.ConsoleCodeMirror) : reject(new Error("no editor")));
      s.onerror = () => { library = null; reject(new Error("editor did not load")); };
      document.head.append(s);
    });
    return library;
  }
  // Note names for [[ completion, from the same index the quick switcher uses.
  async function linkSource(query) {
    try {
      const data = await api(`/workspace/notes?q=${encodeURIComponent(query)}`);
      return data.notes.slice(0, 30).map((p) => p.split("/").pop().replace(/\.md$/, ""));
    } catch { return []; }
  }
  // Swap the textarea for CodeMirror, keeping whatever was typed meanwhile.
  async function upgrade(area, use, { onChange, onSave, onScroll, live, imageUrl }) {
    let lib;
    try { lib = await loadLibrary(); } catch { return; }
    if (!area.isConnected) return;
    const holder = h("div", { class: "note-edit-cm" });
    area.after(holder);
    const editor = lib.create(holder, { doc: area.value, onChange, onSave, onScroll, linkSource, live, imageUrl });
    area.hidden = true;
    use(editor);
    editor.focus();
  }

  // Scroll sync. The preview's blocks carry data-line (their first source line); between two
  // of them a position is interpolated, so a long paragraph scrolls smoothly too.
  function anchors(preview) {
    const base = preview.getBoundingClientRect().top - preview.scrollTop;
    const out = [{ line: 1, top: 0 }];
    for (const el of preview.querySelectorAll("[data-line]")) {
      const line = Number(el.dataset.line);
      const top = el.getBoundingClientRect().top - base;
      const last = out[out.length - 1];
      if (line > last.line && top >= last.top) out.push({ line, top });
    }
    return out;
  }
  // Map *value* from one key to the other ("line" ↔ "top") through the anchors.
  function interpolate(points, from, to, value) {
    let i = 0;
    while (i + 1 < points.length && points[i + 1][from] <= value) i += 1;
    const a = points[i], b = points[i + 1];
    if (!b) return a[to];
    return a[to] + (value - a[from]) / Math.max(1e-6, b[from] - a[from]) * (b[to] - a[to]);
  }
  function syncScroll(preview) {
    let source = null;
    let driven = null; // the side being scrolled by the other one: its next scroll events are echoes
    let timer = 0;
    const drive = (side, apply) => {
      driven = side;
      apply();
      clearTimeout(timer);
      timer = setTimeout(() => { driven = null; }, 120);
    };
    const fromEditor = () => {
      if (!source || driven === "editor") return;
      drive("preview", () => {
        if (!source.topLine) { // the plain textarea: by proportion
          const el = source.element;
          preview.scrollTop = el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight) * (preview.scrollHeight - preview.clientHeight);
        } else if (source.atEnd()) preview.scrollTop = preview.scrollHeight;
        else preview.scrollTop = interpolate(anchors(preview), "line", "top", source.topLine());
      });
    };
    const fromPreview = () => {
      if (!source || driven === "preview") return;
      drive("editor", () => {
        const end = preview.scrollTop + preview.clientHeight >= preview.scrollHeight - 2;
        if (!source.topLine) {
          const el = source.element;
          el.scrollTop = preview.scrollTop / Math.max(1, preview.scrollHeight - preview.clientHeight) * (el.scrollHeight - el.clientHeight);
        } else if (end && preview.scrollTop > 0) source.scrollToEnd();
        else source.scrollToLine(interpolate(anchors(preview), "top", "line", preview.scrollTop));
      });
    };
    preview.addEventListener("scroll", fromPreview, { passive: true });
    return { use: (next) => { source = next; }, fromEditor };
  }

  const MODE_KEY = "ccdb.noteEditMode";

  /** Replace *host*'s content with an editor for *note*; *render* turns Markdown into nodes. */
  function start(note, host, { render, imageUrl, done }) {
    const l = L();
    let revision = note.revision;
    let saving = false;
    const original = note.text;
    const area = h("textarea", { class: "note-edit-text", spellcheck: "false", "aria-label": l.edit });
    area.value = note.text;
    const preview = h("div", { class: "note-edit-preview note-content" });
    // What the editor holds: the textarea until CodeMirror replaces it.
    let source = { get value() { return area.value; }, focus: () => area.focus(), element: area };
    const sync = syncScroll(preview);
    sync.use(source);
    area.addEventListener("scroll", sync.fromEditor, { passive: true });
    // Re-rendering resets the preview's layout, so it is aligned with the editor again.
    const draw = () => { if (living()) return; preview.replaceChildren(render(source.value)); sync.fromEditor(); };
    let timer = 0;
    area.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(draw, 250); });
    const log = h("input", { type: "checkbox", class: "note-edit-log" });
    const split = h("div", { class: "note-edit-split show-edit" }, area, preview);
    const tab = (mode, label) => h("button", { type: "button", class: "chip note-edit-tab", "data-mode": mode, text: label,
      onclick: () => { split.className = `note-edit-split show-${mode}`; if (mode === "preview") draw(); } });
    const dirty = () => source.value !== original;
    // Live preview (CodeMirror only) draws the note in the editor itself, so the preview pane
    // goes away; the choice is remembered. The textarea fallback always shows both panes.
    let live = localStorage.getItem(MODE_KEY) !== "split";
    const living = () => live && Boolean(source.setLive);
    const modeBtn = h("button", { type: "button", class: "chip note-edit-mode", hidden: true, onclick: () => {
      live = !live;
      localStorage.setItem(MODE_KEY, live ? "live" : "split");
      showMode();
      draw();
      source.focus();
    } });
    const showMode = () => {
      modeBtn.textContent = live ? l.toSplit : l.toLive;
      host.classList.toggle("note-edit-live", living());
      if (source.setLive) source.setLive(live);
    };

    async function save(force = false) {
      if (saving) return;
      saving = true;
      try {
        const saved = await api("/workspace/note", { method: "PUT",
          body: { path: note.path, revision, text: source.value, log: log.checked } });
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
        diffNode(current.text, source.value),
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
        modeBtn,
        h("span", { class: "note-edit-tabs" }, tab("edit", l.edit), tab("preview", l.preview))),
      split);
    draw();
    area.focus();
    upgrade(area, (editor) => { source = editor; sync.use(editor); modeBtn.hidden = false; showMode(); }, {
      live,
      onChange: () => { clearTimeout(timer); timer = setTimeout(draw, 250); },
      onScroll: () => sync.fromEditor(),
      onSave: () => save(),
      imageUrl,
    });
    return { dirty, cancel };
  }

  return { start, diff };
})();
