// CLI sign-in: a chip in the usage strip for each agent CLI that is not signed in,
// and a dialog that walks through the CLI's own login (device code or pasted code).
// The relay never sees a credential; this page only shows the URL and code the CLI printed.
// Server data reaches the page through textContent only.
"use strict";

(() => {
  const POLL_MS = 60000;
  const ACTIVE_MS = 2000;
  const NAMES = { claude: "Claude", codex: "Codex" };
  const L = new Proxy({
    en: { signin: "Sign in", out: "not signed in", title: (n) => `Sign ${n} in`, starting: "Starting the CLI…",
          open: "1. Open this page and sign in", enter: "2. Enter this code there", paste: "2. Paste the code the page shows",
          submit: "Send", verifying: "Checking the code…", ok: "✅ Signed in", again: "Start again",
          cancel: "Cancel", close: "Close", copy: "Copy", copied: "Copied", waitcodex: "This closes by itself once you have entered the code.",
          expires: (m) => `expires in ${m} min`, failed: "Sign-in did not finish", none: "No CLI can be signed in here." },
    ja: { signin: "サインイン", out: "未ログイン", title: (n) => `${n} にサインイン`, starting: "CLI を起動しています…",
          open: "1. このページを開いてサインイン", enter: "2. そこでこのコードを入力", paste: "2. ページに出たコードを貼り付け",
          submit: "送信", verifying: "コードを確認しています…", ok: "✅ サインインしました", again: "やり直す",
          cancel: "中止", close: "閉じる", copy: "コピー", copied: "コピーしました", waitcodex: "コードを入力すると自動で閉じます。",
          expires: (m) => `あと ${m} 分で失効`, failed: "サインインが完了しませんでした", none: "ここでサインインできる CLI はありません。" },
  }, { get: (strings, key) => strings[document.documentElement.lang === "ja" ? "ja" : "en"][key] });

  let rows = [];
  let open = null; // backend whose dialog is showing
  let timer = null;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  async function call(path, body) {
    const headers = { Accept: "application/json" };
    const token = localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
    const init = { headers, credentials: "same-origin" };
    if (body !== undefined) {
      Object.assign(headers, { "X-Console-Request": "1", "Content-Type": "application/json" });
      Object.assign(init, { method: "POST", body: JSON.stringify(body) });
    }
    const res = await fetch(`/console/api/cli-login${path}`, init);
    let data = null;
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  const running = (flow) => flow && ["starting", "waiting", "verifying"].includes(flow.state);
  const row = (backend) => rows.find((r) => r.backend === backend);

  function schedule() {
    clearTimeout(timer);
    const busy = rows.some((r) => running(r.flow));
    timer = setTimeout(load, busy || open ? ACTIVE_MS : POLL_MS);
  }

  async function load() {
    try {
      const data = await call("");
      rows = (data && data.backends) || [];
    } catch { /* 401 is the board's to handle; offline keeps the last answer */ }
    window.dispatchEvent(new Event("console-cli-login-change"));
    if (open) renderDialog();
    schedule();
  }

  async function act(backend, body) {
    try {
      const data = await call(`/${backend}`, body);
      const r = row(backend);
      if (r && data) r.flow = data.flow;
    } catch (e) {
      const r = row(backend);
      if (r) r.flow = { ...(r.flow || {}), backend, state: "failed", message: e.message };
    }
    renderDialog();
    schedule();
  }

  // ---- the strip ------------------------------------------------------------
  // usage.js asks for these on every render, so they sit beside the usage blocks.
  function chips() {
    return rows.filter((r) => r.logged_in === false || running(r.flow)).map((r) => {
      const block = el("div", "u-be out cl-chip");
      block.append(el("span", "u-name", NAMES[r.backend] || r.backend));
      block.append(el("span", "u-out", running(r.flow) ? "…" : L.out));
      const btn = el("button", "cl-btn", `🔑 ${L.signin}`);
      btn.type = "button";
      btn.addEventListener("click", () => show(r.backend));
      block.append(btn);
      return block;
    });
  }
  // backend.js reads sign-in state for the subscription route in its picker.
  window.consoleCliLogin = { chips, show: (b) => show(b), state: (b) => row(b) || null };

  // ---- the dialog -----------------------------------------------------------
  const dialog = el("dialog", "cl-dialog");
  dialog.addEventListener("close", () => { open = null; schedule(); });
  document.addEventListener("DOMContentLoaded", () => document.body.append(dialog));
  if (document.body) document.body.append(dialog);

  function show(backend) {
    open = backend;
    const r = row(backend);
    if (!r || !running(r.flow)) act(backend, { action: "start" });
    renderDialog();
    if (!dialog.open) dialog.showModal();
  }

  function copyButton(text) {
    const btn = el("button", "cl-btn", L.copy);
    btn.type = "button";
    btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(text); btn.textContent = L.copied; } catch { /* no clipboard */ }
    });
    return btn;
  }

  function link(url) {
    const a = el("a", "cl-link", url);
    if (/^https:\/\//.test(url)) a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  function button(text, onClick, cls = "cl-btn") {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function renderDialog() {
    if (!open) return;
    const backend = open;
    const r = row(backend);
    const flow = r && r.flow;
    const body = [el("h3", "cl-title", L.title(NAMES[backend] || backend))];
    const actions = el("div", "cl-actions");
    if (!flow || flow.state === "starting") {
      body.push(el("p", "cl-dim", L.starting));
    } else if (flow.state === "waiting") {
      if (flow.url) body.push(el("p", "cl-step", L.open), el("p", "cl-url"));
      if (flow.url) body[body.length - 1].append(link(flow.url), copyButton(flow.url));
      if (backend === "codex" && flow.user_code) {
        const code = el("p", "cl-code");
        code.append(el("code", "", flow.user_code), copyButton(flow.user_code));
        body.push(el("p", "cl-step", L.enter), code, el("p", "cl-dim", L.waitcodex));
      }
      if (backend === "claude") {
        const form = el("form", "cl-form");
        const input = el("input", "cl-input");
        Object.assign(input, { type: "text", autocomplete: "off", spellcheck: false, required: true, maxLength: 512 });
        form.append(input, button(L.submit, () => form.requestSubmit(), "cl-btn primary"));
        form.addEventListener("submit", (ev) => {
          ev.preventDefault();
          if (input.value.trim()) act(backend, { action: "code", code: input.value.trim() });
        });
        body.push(el("p", "cl-step", L.paste), form);
      }
      if (flow.expires_at) {
        const mins = Math.max(0, Math.round((flow.expires_at - Date.now() / 1000) / 60));
        body.push(el("p", "cl-dim", L.expires(mins)));
      }
    } else if (flow.state === "verifying") {
      body.push(el("p", "cl-dim", L.verifying));
    } else if (flow.state === "succeeded") {
      body.push(el("p", "cl-ok", L.ok));
      if (flow.message) body.push(el("p", "cl-dim", flow.message));
    } else {
      body.push(el("p", "cl-err", L.failed));
      if (flow.message) body.push(el("p", "cl-dim", flow.message));
      actions.append(button(L.again, () => act(backend, { action: "start" }), "cl-btn primary"));
    }
    if (running(flow)) actions.append(button(L.cancel, () => act(backend, { action: "cancel" })));
    actions.append(button(L.close, () => dialog.close()));
    // Keep a half-typed code across polls: only rebuild when the shape changed.
    const shape = JSON.stringify([backend, flow && flow.state, flow && flow.url, flow && flow.user_code, flow && flow.message]);
    if (dialog.dataset.shape === shape) return;
    dialog.dataset.shape = shape;
    dialog.replaceChildren(...body, actions);
  }

  window.addEventListener("console-language-change", () => { dialog.dataset.shape = ""; renderDialog(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  load();
})();
