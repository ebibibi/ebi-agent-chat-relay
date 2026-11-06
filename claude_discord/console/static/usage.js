// Usage strip: how much each backend has left and when an exhausted one is back.
// Always on screen, above the board; on a phone it folds to the default backend until opened.
// Server data reaches the page through textContent only.
"use strict";

(() => {
  const POLL_MS = 60000;
  const TICK_MS = 20000;
  const L = new Proxy({
    en: { back: "back", reset: "reset", unavailable: "usage unavailable", nodata: "no usage yet", subs: "Subscription usage (Foundry / API routes are not counted here)",
          credits: (n) => `${n} resets`, more: "Show every backend", less: "Show only the default", resetsAt: "resets", d: "d", h: "h", m: "m",
          out: (at, left) => `⛔ back ${at} (${left})` },
    ja: { back: "復活", reset: "リセット済", unavailable: "取得できません", nodata: "まだ記録なし", subs: "サブスクの利用枠（Foundry や API の接続先はここに含まれません）",
          credits: (n) => `リセット券 ${n}`, more: "すべてのAIを表示", less: "既定のAIだけ表示", resetsAt: "リセット", d: "日", h: "時間", m: "分",
          out: (at, left) => `⛔ 復活 ${at}（あと${left}）` },
  }, {get: (strings, key) => strings[document.documentElement.lang === "ja" ? "ja" : "en"][key]});
  const NAMES = { claude: "Claude", codex: "Codex", antigravity: "Antigravity", pi: "pi", local: "Local", agui: "AG-UI" };
  const WINDOWS = new Proxy({}, {get: (_, key) => {
    const week = document.documentElement.lang === "ja" ? "週" : "7d";
    return {five_hour:"5h",seven_day:week,seven_day_opus:`${week} Opus`,seven_day_sonnet:`${week} Sonnet`,gemini:"Gemini",partner:"Claude/GPT"}[key];
  }});

  const strip = document.getElementById("usage");
  if (!strip) return;
  let data = null;
  let skew = 0; // server clock minus browser clock, seconds
  let open = false; // phone: the strip starts folded to the default backend
  const narrow = window.matchMedia("(max-width: 900px)");

  const now = () => Date.now() / 1000 + skew;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const level = (u) => (u >= 0.9 ? "hot" : u >= 0.7 ? "warn" : "ok");

  function countdown(epoch) {
    let s = Math.max(0, Math.round(epoch - now()));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.max(d || h ? 0 : 1, Math.floor(s / 60));
    if (d) return `${d}${L.d}${h}${L.h}`;
    if (h) return `${h}${L.h}${m}${L.m}`;
    return `${m}${L.m}`;
  }

  function clock(epoch) {
    const at = new Date((epoch - skew) * 1000);
    const opts = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    if (at.toDateString() !== new Date().toDateString()) {
      Object.assign(opts, { month: "numeric", day: "numeric", weekday: "short" });
    }
    return at.toLocaleString(document.documentElement.lang, opts);
  }

  function windowChip(w) {
    const pct = Math.round(w.utilization * 100);
    const chip = el("span", `u-win ${level(w.utilization)}`);
    const bar = el("span", "u-bar");
    const fill = el("i");
    fill.style.width = `${Math.min(100, pct)}%`;
    bar.append(fill);
    chip.append(el("span", "u-k", WINDOWS[w.type] || w.type), bar, el("span", "u-pct", `${pct}%`),
      el("span", "u-rs", w.reset ? L.reset : `↻${countdown(w.resets_at)}`));
    chip.title = w.reset ? L.reset : `${L.resetsAt} ${clock(w.resets_at)}`;
    return chip;
  }

  function backendBlock(b) {
    const worst = Math.max(0, ...b.windows.map((w) => w.utilization));
    const state = !b.available ? "out" : b.error ? "err" : level(worst);
    const block = el("div", `u-be ${state}`);
    const name = el("span", "u-name", NAMES[b.backend] || b.backend);
    // These windows are the subscription's: a Foundry or API route is not counted here.
    if (b.backend === "claude" || b.backend === "codex") { name.append(el("span", "u-prof", "💳")); name.title = L.subs; }
    if (b.profile) name.append(el("span", "u-prof", b.profile));
    block.append(name);
    if (!b.available && b.unavailable_until) {
      block.append(el("span", "u-out", L.out(clock(b.unavailable_until), countdown(b.unavailable_until))));
    }
    if (b.error) block.append(el("span", "u-note", L.unavailable));
    else if (!b.windows.length) block.append(el("span", "u-note", L.nodata));
    b.windows.forEach((w) => block.append(windowChip(w)));
    if (b.reset_credits) block.append(el("span", "u-note", L.credits(b.reset_credits)));
    if (b.plan) block.title = b.plan;
    return block;
  }

  function render() {
    const backends = (data && data.backends) || [];
    // CLIs that are not signed in get a sign-in chip from clilogin.js.
    const signIn = window.consoleCliLogin ? window.consoleCliLogin.chips() : [];
    strip.hidden = backends.length === 0 && signIn.length === 0;
    const blocks = backends.map(backendBlock);
    // A phone folds the strip to the default backend; sign-in chips stay, they need action.
    const def = window.ConsoleBackend && window.ConsoleBackend.defaultBackend();
    const others = backends.filter((b) => b.backend !== def).length;
    const fold = narrow.matches && others > 0;
    strip.classList.toggle("folded", fold && !open);
    // Every render replaces the toggle; whoever was on it (keyboard, screen reader) stays on it.
    const focused = document.activeElement?.classList.contains("u-toggle");
    if (fold) {
      const toggle = el("button", "u-toggle", open ? "▴" : `+${others} ▾`);
      toggle.type = "button";
      toggle.title = open ? L.less : L.more;
      toggle.setAttribute("aria-label", toggle.title);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.onclick = () => { open = !open; render(); };
      const shown = open ? blocks : blocks.filter((_, i) => backends[i].backend === def);
      strip.replaceChildren(...shown, ...signIn, toggle);
      if (focused) toggle.focus();
    } else {
      strip.replaceChildren(...blocks, ...signIn);
    }
    // Narrow screens overlay the nav and detail panes; they start below the strip.
    document.documentElement.style.setProperty("--usage-h", `${strip.hidden ? 0 : strip.offsetHeight}px`);
  }

  async function load() {
    const headers = { Accept: "application/json" };
    const token = localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const res = await fetch("/console/api/usage", { headers, credentials: "same-origin" });
      if (!res.ok) return; // 401 is the board's sign-in to handle; try again next poll
      data = await res.json();
      if (typeof data.now === "number") skew = data.now - Date.now() / 1000;
      render();
    } catch { /* offline: keep showing the last answer */ }
  }

  let poll = setInterval(load, POLL_MS);
  let tick = setInterval(render, TICK_MS);
  document.addEventListener("visibilitychange", () => {
    clearInterval(poll); clearInterval(tick);
    if (!document.hidden) { load(); poll = setInterval(load, POLL_MS); tick = setInterval(render, TICK_MS); }
  });
  window.addEventListener("resize", render);
  // A new default folds the strip around it, even if it was expanded.
  window.addEventListener("console-defaults-change", () => { open = false; render(); });
  window.addEventListener("console-language-change", render);
  window.addEventListener("console-cli-login-change", render);
  load();
})();
