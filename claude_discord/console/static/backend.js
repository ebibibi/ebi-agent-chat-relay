// Switch backend, model and effort with clicks. One picker serves two scopes: a conversation
// (opened from the "🤖 backend · model · effort" line above the reply box) and the default new
// conversations start with (opened from the nav). Each choice applies at once through the same
// validation as /backend, /model and /effort, and the picker redraws for what is now current —
// a new backend brings its own models and effort levels. Loaded before slash.js and app.js.
"use strict";

window.ConsoleBackend = (() => {
  const LABELS = { claude: "Claude", codex: "Codex", local: "Local", agui: "AG-UI", pi: "pi" };
  let panel = null;
  let defaults = null; // the default catalog, for the nav line

  function close() {
    if (!panel) return;
    panel.remove();
    panel = null;
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("click", onOutside, true);
  }
  function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
  function onOutside(e) { if (panel && !panel.contains(e.target)) close(); }

  const summary = (c) => [LABELS[c.backend] || c.backend, c.model, c.effort].filter(Boolean).join(" · ");

  // *scope*: { title, load() -> catalog, apply(name, arg) -> { message, catalog }, onChange(catalog) }
  async function open(scope, anchor) {
    close();
    panel = h("div", { class: "backend-picker", role: "dialog", "aria-label": scope.title });
    document.body.append(panel);
    const r = anchor.getBoundingClientRect();
    const place = () => {
      const top = r.bottom + 6 + panel.offsetHeight > window.innerHeight ? r.top - panel.offsetHeight - 6 : r.bottom + 6;
      panel.style.top = `${Math.max(8, top)}px`;
      panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - panel.offsetWidth - 8))}px`;
    };
    panel.append(h("div", { class: "dim", text: "…" }));
    place();
    setTimeout(() => {
      document.addEventListener("keydown", onKey, true);
      document.addEventListener("click", onOutside, true);
    }, 0);
    let c;
    try { c = await scope.load(); } catch (err) { close(); toast(err.message); return; }
    const draw = () => {
      if (!panel) return;
      const chip = (on, text, onclick, title) => h("button", { type: "button", class: "chip" + (on ? " on" : ""),
        title, "aria-pressed": on ? "true" : "false", onclick, text });
      const choose = async (name, arg) => {
        try {
          const res = await scope.apply(name, arg);
          if (res && res.catalog) { c = res.catalog; scope.onChange && scope.onChange(c); }
          toast(res && res.message ? `✅ ${res.message}` : "✅");
          draw();
        } catch (err) { toast(err.message); }
      };
      const custom = h("input", { type: "text", class: "bp-custom", placeholder: T.bp_model_other, size: 14,
        onkeydown: (e) => { if (e.key === "Enter" && e.target.value.trim()) choose("model", e.target.value.trim()); } });
      const models = c.models || [];
      panel.replaceChildren(
        h("div", { class: "bp-head" }, h("strong", { text: scope.title }),
          h("button", { class: "icon-btn", type: "button", "aria-label": "close", onclick: close, text: "×" })),
        h("div", { class: "bp-label", text: T.bp_backend }),
        h("div", { class: "chips" }, (c.backends || []).map((b) =>
          chip(b === c.backend, LABELS[b] || b, () => b !== c.backend && choose("backend", b)))),
        h("div", { class: "bp-label", text: `${T.bp_model} (${LABELS[c.backend] || c.backend})` }),
        h("div", { class: "chips" }, models.map((m) =>
          chip(m.value === c.model, m.value, () => m.value !== c.model && choose("model", m.value), m.label || m.value))),
        custom,
        (c.efforts || []).length ? h("div", { class: "bp-label", text: T.bp_effort }) : null,
        (c.efforts || []).length ? h("div", { class: "chips" }, c.efforts.map((e) =>
          chip(e === c.effort, e, () => e !== c.effort && choose("effort", e)))) : null,
        h("p", { class: "dim bp-note", text: scope.note }));
      place();
    };
    draw();
  }

  // ------------------------------------------------------------ a conversation
  function forItem(id, onChange) {
    const path = `/items/${encodeURIComponent(id)}`;
    return {
      title: T.bp_title_thread,
      note: T.bp_note_thread,
      load: () => api(`${path}/commands`),
      apply: (name, arg) => api(`${path}/command`, { method: "POST", body: { name, arg } }),
      onChange,
    };
  }

  const teamText = () => document.documentElement.lang === "ja" ? {
    manager: "親・マネージャーのAI", child: "これから作る子のAI", global: "子のAI（既定値）",
    note: "今後このスレッドから作る子に適用します。作成済みの子の設定は変わりません。",
    globalNote: "親ごとの指定がない場合に使います。バックエンドのインストール・認証が必要です。",
  } : {
    manager: "Manager / this thread AI", child: "AI for future children", global: "Default child AI",
    note: "Applies to future children of this thread. Existing children keep their settings.",
    globalNote: "Used unless a manager has its own child settings. The backend must be installed and authenticated.",
  };

  function childScope(id, onChange) {
    const path = id ? `/items/${encodeURIComponent(id)}/children` : "/defaults/children";
    return { title: id ? teamText().child : teamText().global,
      note: id ? teamText().note : teamText().globalNote,
      load: () => api(path),
      apply: (name, arg) => api(path, {method: "POST", body: {name, arg}}), onChange };
  }

  function controls(item) {
    if (!item.thread_id) return null;
    const make = (cls, label, scope) => {
      const btn = h("button", {class: `btn ${cls}`, type: "button", text: label,
        onclick: e => { e.stopPropagation(); open(scope, btn); }});
      scope.onChange = c => { btn.textContent = `${label}: ${summary(c)}`; };
      scope.load().then(scope.onChange).catch(() => {});
      return btn;
    };
    return h("div", {class: "team-settings"},
      make("team-manager-ai", teamText().manager, forItem(item.id)),
      make("team-child-ai", teamText().child, childScope(item.id)));
  }

  function childNav() {
    const btn = h("button", {class: "nav-child-ai", type: "button", text: `🤖 ${teamText().global}`,
      onclick: e => {e.stopPropagation(); open(childScope(null), btn);} });
    return btn;
  }

  // ------------------------------------------------------------ the default
  const defaultScope = {
    get title() { return T.bp_title_default; },
    get note() { return T.bp_note_default; },
    load: async () => (defaults = await api("/defaults")),
    apply: (name, arg) => api("/defaults", { method: "POST", body: { name, arg } }),
    onChange: (c) => { defaults = c; renderNav(); },
  };

  async function loadDefaults() {
    try { defaults = await api("/defaults"); } catch { defaults = null; }
    renderNav();
  }

  // The nav line: "🤖 Default: Claude · opus" — click to change.
  function nav() {
    if (!defaults) return null;
    const btn = h("button", { class: "nav-backend", type: "button", title: T.bp_title_default,
      onclick: (e) => { e.stopPropagation(); open(defaultScope, btn); } },
      h("span", { text: `🤖 ${T.bp_default}: ${summary(defaults)}` }));
    return btn;
  }

  return { open, forItem, nav, childNav, controls, loadDefaults, summary };
})();
