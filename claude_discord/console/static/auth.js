// Sign-in for the Relay Console: passkeys first, a token only when the server offers one.
// Loaded before app.js, which calls ConsoleAuth.showLogin() on a 401. Server data reaches
// the page through textContent only.
"use strict";

(() => {
  const AUTH = "/console/api/auth";
  const lang = (localStorage.getItem("lang")
    || ((navigator.languages || [navigator.language || "en"]).some((l) => l.toLowerCase().startsWith("ja")) ? "ja" : "en"));
  const L = {
    en: {
      title: "Sign in to Relay Console", signin: "Sign in with a passkey",
      setup: "First time here: enter the setup code from the ccdb log, then create a passkey on this device.",
      code: "Setup code", name: "Name for this device", create: "Create passkey",
      newcode: "Write a new setup code to the log", logged: "A new setup code is in the ccdb log",
      other: "Add this device with a code", token: "Use a token instead", tokenph: "Console token",
      unsupported: "This browser cannot use passkeys here. Open the console over https or on localhost.",
      devices: "Passkeys", invite: "Add a device", invited: (c) => `On the new device, open the console and enter: ${c} (valid 15 minutes, single use)`,
      remove: "Remove", signout: "Sign out", lastused: "last used", never: "never used", close: "Close",
      cancelled: "Cancelled", sso: (n) => `Sign in with ${n}`, or: "or",
    },
    ja: {
      title: "Relay Console にサインイン", signin: "パスキーでサインイン",
      setup: "はじめての設定: ccdb のログに出ているセットアップコードを入れて、この端末にパスキーを作ります。",
      code: "セットアップコード", name: "この端末の名前", create: "パスキーを作成",
      newcode: "新しいセットアップコードをログに出す", logged: "ccdb のログに新しいセットアップコードを出しました",
      other: "コードでこの端末を追加", token: "トークンで入る", tokenph: "コンソールのトークン",
      unsupported: "このブラウザーではここでパスキーを使えません。https か localhost で開いてください。",
      devices: "パスキー", invite: "端末を追加", invited: (c) => `新しい端末でコンソールを開いて、このコードを入れてください: ${c}（15分間・1回限り）`,
      remove: "削除", signout: "サインアウト", lastused: "最終利用", never: "未使用", close: "閉じる",
      cancelled: "キャンセルしました", sso: (n) => `${n} でサインイン`, or: "または",
    },
  }[lang === "ja" ? "ja" : "en"];

  // ------------------------------------------------------------ base64url <-> bytes
  const toBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
  const toB64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const ids = (list) => (list || []).map((c) => ({ ...c, id: toBytes(c.id) }));

  function creationOptions(o) {
    return { ...o, challenge: toBytes(o.challenge), user: { ...o.user, id: toBytes(o.user.id) },
      excludeCredentials: ids(o.excludeCredentials) };
  }
  function requestOptions(o) {
    return { ...o, challenge: toBytes(o.challenge), allowCredentials: ids(o.allowCredentials) };
  }
  function credentialJSON(cred) {
    const r = cred.response;
    const response = { clientDataJSON: toB64(r.clientDataJSON) };
    if (r.attestationObject) {
      response.attestationObject = toB64(r.attestationObject);
      if (r.getTransports) response.transports = r.getTransports();
    } else {
      response.authenticatorData = toB64(r.authenticatorData);
      response.signature = toB64(r.signature);
      if (r.userHandle) response.userHandle = toB64(r.userHandle);
    }
    return { id: cred.id, rawId: toB64(cred.rawId), type: cred.type, response,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
  }

  async function call(path, body, method = "POST") {
    const headers = { Accept: "application/json" };
    if (method !== "GET") { headers["X-Console-Request"] = "1"; headers["Content-Type"] = "application/json"; }
    const res = await fetch(path, { method, headers, credentials: "same-origin",
      body: method === "GET" ? undefined : JSON.stringify(body || {}) });
    let data = null;
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  const supported = () => !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);

  async function signIn() {
    const { ticket, options } = await call(`${AUTH}/passkey/login/options`);
    const cred = await navigator.credentials.get({ publicKey: requestOptions(options) });
    await call(`${AUTH}/passkey/login/verify`, { ticket, credential: credentialJSON(cred) });
  }

  async function register(code, name) {
    const { ticket, options } = await call(`${AUTH}/passkey/register/options`, { code });
    const cred = await navigator.credentials.create({ publicKey: creationOptions(options) });
    await call(`${AUTH}/passkey/register/verify`, { ticket, credential: credentialJSON(cred), name });
  }

  const defaultName = () => {
    const ua = navigator.userAgent;
    const os = /iPhone|iPad/.test(ua) ? "iPhone/iPad" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac"
      : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "device";
    return `${os} · ${new Date().toISOString().slice(0, 10)}`;
  };

  // ------------------------------------------------------------ dom
  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
    return e;
  }
  const overlay = () => document.getElementById("help");
  function open(mode, card) {
    const o = overlay();
    o.dataset.mode = mode;
    o.hidden = false;
    o.replaceChildren(card);
  }
  function close() {
    const o = overlay();
    o.hidden = true;
    o.dataset.mode = "";
  }
  const errorLine = () => el("p", { class: "auth-error", role: "alert" });
  const fail = (line, err) => {
    line.textContent = err && err.name === "NotAllowedError" ? L.cancelled : String((err && err.message) || err);
  };

  // ------------------------------------------------------------ sign-in card
  let showing = false;
  async function showLogin(onDone) {
    if (showing) return;
    showing = true;
    let status = { methods: { passkey: true, token: false }, needs_setup: false };
    try { status = await call(`${AUTH}/status`, null, "GET"); } catch { /* fall back to the defaults */ }
    const done = () => { showing = false; close(); onDone(); };
    // A passkey session replaces any token this browser kept from before.
    const signedIn = () => { localStorage.removeItem("token"); done(); };
    const err = errorLine();
    const parts = [el("h2", { text: L.title })];
    // The OIDC callback comes back here with ?signin_error=… when it refused.
    const params = new URLSearchParams(location.search);
    if (params.has("signin_error")) {
      err.textContent = params.get("signin_error");
      params.delete("signin_error");
      history.replaceState(null, "", location.pathname + (params.size ? `?${params}` : "") + location.hash);
    }
    if (status.methods.oidc) {
      parts.push(el("a", { class: "btn primary big", href: `${AUTH}/oidc/start`, text: L.sso(status.methods.oidc) }));
      if (status.methods.passkey) parts.push(el("p", { class: "auth-or", text: L.or }));
    }

    if (status.methods.passkey && !supported()) parts.push(el("p", { text: L.unsupported }));
    else if (status.methods.passkey) {
      const code = el("input", { autocomplete: "one-time-code", placeholder: L.code, autocapitalize: "characters" });
      const name = el("input", { value: defaultName(), placeholder: L.name, maxlength: "60" });
      const enroll = el("form", { class: "auth-enroll", hidden: !status.needs_setup, onsubmit: async (e) => {
        e.preventDefault();
        try { await register(code.value.trim(), name.value.trim()); signedIn(); } catch (x) { fail(err, x); }
      } }, el("label", { text: L.code }, code), el("label", { text: L.name }, name),
      el("button", { class: "btn primary", type: "submit", text: L.create }));

      if (status.needs_setup) {
        parts.push(el("p", { text: L.setup }), enroll, el("button", { class: "btn link", type: "button", text: L.newcode,
          onclick: async () => { try { await call(`${AUTH}/setup-code`); err.textContent = L.logged; } catch (x) { fail(err, x); } } }));
      } else {
        parts.push(el("button", { class: "btn primary big", type: "button", text: L.signin,
          onclick: async () => { try { await signIn(); signedIn(); } catch (x) { fail(err, x); } } }));
        parts.push(el("button", { class: "btn link", type: "button", text: L.other,
          onclick: (e) => { e.target.hidden = true; enroll.hidden = false; code.focus(); } }), enroll);
      }
    }

    if (status.methods.token) {
      const input = el("input", { type: "password", autocomplete: "current-password", placeholder: L.tokenph });
      const form = el("form", { class: "auth-token", hidden: status.methods.passkey, onsubmit: (e) => {
        e.preventDefault();
        if (!input.value.trim()) return;
        localStorage.setItem("token", input.value.trim());
        done();
      } }, input, " ", el("button", { class: "btn", type: "submit", text: "OK" }));
      if (status.methods.passkey) {
        parts.push(el("button", { class: "btn link", type: "button", text: L.token,
          onclick: (e) => { e.target.hidden = true; form.hidden = false; input.focus(); } }));
      }
      parts.push(form);
    }
    parts.push(err);
    open("login", el("div", { class: "card auth", onclick: (e) => e.stopPropagation() }, parts));
  }

  // ------------------------------------------------------------ devices card
  async function showDevices() {
    let passkeysOn = true;
    try { passkeysOn = (await call(`${AUTH}/status`, null, "GET")).methods.passkey; } catch { /* assume on */ }
    const err = errorLine();
    const list = el("ul", { class: "auth-list" });
    const note = el("p", { class: "auth-note" });
    async function load() {
      if (!passkeysOn) return;
      try {
        const { passkeys } = await call("/console/api/passkeys", null, "GET");
        list.replaceChildren(...passkeys.map((p) => el("li", {},
          el("span", { text: p.name }),
          el("span", { class: "dim", text: p.last_used_at ? `${L.lastused} ${p.last_used_at.slice(0, 10)}` : L.never }),
          el("button", { class: "btn", type: "button", text: L.remove, onclick: async () => {
            try { await call(`/console/api/passkeys/${encodeURIComponent(p.id)}`, null, "DELETE"); load(); } catch (x) { fail(err, x); }
          } }))));
      } catch (x) { fail(err, x); }
    }
    open("devices", el("div", { class: "card auth", onclick: (e) => e.stopPropagation() },
      el("h2", { text: L.devices }), list, note,
      el("div", { class: "auth-actions" },
        passkeysOn && el("button", { class: "btn primary", type: "button", text: L.invite, onclick: async () => {
          try { note.textContent = L.invited((await call("/console/api/passkeys/invite")).code); } catch (x) { fail(err, x); }
        } }),
        el("button", { class: "btn", type: "button", text: L.signout, onclick: async () => {
          try { await call(`${AUTH}/logout`); } catch { /* signing out locally is enough */ }
          localStorage.removeItem("token");
          location.reload();
        } }),
        el("button", { class: "btn", type: "button", text: L.close, onclick: close })),
      err));
    load();
  }

  window.ConsoleAuth = { showLogin, showDevices, label: L.devices };
})();
