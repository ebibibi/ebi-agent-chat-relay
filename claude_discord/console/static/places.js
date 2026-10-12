// Places: the locations and devices work can be bound to ("home", "office", "work PC").
// - picker(item): the 📍 button in the detail header. One click opens a menu of places;
//   pick one, "Anywhere", or type a new one. A person's choice beats the model's guess.
// - manage(): a panel to add, rename and delete places. A rename or delete reaches every
//   item that carries the place (and the model's guesses).
// The list comes with the board (state.places). Loaded before app.js and focus.js.
"use strict";

window.ConsolePlaces = (() => {
  const names = () => (state.places || []).map((p) => p.name);

  async function call(method, body) {
    try {
      const data = await api("/places", { method, body });
      state.places = data.places;
      await refresh();
      return true;
    } catch (err) { toast(err.message); return false; }
  }

  async function setPlace(item, place) {
    try { await api(`/items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { place } }); }
    catch (err) { toast(err.message); return; }
    toast(place ? `📍 ${place}` : `📍 ${T.anywhere}`);
    await refresh();
  }

  // A text box that calls onAdd(name) on Enter or the button.
  function addBox(onAdd) {
    const input = h("input", { type: "text", maxlength: 40, placeholder: T.place_new, size: 12,
      onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } } });
    const submit = () => { const v = input.value.trim(); if (v) onAdd(v); };
    return h("div", { class: "place-add" }, input, h("button", { class: "btn", type: "button", onclick: submit, text: T.add }));
  }

  // ---------------------------------------------------------------- the item menu
  let menu = null;
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }
  document.addEventListener("click", (e) => { if (menu && !menu.contains(e.target)) closeMenu(); }, true);

  function openMenu(item, anchor) {
    closeMenu();
    const current = item.place_by === "ai" ? null : item.place;
    const chip = (value, label) => h("button", { type: "button", class: "chip" + (current === value ? " on" : ""),
      onclick: () => { closeMenu(); setPlace(item, value); }, text: label });
    menu = h("div", { class: "place-menu", role: "menu" },
      h("div", { class: "chips" }, chip(null, T.anywhere), ...names().map((n) => chip(n, `📍 ${n}`))),
      item.place_by === "ai" ? h("div", { class: "dim", text: `🤖 ${T.ai_guess}: ${item.place}` }) : null,
      addBox((name) => { closeMenu(); setPlace(item, name); }),
      h("button", { class: "btn link", type: "button", onclick: () => { closeMenu(); manage(); }, text: `⚙ ${T.place_manage}` }));
    document.body.append(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.querySelector("input").focus();
  }

  function picker(item) {
    const label = item.place ? `📍 ${item.place}` : `📍 ${T.place}`;
    const btn = h("button", { class: "btn d-place" + (item.place ? " on" : "") + (item.place_by === "ai" ? " ai" : ""),
      type: "button", title: T.place_hint, text: label,
      onclick: (e) => { e.stopPropagation(); if (menu) closeMenu(); else openMenu(item, btn); } });
    return btn;
  }

  // ---------------------------------------------------------------- the management panel
  let panel = null;
  function closePanel() { if (panel) { panel.remove(); panel = null; } }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || (!panel && !menu)) return;
    closeMenu(); closePanel();
    e.stopImmediatePropagation();
  }, true);

  function placeRow(p) {
    const input = h("input", { type: "text", value: p.name, maxlength: 40, size: 14, "aria-label": T.place,
      onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); },
      onchange: async (e) => {
        const name = e.target.value.trim();
        if (!name || name === p.name) { e.target.value = p.name; return; }
        if (await call("PATCH", { from: p.name, name })) drawPanel();
      } });
    const del = h("button", { class: "btn danger", type: "button", text: T.delete,
      onclick: async () => {
        if (p.count && !window.confirm(T.place_delete_confirm(p.name, p.count))) return;
        if (await call("DELETE", { name: p.name })) drawPanel();
      } });
    return h("li", {}, input, h("span", { class: "dim", text: T.place_count(p.count) }),
      p.registered ? null : h("span", { class: "tag ai", title: T.place_unlisted_hint, text: T.place_unlisted }), del);
  }

  function drawPanel() {
    if (!panel) return;
    const list = state.places || [];
    panel.replaceChildren(h("div", { class: "card places-card", onclick: (e) => e.stopPropagation() },
      h("div", { class: "places-head" }, h("strong", { text: `📍 ${T.place_manage}` }),
        h("button", { class: "icon-btn", "aria-label": "close", onclick: closePanel, text: "×" })),
      h("p", { class: "dim", text: T.place_manage_hint }),
      list.length ? h("ul", { class: "places-list" }, list.map(placeRow)) : h("p", { class: "dim", text: T.place_none }),
      addBox(async (name) => { if (await call("POST", { name })) drawPanel(); })));
  }

  function manage() {
    closePanel();
    panel = h("div", { class: "help places-dialog", onclick: closePanel });
    document.body.append(panel);
    drawPanel();
    const input = panel.querySelector(".place-add input");
    if (input) input.focus();
  }

  async function add(name) { return call("POST", { name }); }

  return { names, picker, manage, add };
})();
