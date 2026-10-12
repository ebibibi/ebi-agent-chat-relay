// Finishing a thread from the detail header. The button needs two clicks: the first arms
// it ("Click again to finish", for a few seconds), the second marks it done, so a stray
// click, or a double-click, changes nothing. Every finish (button or the "e" key) ends
// with a toast whose Undo reopens it; a done thread's header offers Reopen for later.
// Loaded before app.js, which puts button() in the header and calls finish() for "e".
"use strict";

window.ConsoleFinish = (() => {
  const ARM_MS = 3500;
  const UNDO_MS = 10000;
  const SETTLE_MS = 450; // a double-click is one gesture, not arm + confirm
  let armed = null; // item id whose button is waiting for the confirming click
  let armedAt = 0;
  let disarm = null;

  // The header re-renders on every poll, so the armed state lives here, not in the node.
  function arm(id) {
    armed = id;
    armedAt = Date.now();
    clearTimeout(disarm);
    disarm = setTimeout(() => { armed = null; renderDetail(); }, ARM_MS);
    renderDetail();
  }

  async function finish(id) {
    armed = null;
    clearTimeout(disarm);
    try {
      await api(`/items/${encodeURIComponent(id)}/done`, { method: "POST", body: {} });
    } catch (err) { toast(err.message); return; }
    await refresh();
    undoToast(id);
  }

  async function reopen(id) {
    try {
      await api(`/items/${encodeURIComponent(id)}/reopen`, { method: "POST", body: {} });
      toast(T.reopened);
      await refresh();
    } catch (err) { toast(err.message); }
  }

  function undoToast(id) {
    const item = state.byId.get(id);
    const name = (item && (item.title || item.thread_name)) || id;
    const t = h("div", { class: "toast undo", role: "status" },
      h("span", { text: `✅ ${T.finished}: ${name.slice(0, 40)}` }),
      h("button", { class: "btn", type: "button", text: `↩ ${T.undo}`,
        onclick: () => { t.remove(); reopen(id); } }));
    document.body.append(t);
    setTimeout(() => t.remove(), UNDO_MS);
  }

  function button(item) {
    if (item.bucket === "done") {
      return h("button", { class: "btn d-finish", type: "button", title: T.reopen_hint,
        onclick: () => reopen(item.id), text: `↩ ${T.reopen}` });
    }
    const on = armed === item.id;
    return h("button", { class: "btn d-finish" + (on ? " armed" : ""), type: "button",
      title: T.finish_hint, "aria-live": "polite",
      onclick: () => {
        if (!on) arm(item.id);
        else if (Date.now() - armedAt >= SETTLE_MS) finish(item.id);
      },
      text: on ? `✅ ${T.finish_confirm}` : `✅ ${T.finish}` });
  }

  return { button, finish, reopen };
})();
