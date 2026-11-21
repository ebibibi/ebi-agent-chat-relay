// Switch backend, model and effort with clicks. One picker serves two scopes: a conversation
// (opened from the "🤖 backend · model · effort" line above the reply box) and the default new
// conversations start with (opened from the nav). Each choice applies at once through the same
// validation as /backend, /model and /effort, and the picker redraws for what is now current —
// a new backend brings its own models and effort levels. Loaded before slash.js and app.js.
"use strict";

window.ConsoleBackend = (() => {
  const LABELS = { claude: "Claude", codex: "Codex", local: "Local", agui: "AG-UI", pi: "pi", antigravity: "Antigravity" };
  let panel = null;
  let defaults = null; // the default catalog, for the nav line
  let parentDefaults = null; // parent mode default and cap, for the nav line (#1489)

  function close() {
    if (!panel) return;
    panel.remove();
    panel = null;
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("click", onOutside, true);
  }
  function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
  function onOutside(e) { if (panel && !panel.contains(e.target)) close(); }

  // Which route a backend takes decides what a turn costs, so it is always named.
  const ICONS = { subscription: "💳", foundry: "☁️", bedrock: "☁️", vertex: "☁️", api: "🔑" };
  const route = (conn) => conn ? `${ICONS[conn.kind] || "🔌"} ${(T.bp_kind || {})[conn.kind] || conn.kind}` : null;
  const summary = (c) => [LABELS[c.backend] || c.backend, route(c.connection), c.model, c.effort].filter(Boolean).join(" · ");
  const signedOut = (backend) => {
    const s = window.consoleCliLogin && window.consoleCliLogin.state(backend);
    return Boolean(s && s.logged_in === false);
  };

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
      const conns = c.connections || [];
      const current = c.connection ? c.connection.id : null;
      const connChip = (k) => {
        const out = k.kind === "subscription" && signedOut(c.backend);
        const text = `${ICONS[k.kind] || "🔌"} ${k.label}` + (out ? ` · 🔑 ${T.bp_signed_out}` : "");
        const title = [k.detail, k.default ? T.bp_default_route : null].filter(Boolean).join(" · ") || k.label;
        return chip(k.id === current, text, async () => {
          if (k.id !== current) await choose("connection", k.id);
          if (out && window.consoleCliLogin) window.consoleCliLogin.show(c.backend);
        }, title);
      };
      panel.replaceChildren(
        h("div", { class: "bp-head" }, h("strong", { text: scope.title }),
          h("button", { class: "icon-btn", type: "button", "aria-label": "close", onclick: close, text: "×" })),
        h("div", { class: "bp-label", text: T.bp_backend }),
        h("div", { class: "chips" }, (c.backends || []).map((b) =>
          chip(b === c.backend, LABELS[b] || b, () => b !== c.backend && choose("backend", b)))),
        conns.length ? h("div", { class: "bp-label", text: `${T.bp_connection} (${LABELS[c.backend] || c.backend})` }) : null,
        conns.length ? h("div", { class: "chips bp-conns" }, conns.map(connChip)) : null,
        h("div", { class: "bp-label", text: `${T.bp_model} (${[LABELS[c.backend] || c.backend, route(c.connection)].filter(Boolean).join(" · ")})` }),
        conns.length && !models.length ? h("p", { class: "dim bp-note", text: T.bp_no_models }) : null,
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

  // ------------------------------------------------------------ parent mode (#1489)
  // A thread in parent mode splits its work into child threads and reports once; the human
  // talks only to it. Per thread: on / off / follow the default. Children never follow the
  // default: they stay workers.
  const parentText = () => document.documentElement.lang === "ja" ? {
    label: "👪 親子モード", on: "オン", off: "オフ", follow: (on) => `既定に従う（${on ? "オン" : "オフ"}）`,
    child: "子スレッドなので既定ではオフ",
    title: "オンにすると、このスレッドが親として作業を子スレッドに分け、子の進捗・CI・失敗を追い、まとめて1回報告します",
    nav: (on, n) => `👪 親子モード既定: ${on ? "オン" : "オフ"}（子は最大${n}）`,
    navTitle: "新しく話しかけるスレッドの既定。オンにすると、言わなくても親として子スレッドに仕事を分けます",
    max: "未完了の子の上限", saved: "親子モードを変更しました",
  } : {
    label: "👪 Parent mode", on: "On", off: "Off", follow: (on) => `Default (${on ? "on" : "off"})`,
    child: "A child thread: off unless set",
    title: "On: this thread splits work into child threads, follows their progress, CI and failures, and reports once",
    nav: (on, n) => `👪 Parent mode default: ${on ? "on" : "off"} (max ${n} children)`,
    navTitle: "The default for threads you talk to. On: they delegate to child threads without being told",
    max: "Max unfinished children", saved: "Parent mode updated",
  };

  function parentModeControl(item) {
    const path = `/items/${encodeURIComponent(item.id)}/parent-mode`;
    const t = parentText();
    const select = h("select", { class: "team-parent-mode", "aria-label": t.label, title: t.title, disabled: true });
    const draw = (s) => {
      select.replaceChildren(
        h("option", { value: "default", text: s.is_child ? t.child : t.follow(s.default_enabled) }),
        h("option", { value: "on", text: t.on }),
        h("option", { value: "off", text: t.off }));
      select.value = s.mode;
      select.disabled = false;
      wrap.classList.toggle("on", Boolean(s.enabled));
    };
    select.onchange = async () => {
      try { draw(await api(path, { method: "POST", body: { mode: select.value } })); toast(`✅ ${t.saved}`); }
      catch (err) { toast(err.message); api(path).then(draw).catch(() => {}); }
    };
    const wrap = h("label", { class: "team-parent", title: t.title }, h("span", { text: t.label }), select);
    api(path).then(draw).catch(() => { wrap.hidden = true; });
    return wrap;
  }

  function parentNav() {
    const t = parentText();
    if (!parentDefaults) return null;
    let s = parentDefaults;
    const btn = h("button", { class: "nav-parent-mode" + (s.enabled ? " on" : ""), type: "button", title: t.navTitle,
      text: t.nav(s.enabled, s.max_children) });
    const save = async (body) => {
      try {
        s = parentDefaults = await api("/defaults/parent-mode", { method: "POST", body });
        btn.textContent = t.nav(s.enabled, s.max_children);
        btn.classList.toggle("on", s.enabled);
        toast(`✅ ${t.saved}`);
      } catch (err) { toast(err.message); }
    };
    btn.onclick = (e) => {
      e.stopPropagation();
      if (!s) return;
      close();
      panel = h("div", { class: "backend-picker", role: "dialog", "aria-label": t.label });
      const max = h("input", { type: "number", min: 1, max: s.max_children_limit || 50, value: s.max_children, class: "bp-custom", size: 4,
        onchange: (ev) => { const n = Number(ev.target.value); if (Number.isInteger(n) && n !== s.max_children) save({ max_children: n }); } });
      const chip = (on) => h("button", { type: "button", class: "chip" + (s.enabled === on ? " on" : ""), "aria-pressed": s.enabled === on ? "true" : "false",
        text: on ? t.on : t.off, onclick: async () => { await save({ enabled: on }); close(); } });
      panel.append(
        h("div", { class: "bp-head" }, h("strong", { text: t.label }),
          h("button", { class: "icon-btn", type: "button", "aria-label": "close", onclick: close, text: "×" })),
        h("p", { class: "dim bp-note", text: t.navTitle }),
        h("div", { class: "chips" }, chip(true), chip(false)),
        h("div", { class: "bp-label", text: t.max }), max);
      document.body.append(panel);
      const r = btn.getBoundingClientRect();
      panel.style.top = `${Math.max(8, Math.min(r.bottom + 6, window.innerHeight - panel.offsetHeight - 8))}px`;
      panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - panel.offsetWidth - 8))}px`;
      setTimeout(() => {
        document.addEventListener("keydown", onKey, true);
        document.addEventListener("click", onOutside, true);
      }, 0);
    };
    return btn;
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
      make("team-child-ai", teamText().child, childScope(item.id)),
      parentModeControl(item));
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
    // A deployment without the settings store has no parent mode: the nav simply omits it.
    try { parentDefaults = await api("/defaults/parent-mode"); } catch { parentDefaults = null; }
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

  return { open, forItem, nav, childNav, parentNav, controls, loadDefaults, summary };
})();
