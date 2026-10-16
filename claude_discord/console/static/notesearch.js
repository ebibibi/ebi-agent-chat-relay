// Find a note: Ctrl+O (⌘O) searches note names, Ctrl+Shift+F (⌘⇧F) searches inside notes.
// ↑/↓ move, Enter opens in the note viewer, Tab switches between the two modes, Escape closes.
// Also reachable from the 📝 button in the navigation (phones have no shortcuts).
// Excluded notes never appear: the server leaves them out of both searches.
"use strict";

window.ConsoleNoteSearch = (() => {
  let box = null;
  let mode = "name";
  let timer = 0;
  let seq = 0;
  let results = [];
  let active = 0;
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    name: "ノート名", text: "本文", ph_name: "ノート名で探す（Tab で本文検索）", ph_text: "ノートの本文を検索（Tab で名前検索）",
    none: "見つかりません", hint: "↑↓ で選択・Enter で開く・Esc で閉じる", nav: "📝 ノートを探す",
  } : {
    name: "Names", text: "Text", ph_name: "Find a note by name (Tab: search text)", ph_text: "Search inside notes (Tab: names)",
    none: "Nothing found", hint: "↑↓ select · Enter open · Esc close", nav: "📝 Find a note",
  };
  const available = () => Boolean(window.ConsoleWorkspace?.available());

  function close() { if (box) { box.remove(); box = null; } }

  function draw() {
    const list = box.querySelector(".nsearch-list");
    const l = L();
    list.replaceChildren(...(results.length ? results.map((r, i) => h("li", {
      class: "nsearch-item" + (i === active ? " on" : ""), role: "option", "aria-selected": i === active ? "true" : "false",
      onmousedown: (e) => { e.preventDefault(); choose(i); },
    }, h("div", { class: "nsearch-name", text: r.path.split("/").pop().replace(/\.md$/, "") }),
      h("div", { class: "dim nsearch-path", text: r.path }),
      r.snippet ? h("div", { class: "nsearch-snippet", text: r.snippet }) : null))
      : [h("li", { class: "dim nsearch-empty", text: box.querySelector("input").value.trim() ? l.none : l.hint })]));
    list.querySelector(".on")?.scrollIntoView({ block: "nearest" });
    for (const tab of box.querySelectorAll(".nsearch-tab")) tab.classList.toggle("on", tab.dataset.mode === mode);
  }

  async function run() {
    const q = box.querySelector("input").value.trim();
    const mine = ++seq;
    if (!q && mode === "text") { results = []; draw(); return; }
    try {
      const data = mode === "name"
        ? await api(`/workspace/notes?q=${encodeURIComponent(q)}`)
        : await api(`/workspace/search?q=${encodeURIComponent(q)}`);
      if (mine !== seq || !box) return;
      results = mode === "name" ? data.notes.map((path) => ({ path })) : data.hits;
      active = 0;
      draw();
    } catch (err) { toast(err.message); }
  }

  function choose(i) {
    const hit = results[i];
    if (!hit) return;
    close();
    window.ConsoleNotes.open(hit.path);
  }

  function setMode(next) {
    mode = next;
    const input = box.querySelector("input");
    input.placeholder = mode === "name" ? L().ph_name : L().ph_text;
    run();
    input.focus();
  }

  function open(nextMode = "name") {
    if (!available()) return;
    close();
    mode = nextMode;
    results = [];
    const l = L();
    const input = h("input", { type: "search", class: "nsearch-input", "aria-label": l.ph_name,
      placeholder: mode === "name" ? l.ph_name : l.ph_text, autocomplete: "off" });
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, mode === "name" ? 120 : 300); });
    input.addEventListener("keydown", (e) => {
      if (e.isComposing) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, results.length - 1); draw(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); draw(); }
      else if (e.key === "Enter") { e.preventDefault(); choose(active); }
      else if (e.key === "Tab") { e.preventDefault(); setMode(mode === "name" ? "text" : "name"); }
    });
    const tab = (m, label) => h("button", { type: "button", class: "chip nsearch-tab", "data-mode": m,
      onclick: () => setMode(m), text: label });
    box = h("div", { class: "nsearch", role: "dialog", "aria-label": l.nav,
      onmousedown: (e) => { if (e.target === box) close(); } },
      h("div", { class: "nsearch-shell" },
        h("div", { class: "nsearch-tabs" }, tab("name", l.name), tab("text", l.text)),
        input, h("ul", { class: "nsearch-list", role: "listbox" })));
    document.body.append(box);
    draw();
    input.focus();
    run();
  }

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.isComposing || !available()) return;
    const key = e.key.toLowerCase();
    if (key === "o" && !e.shiftKey) { e.preventDefault(); open("name"); }
    else if (key === "f" && e.shiftKey) { e.preventDefault(); open("text"); }
  });

  function nav() {
    return available() ? h("button", { type: "button", class: "nav-notes", text: L().nav, onclick: () => open("name") }) : null;
  }

  return { open, close, nav };
})();
