// Usage strip: how much each backend has left and when an exhausted one is back.
// Always on screen, above the board. Server data reaches the page through textContent only.
"use strict";

(() => {
  const POLL_MS = 60000;
  const TICK_MS = 20000;
  const lang = document.documentElement.lang === "ja" ? "ja" : "en";
  const L = {
    en: { back: "back", reset: "reset", unavailable: "usage unavailable", nodata: "no usage yet",
          credits: (n) => `${n} resets`, resetsAt: "resets", d: "d", h: "h", m: "m",
          out: (at, left) => `⛔ back ${at} (${left})` },
    ja: { back: "復活", reset: "リセット済", unavailable: "取得できません", nodata: "まだ記録なし",
          credits: (n) => `リセット券 ${n}`, resetsAt: "リセット", d: "日", h: "時間", m: "分",
          out: (at, left) => `⛔ 復活 ${at}（あと${left}）` },
  }[lang];
  const NAMES = { claude: "Claude", codex: "Codex", pi: "pi", local: "Local", agui: "AG-UI" };
  const WEEK = lang === "ja" ? "週" : "7d";
  const WINDOWS = { five_hour: "5h", seven_day: WEEK,
    seven_day_opus: `${WEEK} Opus`, seven_day_sonnet: `${WEEK} Sonnet` };

  const strip = document.getElementById("usage");
  if (!strip) return;
  let data = null;
  let skew = 0; // server clock minus browser clock, seconds

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
    return at.toLocaleString(lang, opts);
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
    strip.hidden = backends.length === 0;
    strip.replaceChildren(...backends.map(backendBlock));
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
  load();
})();
