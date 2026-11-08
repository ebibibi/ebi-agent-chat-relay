// The reply box at the bottom of the situation panel: answer the parent or any member of the
// family without leaving the panel. Built once per pane so typing survives the panel's redraws.
// Loaded after situation.js's dependencies and before situation.js, which owns the panel.
"use strict";
window.ConsoleSituationReply = (() => {
  const STRINGS = {
    en: { to: "To", parent: "Parent", child: "Child", ph: "Reply from here… (Ctrl+Enter to send)", send: "Send" },
    ja: { to: "宛先", parent: "親", child: "子", ph: "ここから返信…（Ctrl+Enterで送信）", send: "送信" },
  };
  const L = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];
  const drafts = new Map(); // target item id -> text, kept across threads and redraws
  let target = null; // item id the box sends to
  let sending = false;

  const nodes = () => ({ slot: $("d-side-reply"), select: $("sit-to"), box: $("sit-reply") });

  function build(slot) {
    const l = L();
    const box = h("textarea", { id: "sit-reply", rows: 2, placeholder: l.ph,
      oninput: (e) => { drafts.set(target, e.target.value); grow(e.target); },
      onkeydown: (e) => { if (isSendKey(e)) { e.preventDefault(); send(); } } });
    const select = h("select", { id: "sit-to", "aria-label": l.to,
      onchange: (e) => choose(e.target.value) });
    slot.replaceChildren(h("form", { class: "sit-reply", onsubmit: (e) => { e.preventDefault(); send(); } },
      h("label", { class: "sit-to" }, h("span", { class: "dim", text: `${l.to}:` }), select),
      box,
      h("button", { class: "btn primary", type: "submit", text: l.send })));
    slot.dataset.built = "1";
  }

  // members: [{item, role}] with the parent first. Keeps the chosen target while it is listed.
  function render(members, fallback) {
    const { slot } = nodes();
    if (!slot) return;
    if (!slot.dataset.built) build(slot);
    const { select, box } = nodes();
    const ids = members.map((m) => m.item.id);
    if (!ids.includes(target)) target = fallback;
    const key = JSON.stringify([LANG, members.map((m) => [m.item.id, m.role, m.item.title])]);
    if (select.dataset.key !== key) {
      select.dataset.key = key;
      const l = L();
      select.replaceChildren(...members.map(({ item, role }) => h("option", { value: item.id,
        text: `${role === "parent" ? l.parent : l.child}: ${item.title || item.thread_name || item.id}` })));
    }
    select.value = target;
    if (document.activeElement !== box) box.value = drafts.get(target) || "";
  }

  function choose(id, { focus = false } = {}) {
    const { select, box } = nodes();
    target = id;
    if (!select || !box) return;
    select.value = id;
    box.value = drafts.get(id) || "";
    grow(box);
    if (focus) focusEnd(box);
  }

  function quote(id, text) {
    const quoted = `${text.split("\n").map((line) => `> ${line}`).join("\n")}\n\n`;
    drafts.set(id, quoted + (drafts.get(id) || ""));
    choose(id, { focus: true });
  }

  function focusEnd(box) {
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }

  async function send() {
    const { box } = nodes();
    if (sending || !box || !target) return;
    const text = box.value.trim();
    if (!text) return;
    const id = target;
    sending = true;
    box.disabled = true;
    try {
      const body = await window.ConsolePaste.prepareText(text, []);
      const sent = await api(`/items/${encodeURIComponent(id)}/reply`, { method: "POST", body });
      drafts.delete(id);
      box.value = "";
      grow(box);
      toast(sent && sent.delivery === "injected" ? T.sent_live : T.sent);
      await refresh();
      if (state.open === id) setTimeout(() => loadMessages(id), 1500);
    } catch (err) { toast(err.message); }
    finally { sending = false; box.disabled = false; box.focus(); }
  }

  return { render, choose, quote };
})();
