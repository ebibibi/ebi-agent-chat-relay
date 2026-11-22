// Every request the Console makes goes through ConsoleNet.fetch, which tells three failures
// apart and says which one happened instead of a bare "Failed to fetch":
//
// * the network is down — say so in plain words; the next poll retries by itself;
// * an identity-aware reverse proxy in front of the Console (Entra application proxy,
//   Cloudflare Access, an oauth2-proxy, …) wants a new sign-in — its session cookie expired.
//   It answers every request with a redirect to the identity provider on another origin.
//   fetch() follows it, the provider sends no CORS headers, and the request dies as a
//   TypeError before it reaches ccdb: from the page it looks exactly like a lost network.
//   A second, same-origin probe with redirect: "manual" shows the difference — the proxy
//   redirects it again (an "opaqueredirect" response), a dead network throws again;
// * anything else — passed through untouched.
//
// Only a full navigation can follow the proxy to the provider (whose own session usually
// signs the browser in again without a prompt), so recovery is a page reload. A reload
// must not cost the human anything: what they typed (every text box they touched, plus
// whatever modules register with keep()) and the open view (location.hash) are saved to
// sessionStorage first and put back after the page loads again. It never reloads while
// someone is typing or while a module reports unsaved work (busy()); a one-line banner
// with "Reconnect" stays instead, which signs in again in a small window and keeps this
// page exactly as it is. A loop guard stops automatic reloads when sign-in keeps failing.
"use strict";

