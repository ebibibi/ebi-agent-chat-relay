// Slash commands in the reply box: "/" opens a list of commands and the current backend's
// built-ins and skills; after "/backend ", "/model " or "/effort " it lists the values valid right now.
// ↑↓ move, Tab or Enter completes, Esc closes. Sending a "/command" runs it instead of
// replying. Loaded before app.js, which calls attach() for each reply box.
"use strict";

window.ConsoleSlash = (() => {
  const catalogs = new Map(); // item id -> catalog from /items/{id}/commands
  const ARG_SOURCES = { backend: "backends", model: "models", effort: "efforts" };
  let box = null;
  let itemId = null;
  let meta = null;
  let menu = null;
  let options = [];
  let active = 0;

  async function load(id, force = false) {
    if (!force && catalogs.has(id)) return catalogs.get(id);
    try {
      const data = await api(`/items/${encodeURIComponent(id)}/commands`);
      catalogs.set(id, data);
      if (id === itemId) drawMeta();
      return data;
    } catch { return null; } // no settings in this deployment: the box stays a plain box
  }

  function drawMeta() {
    if (!meta || !meta.isConnected) return;
    const c = catalogs.get(itemId);
    meta.hidden = !c;
    if (!c) return;
    const parts = [c.backend, c.model, c.effort].filter(Boolean).join(" · ");
    const id = itemId;
    const now = h("button", { class: "slash-now", type: "button", title: T.bp_title_thread,
      onclick: (e) => {
        e.stopPropagation();
        window.ConsoleBackend.open(window.ConsoleBackend.forItem(id, (next) => {
          catalogs.set(id, next);
          if (id === itemId) drawMeta();
        }), now);
      }, text: `🤖 ${parts} ▾` });
    meta.replaceChildren(now, h("span", { class: "slash-hint", text: T.slash_hint }));
  }

  // What to offer for the text before the caret, or [] when it is not a command.
  function suggestions(text, c) {
    const m = /^\/([\w:.-]*)(\s+([\s\S]*))?$/.exec(text);
    if (!m || text.includes("\n")) return [];
    const [, name, spaced, rest] = m;
    const q = (spaced ? rest || "" : name).toLowerCase();
    if (!spaced) {
      const all = [
        ...[...c.commands, ...(c.builtins || [])].map((x) => ({ insert: `/${x.name} `, label: `/${x.name}`, hint: T.slash_desc[x.name] || x.description })),
        ...c.skills.map((s) => ({ insert: `/${s.name} `, label: `/${s.name}`, hint: s.description || "" })),
      ];
      return rank(all, q, (o) => o.label.slice(1));
    }
    const source = ARG_SOURCES[name];
    if (!source) return [];
    const values = c[source].map((v) => (typeof v === "string" ? { value: v, label: v } : v));
    const current = { backends: c.backend, models: c.model, efforts: c.effort }[source];
    return rank(values.map((v) => ({ insert: `/${name} ${v.value}`, label: v.value,
      hint: (v.value === current ? "✓ " : "") + (v.label !== v.value ? v.label : "") })), q, (o) => o.label);
  }

  // Prefix matches first, then substring matches; at most 12.
  function rank(list, q, key) {
    if (!q) return list.slice(0, 12);
    const pre = list.filter((o) => key(o).toLowerCase().startsWith(q));
    const sub = list.filter((o) => !key(o).toLowerCase().startsWith(q) && key(o).toLowerCase().includes(q));
    return [...pre, ...sub].slice(0, 12);
  }

  function close() { options = []; if (menu) menu.hidden = true; }

  function draw() {
    if (!menu) return;
    menu.hidden = !options.length;
    menu.replaceChildren(...options.map((o, i) => h("li", {
      class: "slash-opt" + (i === active ? " on" : ""), role: "option",
      onmousedown: (e) => { e.preventDefault(); pick(i); },
    }, h("span", { class: "slash-label", text: o.label }), h("span", { class: "slash-desc", text: o.hint }))));
    const on = menu.querySelector(".on");
    if (on) on.scrollIntoView({ block: "nearest" });
  }

  async function update() {
    const text = box.value.slice(0, box.selectionStart);
    if (!text.startsWith("/")) { close(); return; }
    const c = await load(itemId);
    if (!c || box.value.slice(0, box.selectionStart) !== text) return;
    options = suggestions(text, c);
    active = Math.min(active, Math.max(0, options.length - 1));
    draw();
  }

  function pick(i) {
    const o = options[i];
    if (!o) return;
    box.value = o.insert;
    box.setSelectionRange(box.value.length, box.value.length);
    box.dispatchEvent(new Event("input"));
    box.focus();
  }

  function attach(el, id, metaEl) {
    box = el;
    itemId = id;
    meta = metaEl;
    active = 0;
    menu = h("ul", { class: "slash-menu", role: "listbox", hidden: true });
    el.parentElement.insertBefore(menu, el);
    drawMeta();
    load(id);
    el.addEventListener("input", () => { active = 0; update(); });
    el.addEventListener("click", update);
    el.addEventListener("blur", () => setTimeout(close, 120));
    // Capture: the menu's keys win over the box's own Ctrl+Enter handling.
    el.addEventListener("keydown", (e) => {
      if (!options.length || e.isComposing) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
        draw();
      } else if ((e.key === "Tab" || e.key === "Enter") && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        e.preventDefault();
        e.stopImmediatePropagation();
        pick(active);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    }, true);
  }

  // Runs *text* as a command if it is one; true when handled (the caller does not reply).
  async function run(id, text) {
    const m = /^\/([\w:.-]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
    const c = m && await load(id);
    if (!m || !c) return false;
    const [, name, arg = ""] = m;
    const known = c.commands.some((x) => x.name === name) || c.skills.some((s) => s.name === name) ||
      (c.builtins || []).some((b) => b.name === name && b.handled);
    if (!known) return false; // Claude's built-ins (/compact) and pasted paths go out as text
    if (name === "compact") toast(T.compacting);
    const res = await api(`/items/${encodeURIComponent(id)}/command`, { method: "POST", body: { name, arg } });
    if (res && res.catalog) {
      catalogs.set(id, res.catalog);
      if (id === itemId) drawMeta();
    }
    toast(res && res.message ? `✅ ${res.message}` : T.sent);
    close();
    return true;
  }

  return { attach, run, refresh: (id) => load(id, true) };
})();
