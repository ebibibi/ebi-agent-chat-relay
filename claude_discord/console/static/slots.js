// Running work at a glance and in hand: the nav shows "running / limit" with − / + to change
// the limit on the spot, and each thread gets one-click controls for its state —
// running: ⏸ Pause (stop the turn, mark it paused) and ■ Stop; paused: ▶ Resume (ask the
// agent to carry on); waiting for a slot: ⏫ First and ⏬ Later. Loaded before app.js, which
// puts nav() in the nav and controls() on rows and in the detail header.
"use strict";

window.ConsoleSlots = (() => {
  const stopping = new Set();
  const stopRequests = new Set();

  function syncStopButtons() {
    for (const button of document.querySelectorAll("[data-stop-id]")) {
      const busy = stopping.has(button.dataset.stopId);
      button.disabled = busy;
      button.setAttribute("aria-busy", String(busy));
      button.title = busy ? T.stopping : T.stop_hint;
      button.setAttribute("aria-label", button.title);
      button.textContent = button.dataset.compact === "true"
        ? (busy ? "⏳" : "■") : (busy ? T.stopping : `■ ${T.stop}`);
    }
  }

  async function stop(itemId) {
    if (stopping.has(itemId)) return;
    stopping.add(itemId);
    stopRequests.add(itemId);
    syncStopButtons();
    try {
      await api(`/items/${encodeURIComponent(itemId)}/stop`, { method: "POST", body: {} });
    } catch (err) {
      stopping.delete(itemId);
      toast(err.message);
    } finally {
      stopRequests.delete(itemId);
      syncStopButtons();
    }
    await refresh();
  }

  // A 202 acknowledges the request; keep feedback until the board confirms it stopped.
  function onBoard(items) {
    for (const itemId of stopping) {
      const item = items.find((i) => i.id === itemId);
      if (!stopRequests.has(itemId) && !item?.running && !item?.queued) stopping.delete(itemId);
    }
    syncStopButtons();
  }

  function stopButton(itemId, compact = false, cls = "slot-btn stop") {
    const busy = stopping.has(itemId);
    return h("button", {
      class: `btn ${cls}`, type: "button", "data-stop-id": itemId, "data-compact": String(compact),
      disabled: busy, "aria-busy": String(busy),
      title: busy ? T.stopping : T.stop_hint, "aria-label": busy ? T.stopping : T.stop_hint,
      text: compact ? (busy ? "⏳" : "■") : (busy ? T.stopping : `■ ${T.stop}`),
      onclick: (e) => { e.stopPropagation(); stop(itemId); },
    });
  }

  async function post(path, body, done) {
    try {
      const data = await api(path, { method: "POST", body: body || {} });
      if (done) toast(done);
      await refresh();
      return data;
    } catch (err) { toast(err.message); return null; }
  }

  function setLimit(value) {
    return post("/slots", { max: value }, T.slots_set(value));
  }

  // The nav block: counts, and − / + when a limit is configured.
  function nav() {
    const s = state.slots || {};
    const running = s.running ?? 0;
    const label = h("span", { class: "slots-text", text: T.slots(running, s.max, s.waiting) });
    if (!s.max) return h("div", { class: "slots" }, label);
    const step = (delta, text, hint) => h("button", { class: "btn slots-step", type: "button", title: hint,
      "aria-label": hint, disabled: s.max + delta < 1 || s.max + delta > 32,
      onclick: () => setLimit(s.max + delta), text });
    return h("div", { class: "slots", title: T.slots_hint },
      label, h("span", { class: "slots-steps" }, step(-1, "−", T.slots_down), step(1, "+", T.slots_up)));
  }

  const id = (item) => encodeURIComponent(item.id);

  // The buttons for *item*'s state; empty when nothing applies. *compact* drops the words.
  function controls(item, compact) {
    const b = (cls, icon, text, hint, onclick) => h("button", {
      class: `btn slot-btn ${cls}`, type: "button", title: hint, "aria-label": hint,
      onclick: (e) => { e.stopPropagation(); onclick(); },
      text: compact ? icon : `${icon} ${text}`,
    });
    const out = [];
    if (item.running) {
      out.push(b("pause", "⏸", T.pause, T.pause_hint, () => post(`/items/${id(item)}/pause`, {}, T.paused_toast)));
      out.push(stopButton(item.id, compact));
    } else if (item.queued) {
      out.push(b("first", "⏫", T.first, T.first_hint, () => post(`/items/${id(item)}/queue`, { action: "first" })));
      out.push(b("later", "⏬", T.later, T.later_hint, () => post(`/items/${id(item)}/queue`, { action: "later" })));
    } else if (item.paused_at) {
      out.push(b("resume", "▶", T.resume, T.resume_hint, () => post(`/items/${id(item)}/resume`, {}, T.resumed_toast)));
    }
    return out;
  }

  return { nav, controls, setLimit, stopButton, onBoard };
})();