(() => {
  const PROBE = "/static/reauth.html";
  const STORE = "ccdb.net.saved";
  const RELOADS = "ccdb.net.reloads";
  const CHANNEL = "ccdb-net";
  // No keystroke for this long = done typing. Tests shorten it before the page loads.
  const IDLE_MS = Number(window.CCDB_NET_IDLE_MS) || 10000;
  const SAVED_TTL_MS = 86400000;  // a sign-in that took a day still gets its drafts back
  const RESTORE_MS = 60000;       // how long restored text waits for its box to be drawn
  const GUARD = { max: 3, windowMs: 600000, gapMs: 30000 };
  const L = new Proxy({
    en: {
      network: "Can't reach the Console — check the network connection. It retries by itself.",
      signin: "Your sign-in expired. Reconnecting…",
      banner: "Your sign-in expired.",
      waiting: "It reconnects when you stop typing — what you typed is kept.",
      unsaved: "You have unsaved changes; reconnect in a small window to keep them.",
      stopped: "Automatic reconnect stopped after several tries.",
      reconnect: "Reconnect", popup: "Signing in in another window…", restored: "Restored what you were typing",
    },
    ja: {
      network: "Console に接続できません。ネットワークを確認してください（自動で再試行します）",
      signin: "サインインの有効期限が切れました。再接続しています…",
      banner: "サインインの有効期限が切れました。",
      waiting: "入力が終わったら自動で再接続します。入力中の内容は保持されます。",
      unsaved: "保存していない変更があります。小さなウィンドウで再接続すると、この画面のまま続けられます。",
      stopped: "何度か試しても戻らないため、自動の再接続を止めました。",
      reconnect: "再接続", popup: "別のウィンドウでサインインしています…", restored: "入力中だった内容を復元しました",
    },
  }, { get: (s, k) => s[document.documentElement.lang === "ja" ? "ja" : "en"][k] });

  // ------------------------------------------------------------ pure helpers (unit-tested)
  /** Whether another automatic reload is allowed, given earlier reload times (ms). */
  function allowReload(times, now, guard = GUARD) {
    const recent = times.filter((t) => now - t < guard.windowMs);
    if (recent.length >= guard.max) return false;
    return !recent.length || now - Math.max(...recent) >= guard.gapMs;
  }
  /** A probe answer that means "the proxy redirected": manual mode hides 3xx as opaqueredirect. */
  const isRedirect = (res) => res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400);
  /** A stable name for a text box, so the same box can be found again after a reload. */
  function fieldKey(el) {
    if (el.id) return `#${el.id}`;
    const a = (n) => el.getAttribute(n) || "";
    return [el.tagName.toLowerCase(), el.className, a("name"), a("aria-label"), a("placeholder")].join("|");
  }
  const sameKey = (key) => [...document.querySelectorAll("textarea, input")].filter((el) => fieldKey(el) === key);
  const isText = (el) => el && (el.tagName === "TEXTAREA"
    || (el.tagName === "INPUT" && /^(text|search|url|email|tel|number|)$/.test(el.type || "")));

  // ------------------------------------------------------------ state
  const providers = new Map();  // key -> {save, restore}
  const busyChecks = new Set();
  const touched = new Set();
  let lastInput = 0, composing = false, pending = false, timer = null, banner = null;
  const read = (k, fallback) => { try { return JSON.parse(sessionStorage.getItem(k)) ?? fallback; } catch { return fallback; } };
  const write = (k, v) => { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* storage full or blocked */ } };

  const mark = (e) => { lastInput = Date.now(); if (e.type === "input" && isText(e.target)) touched.add(e.target); };
  for (const type of ["input", "keydown", "paste"]) document.addEventListener(type, mark, true);
  document.addEventListener("compositionstart", () => { composing = true; lastInput = Date.now(); }, true);
  document.addEventListener("compositionend", () => { composing = false; lastInput = Date.now(); }, true);

  // ------------------------------------------------------------ errors
  function failure(kind, cause) {
    const err = new Error(L[kind === "signin" ? "signin" : "network"]);
    err.kind = kind;
    err.cause = cause;
    return err;
  }
  async function classify(cause) {
    let probe;
    try {
      probe = await fetch(PROBE, { cache: "no-store", redirect: "manual", credentials: "same-origin" });
    } catch { return failure("network", cause); }
    if (isRedirect(probe)) { signInNeeded(); return failure("signin", cause); }
    return failure("network", cause);
  }

  async function netFetch(input, init = {}) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin) return fetch(input, init);
    let res;
    try {
      res = await fetch(input, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw await classify(err);
    }
    // A proxy whose sign-in page lives on this origin: an API call that was redirected to HTML.
    if (res.redirected && url.pathname.startsWith("/console/api/")
      && !/json/i.test(res.headers.get("content-type") || "")) {
      signInNeeded();
      throw failure("signin");
    }
    if (pending) recovered();
    return res;
  }

  // ------------------------------------------------------------ save / restore
  function snapshot() {
    const fields = [];
    for (const el of touched) {
      if (!el.isConnected || !el.value || !el.value.trim()) continue;
      const key = fieldKey(el);
      fields.push({ key, index: sameKey(key).indexOf(el), value: el.value });
    }
    const kept = {};
    for (const [key, p] of providers) {
      try { const v = p.save(); if (v !== undefined && v !== null) kept[key] = v; } catch { /* one module must not block the rest */ }
    }
    return { at: Date.now(), hash: location.hash, fields, kept };
  }
  const saved = (() => {
    const s = read(STORE, null);
    if (!s || typeof s !== "object" || Date.now() - (s.at || 0) > SAVED_TTL_MS) { sessionStorage.removeItem(STORE); return null; }
    return s;
  })();
  // A sign-in that went through the provider's form post can drop the fragment; put the view back
  // before app.js reads it.
  if (saved && typeof saved.hash === "string" && /^#[^\s]*$/.test(saved.hash) && !location.hash) {
    history.replaceState(null, "", saved.hash);
  }

  function restoreFields() {
    if (!saved || !Array.isArray(saved.fields) || !saved.fields.length) { sessionStorage.removeItem(STORE); return; }
    let left = saved.fields.slice(), restored = 0;
    const tryNow = () => {
      left = left.filter((f) => {
        const el = sameKey(f.key)[Math.max(0, f.index)] || sameKey(f.key)[0];
        if (!el) return true;
        if (!el.value) {
          el.value = f.value;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          touched.add(el);
          restored += 1;
        }
        return false;
      });
      if (!left.length) done();
    };
    const observer = new MutationObserver(() => tryNow());
    const stop = setTimeout(() => done(), RESTORE_MS);
    function done() {
      observer.disconnect();
      clearTimeout(stop);
      sessionStorage.removeItem(STORE);
      if (restored) say(L.restored);
    }
    observer.observe(document.documentElement, { childList: true, subtree: true });
    tryNow();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", restoreFields);
  else restoreFields();

  // ------------------------------------------------------------ recovery
  const busy = () => [...busyChecks].some((f) => { try { return !!f(); } catch { return false; } });
  const typing = () => composing || Date.now() - lastInput < IDLE_MS;

  function signInNeeded() {
    if (pending) return;
    pending = true;
    showBanner();
    timer = setInterval(tick, 1000);
    tick();
  }
  function tick() {
    if (!pending) return;
    const times = read(RELOADS, []);
    const allowed = allowReload(Array.isArray(times) ? times : [], Date.now());
    showBanner(!allowed ? L.stopped : busy() ? L.unsaved : L.waiting);
    if (allowed && !busy() && !typing()) reload(times);
  }
  function reload(times) {
    write(STORE, snapshot());
    write(RELOADS, [...(Array.isArray(times) ? times : []), Date.now()].slice(-10));
    clearInterval(timer);
    location.reload();
  }
  function recovered() {
    pending = false;
    clearInterval(timer);
    if (banner) { banner.remove(); banner = null; }
    window.dispatchEvent(new Event("console:reconnected"));
  }
  // "Reconnect": sign in again in a small window, which keeps this page and everything on it.
  // The proxy sends that window back to reauth.html, which tells us over a BroadcastChannel.
  function reconnect() {
    const win = window.open(PROBE, "ccdb-reauth", "popup,width=520,height=640");
    if (!win) { reload(read(RELOADS, [])); return; }  // popups blocked: save and reload instead
    showBanner(L.popup);
    const until = Date.now() + 300000;
    const check = setInterval(async () => {
      if (!pending || Date.now() > until) { clearInterval(check); return; }
      try {
        const res = await fetch(PROBE, { cache: "no-store", redirect: "manual", credentials: "same-origin" });
        if (!isRedirect(res)) { clearInterval(check); recovered(); }
      } catch { /* still offline */ }
    }, 2000);
  }
  if (window.BroadcastChannel) {
    new BroadcastChannel(CHANNEL).onmessage = (e) => { if (e.data === "signed-in" && pending) recovered(); };
  }

  function showBanner(detail) {
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "net-banner";
      banner.setAttribute("role", "alert");
      const text = document.createElement("span");
      text.className = "net-banner-text";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "net-banner-btn";
      btn.textContent = L.reconnect;
      btn.addEventListener("click", reconnect);
      banner.append(text, btn);
      document.body.append(banner);
    }
    banner.firstChild.textContent = detail ? `${L.banner} ${detail}` : L.banner;
  }
  function say(msg) {
    const t = document.createElement("div");
    t.className = "toast";
    t.setAttribute("role", "status");
    t.textContent = msg;
    document.body.append(t);
    setTimeout(() => t.remove(), 4000);
  }

  window.ConsoleNet = {
    fetch: netFetch,
    /** Keep a module's own state across a sign-in reload. restore() runs at once if it was saved. */
    keep(key, save, restore) {
      providers.set(key, { save, restore });
      if (saved && saved.kept && Object.hasOwn(saved.kept, key)) {
        try { restore(saved.kept[key]); } catch { /* a stale shape must not break the page */ }
      }
    },
    /** A check that returns true while a reload would lose work no text box holds. */
    busy(check) { busyChecks.add(check); return () => busyChecks.delete(check); },
    pending: () => pending,
    _test: { allowReload, isRedirect, fieldKey, GUARD },
  };
})();
