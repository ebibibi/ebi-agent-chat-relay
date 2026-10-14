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

  // Snapshot a post-order list: descendants must finish before their parent.
  function targets(ids) {
    const seen = new Set(), result = [];
    const visit = id => {
      if (seen.has(id)) return;
      seen.add(id);
      for (const item of state.items) if (item.parent_id === id) visit(item.id);
      if (state.byId.get(id)?.bucket !== "done") result.push(id);
    };
    ids.forEach(visit);
    return result;
  }
  async function completeMany(ids, progress = () => {}) {
    const parents = new Map(state.items.map(i => [i.id,i.parent_id]));
    const errors = new Map(), completed = [];
    for (const id of ids) {
      const failedBelow = [...errors.keys()].some(child => {
        const seen = new Set();
        for (let parent = parents.get(child); parent && !seen.has(parent); parent = parents.get(parent)) {
          if (parent === id) return true;
          seen.add(parent);
        }
        return false;
      });
      if (failedBelow) {
        errors.set(id, document.documentElement.lang === 'ja' ? '子の完了に失敗したため未完了のままです' : 'A child failed; parent remains open');
      } else {
        try { await api(`/items/${encodeURIComponent(id)}/done`, {method:'POST',body:{}}); completed.push(id); }
        catch (err) { errors.set(id,err.message); }
      }
      progress(completed.length+errors.size);
    }
    return {completed,errors};
  }
  let finishing = false;
  async function finish(id) {
    if (finishing) return;
    armed = null;
    clearTimeout(disarm);
    finishing = true;
    renderDetail();
    try {
      const result = await completeMany(targets([id]));
      await refresh();
      if (result.errors.size) {
        toast([...result.errors].map(([key,error]) => `${state.byId.get(key)?.title || key}: ${error}`).join(' · '));
      } else if (result.completed.length) undoToast(id,result.completed);
    } finally { finishing = false; renderDetail(); }
  }

  async function reopen(id) {
    try {
      await api(`/items/${encodeURIComponent(id)}/reopen`, { method: "POST", body: {} });
      toast(T.reopened);
      await refresh();
    } catch (err) { toast(err.message); }
  }

  function undoToast(id, ids = [id]) {
    const item = state.byId.get(id);
    const name = (item && (item.title || item.thread_name)) || id;
    const t = h("div", { class: "toast undo", role: "status" },
      h("span", { text: `✅ ${T.finished}: ${name.slice(0, 40)}` }),
      h("button", { class: "btn", type: "button", text: `↩ ${T.undo}`,
        onclick: async () => { t.remove(); for (const key of ids) await reopen(key); } }));
    document.body.append(t);
    setTimeout(() => t.remove(), UNDO_MS);
  }

  function button(item) {
    if (item.bucket === "done") {
      return h("button", { class: "btn d-finish", type: "button", title: T.reopen_hint,
        onclick: () => reopen(item.id), text: `↩ ${T.reopen}` });
    }
    const on = armed === item.id;
    const count = targets([item.id]).length;
    const label = count > 1 ? (document.documentElement.lang === "ja" ? `親子${count}件を完了` : `Complete family (${count})`) : T.finish;
    return h("button", { class: "btn d-finish" + (on ? " armed" : ""), type: "button",
      title: T.finish_hint, "aria-live": "polite", disabled:finishing,
      onclick: () => {
        if (!on) arm(item.id);
        else if (Date.now() - armedAt >= SETTLE_MS) finish(item.id);
      },
      text: finishing ? T.loading : on ? `✅ ${T.finish_confirm} (${count})` : `✅ ${label}` });
  }

  return { button, finish, reopen, targets, completeMany };
})();
