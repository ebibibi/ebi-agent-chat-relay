// The screen AI (canvas mode and its focus session, #1401): an AI chosen for what is sent from
// these screens only. It rides on each send as `ai` and is never stored — not in the browser
// (no localStorage: closing the tab forgets it), not in any thread's or the global settings.
// The thread's own AI comes back for anything this screen did not send: scheduled tasks, a
// wait's automatic resume, child threads. While the canvas is open the choice is also sent to
// the relay every poll (POST /canvas/screen-ai, kept in memory for 90 s) so the secretary's own
// wakes use it; leaving the canvas withdraws it.
// Loaded before canvas.js, session.js and app.js.
"use strict";

window.ConsoleScreenAI = (() => {
  const STRINGS = {
    en: {
      label: "⚡ This screen's AI", same: "each thread's own", backend: "Backend", connection: "Connection",
      model: "Model", effort: "Effort", keep: "(thread's own)", reset: "↺ Default",
      help: "Applies only to what you send from this screen, one turn at a time, and to the secretary's own wakes while the screen is open. Nothing is saved: each thread's settings, scheduled tasks, child threads and an automatic resume after a wait (CI and the like) keep their own AI. Leaving the screen clears it.",
    },
    ja: {
      label: "⚡ この画面のAI", same: "各スレッドの設定のまま", backend: "バックエンド", connection: "接続",
      model: "モデル", effort: "思考の深さ", keep: "（スレッドの設定）", reset: "↺ 既定に戻す",
      help: "この画面から送った返信・ボタン回答のターンと、画面を開いている間の秘書の起動だけに効きます。保存はしません。各スレッドの設定・定期タスク・子スレッド、送信後の自動再開（CI待ち明けなど）はスレッドの元の設定に戻ります。画面を抜けると消えます。",
    },
  };
  const S = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];

  let choice = null; // { backend, connection, model, effort } — memory only
  let catalog = null; // for choice.backend (or the default backend)
  let defaults = null;
  let loading = false;

  const active = () => (window.ConsoleCanvas && window.ConsoleCanvas.on()) ||
    (window.ConsoleSession && window.ConsoleSession.isOpen());

  async function load(backend) {
    if (loading) return;
    loading = true;
    try {
      const data = await api(`/canvas/ai-catalog?backend=${encodeURIComponent(backend)}`);
      catalog = { backend, ...data };
      if (!defaults && data.default) defaults = data.default;
    } catch (err) { console.warn("screen AI: catalog", err); }
    finally { loading = false; }
    window.ConsoleCanvas && window.ConsoleCanvas.paint();
  }

  // What every send from this screen carries.
  function body() {
    return active() && choice ? { ai: choice } : {};
  }

  // Sent on every canvas poll: the secretary's wakes use it only while the screen is open.
  function heartbeat() {
    api("/canvas/screen-ai", { method: "POST", body: { ai: active() ? choice : null } })
      .catch((err) => console.warn("screen AI: heartbeat", err));
  }

  function clear() {
    const had = choice !== null;
    choice = null;
    if (had) api("/canvas/screen-ai", { method: "POST", body: { ai: null } }).catch(() => {});
  }

  function set(next) {
    choice = next && next.backend ? next : null;
    heartbeat();
    if (choice && (!catalog || catalog.backend !== choice.backend)) load(choice.backend);
    window.ConsoleCanvas && window.ConsoleCanvas.paint();
    if (window.ConsoleSession && window.ConsoleSession.isOpen()) window.ConsoleSession.repaint();
  }

  function select(name, options, value, onchange, keepLabel) {
    const s = h("select", { class: "sai-select", "aria-label": name, onchange: (e) => onchange(e.target.value || null) },
      keepLabel ? h("option", { value: "", text: keepLabel }) : null,
      ...options.map((o) => h("option", { value: o.value, text: o.label || o.value })));
    s.value = value || "";
    return s;
  }

  // The bar shown at the top of the canvas and of the focus session.
  function bar() {
    const s = S();
    const base = (defaults && defaults.backend) || "claude";
    if (!catalog) { load((choice && choice.backend) || base); }
    const backend = (choice && choice.backend) || base;
    const cat = catalog && catalog.backend === backend ? catalog : null;
    const custom = Boolean(choice);
    const backends = ((cat && cat.backends) || [base]).map((b) => ({ value: b }));
    const parts = [
      h("span", { class: "sai-label", text: s.label }),
      select(s.backend, backends, custom ? choice.backend : "", (v) => set(v ? { backend: v } : null), s.same),
    ];
    if (custom && cat) {
      const update = (field) => (v) => set({ ...choice, [field]: v });
      if ((cat.connections || []).length > 1) parts.push(select(s.connection,
        cat.connections.map((c) => ({ value: c.id, label: c.label })), choice.connection, update("connection"), s.keep));
      parts.push(select(s.model, cat.models || [], choice.model, update("model"), s.keep));
      if ((cat.efforts || []).length) parts.push(select(s.effort, cat.efforts.map((e) => ({ value: e })),
        choice.effort, update("effort"), s.keep));
      parts.push(h("button", { type: "button", class: "chip", onclick: () => set(null), text: s.reset }));
    } else if (!custom && defaults) {
      parts.push(h("span", { class: "dim sai-default",
        text: [defaults.backend, defaults.model, defaults.effort].filter(Boolean).join(" · ") }));
    }
    parts.push(h("span", { class: "sai-help", tabindex: "0", role: "note", title: s.help, "aria-label": s.help, text: "?" }));
    return h("div", { class: "sai-bar" + (custom ? " custom" : "") }, ...parts);
  }

  return { bar, body, heartbeat, clear, choice: () => choice };
})();
