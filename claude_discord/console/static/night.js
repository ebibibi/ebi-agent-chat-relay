// Leave instructions at night (#1476): a 🌙 button beside the capture box and every reply box
// puts the night-instruction template into the box (nothing is sent until the person sends it),
// and the person's quiet hours tell notify.js when to stay silent. Quiet hours are off unless
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

  return { load, insert, button, quietNow, urgent };
})();
