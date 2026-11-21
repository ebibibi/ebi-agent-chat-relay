// Leave work at night. The 🌙 beside the capture box starts the night shift (#1511): the server
// picks the threads whose next move is the agent's own and hands them the night instruction
// after a short grace period; what the box holds goes along as the top-priority line, and the
// panel under the box lets the person take threads out before they are sent. The 🌙 beside a
// reply box puts the night-instruction template into that box (#1476), sending nothing. The
// person's quiet hours tell notify.js when to stay silent. Quiet hours are off unless
// the person set them (GET /console/api/night). Loaded before notify.js and app.js.
"use strict";

window.ConsoleNight = (() => {
  const REFRESH_MS = 10 * 60 * 1000;
  let data = null; // { template, settings }
  let loadedAt = 0;
  let loading = null;

  function lang() { return (typeof LANG !== "undefined" && LANG === "en") ? "en" : "ja"; }

  async function load(force = false) {
    if (!force && data && Date.now() - loadedAt < REFRESH_MS) return data;
    if (!loading) {
      loading = api(`/night?lang=${lang()}`)
        .then((d) => { data = d; loadedAt = Date.now(); return d; })
        .catch(() => data) // no settings: the button falls back to nothing, notifications ring as before
        .finally(() => { loading = null; });
    }
    return loading;
  }

  // Put the template into *box*: replace an empty box, else add it below what is there.
  async function insert(box) {
    const d = await load();
    if (!d || !d.template) { toast(lang() === "ja" ? "テンプレートを読めませんでした" : "Could not load the template"); return; }
    const text = box.value.trim() ? `${box.value.replace(/\s+$/, "")}\n\n${d.template}` : d.template;
    box.value = text;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.focus();
    // Put the caret on the first blank to fill in, so typing replaces it.
    const at = box.value.indexOf("［");
    const blank = at >= 0 ? at : box.value.indexOf("[");
    if (blank < 0) return;
    const close = box.value.indexOf(at >= 0 ? "］" : "]", blank);
    box.setSelectionRange(blank, close >= 0 ? close + 1 : blank);
  }

  function button(box) {
    const label = lang() === "ja" ? "夜の指示テンプレートを入れる（送信はしません）" : "Insert the night-instruction template (does not send)";
    return h("button", { class: "btn night", type: "button", title: label, "aria-label": label,
      onclick: (e) => { e.preventDefault(); insert(typeof box === "function" ? box() : box); }, text: "🌙" });
  }

  const minutes = (hhmm) => { const [a, b] = hhmm.split(":").map(Number); return a * 60 + b; };

  // Whether the browser's clock is inside the person's quiet window.
  function quietNow(at = new Date()) {
    const s = data && data.settings;
    if (!s || !s.quiet_enabled) return false;
    const now = at.getHours() * 60 + at.getMinutes();
    const start = minutes(s.quiet_start);
    const end = minutes(s.quiet_end);
    return start < end ? now >= start && now < end : now >= start || now < end;
  }

  // Whether *item* still rings inside the quiet window.
  function urgent(item) {
    const s = data && data.settings;
    if (!s) return true;
    if ((s.urgent_statuses || []).includes(item.status)) return true;
    const title = String(item.title || item.thread_name || "").toLowerCase();
    return (s.urgent_keywords || []).some((w) => title.includes(String(w).toLowerCase()));
  }

  // -- the night shift (#1511) ---------------------------------------------------
  const L = () => lang() === "ja" ? {
    title: "🌙 夜番に任せたもの", none: "今夜AIに任せられるスレッドはありませんでした。判断が要るものは朝の1通に出ます。",
    wait: (s) => `${s}秒後に送ります。外したいものは ✕ を押してください。`, sent: "送りました。結果は朝の1通に出ます。",
    cancelAll: "全部取り消す", take: "外す", close: "閉じる", note: "最優先の指示",
    st: { planned: "送る前", sent: "送った", cancelled: "取り消し", skipped: "見送り" },
    label: "夜番に任せる：AIが続きを持っているスレッドに夜の指示を送ります（入力欄の文は最優先の指示になります）",
  } : {
    title: "🌙 Handed to the night shift", none: "No thread for the agents tonight. Decisions wait in the morning brief.",
    wait: (s) => `Sending in ${s}s. Press ✕ to take a thread out.`, sent: "Sent. Results show in the morning brief.",
    cancelAll: "Cancel all", take: "Take out", close: "Close", note: "Top priority",
    st: { planned: "planned", sent: "sent", cancelled: "cancelled", skipped: "skipped" },
    label: "Night shift: hand the agents' own next moves the night instruction (the box text becomes the top priority)",
  };
  let panel = null;
  let timer = null;

  function drawShift(shift) {
    if (!panel) return;
    const l = L();
    const items = (shift && shift.items) || [];
    const planned = items.filter((e) => e.state === "planned");
    const left = shift && shift.dispatch_at ? Math.max(0, Math.round((Date.parse(shift.dispatch_at) - Date.now()) / 1000)) : 0;
    const rows = items.map((e) => h("li", { class: `night-item ${e.state}` },
      h("a", { href: `#focus/${e.id}`, text: e.title || e.id }),
      h("span", { class: "dim", text: ` — ${l.st[e.state] || e.state}` }),
      e.state === "planned" ? h("button", { class: "icon-btn", type: "button", title: l.take, "aria-label": l.take,
        onclick: () => cancel(e.id), text: "✕" }) : null));
    panel.hidden = false;
    panel.replaceChildren(
      h("div", { class: "night-head" }, h("strong", { text: l.title }),
        h("button", { class: "icon-btn", type: "button", "aria-label": l.close, onclick: closePanel, text: "×" })),
      shift && shift.note ? h("div", { class: "dim", text: `${l.note}: ${shift.note}` }) : null,
      items.length ? h("ul", { class: "night-list" }, ...rows) : h("p", { text: l.none }),
      planned.length ? h("div", { class: "night-foot" }, h("span", { text: l.wait(left) }),
        h("button", { class: "btn", type: "button", onclick: () => cancel(null), text: l.cancelAll }))
        : items.some((e) => e.state === "sent") ? h("p", { class: "dim", text: l.sent }) : null);
    clearTimeout(timer);
    if (planned.length) timer = setTimeout(refreshShift, left > 0 ? Math.min(left * 1000 + 1500, 10000) : 5000);
  }

  function closePanel() { clearTimeout(timer); if (panel) panel.hidden = true; }

  async function refreshShift() {
    try { drawShift((await api("/night/shift")).shift); } catch { /* the panel keeps what it showed */ }
  }

  async function cancel(itemId) {
    try {
      const q = itemId ? `?item=${encodeURIComponent(itemId)}` : "";
      drawShift((await api(`/night/shift${q}`, { method: "DELETE" })).shift);
    } catch (err) { toast(err.message); }
  }

  async function startShift(box) {
    const note = box.value.trim();
    try {
      const res = await api("/night/shift", { method: "POST", body: { note: note || null, lang: lang() } });
      if (note) { box.value = ""; box.dispatchEvent(new Event("input", { bubbles: true })); }
      if (!panel) {
        panel = h("section", { class: "night-panel", id: "night-panel", "aria-live": "polite" });
        const anchor = document.getElementById("capture-status") || box.closest("form");
        anchor.after(panel);
      }
      drawShift(res.shift);
    } catch (err) { toast(err.message); }
  }

  function shiftButton(box) {
    const l = L();
    return h("button", { class: "btn night", type: "button", title: l.label, "aria-label": l.label,
      onclick: (e) => { e.preventDefault(); startShift(box); }, text: "🌙" });
  }

  return { load, insert, button, shiftButton, quietNow, urgent };
})();
