// One card per piece of content on a page (#1564). Items sharing a `group` are drawn as one card
// with a row per target: rows an automation posted ("auto") show their outcome and link, rows a
// person finishes show their buttons. "Next ▶" walks a person through the rows still to post:
// the text goes to the clipboard, the files to the posting app through the share sheet (or, on
// a device without one, are offered for saving), and on return "Posted?" records it and moves
// on. Every step says what to do and what to press (#1574), and the walk survives a reload.
// Nothing here knows a network by name. Everything is drawn as text.
"use strict";

window.ConsolePageCards = (() => {
  // Per card (by group): which row "Next" is on and whether it is waiting for "Posted?".
  const flows = new Map();
  let slug = ""; // the page the walks belong to

  const actionable = (item) => item.state === "open" && (item.mode !== "auto" || item.stalled);
  const actionableRows = (card) => card.items.filter(actionable);

  function mark(item) {
    if (item.state === "done") return "✅";
    if (item.state === "skipped") return "⏭";
    if (item.mode === "auto" && !item.stalled) return "⏳";
    return item.error || item.stalled ? "⚠️" : "⬜";
  }

  function status(item, kit) {
    const l = kit.L();
    if (item.state === "done") return item.resolved_by === "person" ? l.posted_by_hand : l.posted_auto;
    if (item.state === "skipped") return item.resolved_by === "expired" ? l.expired : l.skip;
    if (item.mode === "auto" && !item.stalled) return l.auto_pending;
    if (item.stalled) return l.auto_silent;
    if (item.error) return l.auto_failed(item.error);
    return l.not_posted;
  }

  function detail(item, kit) {
    const l = kit.L();
    const files = item.attachments || [];
    if (!item.body && !item.link && !files.length) return null;
    return h("details", { class: "page-row-detail" },
      h("summary", {}, h("span", { text: files.length ? l.text_and_files : l.text }), kit.counter(item)),
      item.body ? h("div", { class: "page-item-body", text: item.body }) : null,
      item.link ? h("div", { class: "page-item-link" },
        h("span", { class: "dim", text: `🔗 ${l.link}: ` }), kit.link(item.link, item.link, "page-link")) : null,
      files.length ? h("div", { class: "page-files", "aria-label": l.attachments }, files.map(kit.attachment)) : null);
  }

  function actions(item, kit) {
    const l = kit.L();
    const { button, link } = kit;
    if (kit.tab() !== "open") {
      return item.state === "open" ? [] : [button(l.reopen, () => kit.change(item, "open"), "btn page-reopen")];
    }
    if (!actionable(item)) return [];
    const labels = kit.labels();
    return [item.url ? link(labels.open, item.url, "btn page-open") : null,
      item.body ? button(l.copy, () => kit.copy(item.body), "btn page-copy") : null,
      item.link ? button(`🔗 ${l.copy_link}`, () => kit.copy(item.link), "btn page-copy-link") : null,
      button(labels.done, () => kit.change(item, "done"), "btn page-done"),
      button(labels.skip, () => kit.change(item, "skipped"), "btn page-skip")];
  }

  function row(item, kit) {
    const l = kit.L();
    const cls = ["page-row", `is-${item.state}`, item.mode === "auto" ? "is-auto" : "",
      item.faded ? "faded" : ""].filter(Boolean).join(" ");
    const buttons = actions(item, kit).filter(Boolean);
    return h("div", { class: cls, "data-key": item.key },
      h("div", { class: "page-row-main" },
        h("span", { class: "page-row-mark", "aria-hidden": "true", text: mark(item) }),
        h("span", { class: "page-target", text: item.target || l.no_target }),
        h("span", { class: "page-row-status", text: status(item, kit) }),
        item.result_url ? kit.link(`${l.result} ↗`, item.result_url, "page-row-result") : null),
      buttons.length ? h("div", { class: "page-item-actions page-row-actions" }, buttons) : null,
      detail(item, kit));
  }

  // -- "Next ▶" -------------------------------------------------------------------------------
  // The walk is kept in localStorage per page, so a phone that reloads the Console while the
  // person is in the posting app comes back to "Posted on X?" instead of a fresh card (#1574).
  // Only the position is kept; fetched files stay in memory and are fetched again if needed.
  const KEEP_MS = 12 * 60 * 60 * 1000;
  const storeKey = (slug) => `page-flow:${slug}`;

  function stored(slug) {
    try {
      const all = JSON.parse(localStorage.getItem(storeKey(slug)) || "{}");
      const now = Date.now();
      return Object.fromEntries(Object.entries(all && typeof all === "object" ? all : {})
        .filter(([, f]) => f && typeof f.key === "string" && now - (Number(f.at) || 0) < KEEP_MS));
    } catch { return {}; }
  }

  function persist() {
    if (!slug) return;
    const all = Object.fromEntries([...flows].map(([group, f]) => [group, { key: f.key, stage: f.stage, at: f.at }]));
    try {
      if (Object.keys(all).length) localStorage.setItem(storeKey(slug), JSON.stringify(all));
      else localStorage.removeItem(storeKey(slug));
    } catch { /* storage full or blocked: the walk still works until a reload */ }
  }

  function put(group, flow) { flows.set(group, { ...flow, at: Date.now() }); persist(); }
  function drop(group) { flows.delete(group); persist(); }

  // Restores a page's walks; a step that was handed over comes back as the question.
  function reset(page) {
    flows.clear();
    slug = page || "";
    if (!slug) return;
    for (const [group, f] of Object.entries(stored(slug))) {
      flows.set(group, { key: f.key, stage: f.stage === "ask" ? "ask" : "go", at: f.at, resumed: f.stage === "ask" });
    }
  }

  // Whether a page has a step that was handed over and not yet answered.
  const pending = (page) => Object.values(stored(page)).some((f) => f.stage === "ask");

  function start(card, kit) {
    const first = actionableRows(card)[0];
    if (!first) return;
    put(card.group, { key: first.key, stage: "go" });
    kit.redraw();
  }

  function stop(card, kit) { drop(card.group); kit.redraw(); }

  // The row after *key* that still needs a person, wrapping round to rows skipped over earlier.
  function after(card, key) {
    const rows = actionableRows(card);
    const index = card.items.findIndex((i) => i.key === key);
    return rows.find((i) => card.items.indexOf(i) > index) || rows.find((i) => i.key !== key) || null;
  }

  function advance(card, key, kit) {
    const fresh = kit.cardOf(card.group) || card;
    const next = after(fresh, key);
    if (next) put(card.group, { key: next.key, stage: "go" });
    // "Later" on the last row left: stay on it rather than claim the card is finished.
    else if (actionableRows(fresh).some((i) => i.key === key)) kit.toast(kit.L().only_one);
    else { drop(card.group); kit.toast(kit.L().card_finished); }
    kit.redraw();
  }

  // Files are fetched before the tap: the share sheet must open within the tap's activation,
  // and a phone browser refuses it once a download has taken a few seconds.
  function prepare(flow, item, kit) {
    if (!flow.files && kit.canShare() && kit.hasFiles(item)) flow.files = kit.filesOf(item);
    return flow.files || null;
  }

  // How this step reaches the posting app on this device: "share" (the files go through the
  // share sheet), "save" (the files must be saved and attached by hand), or "text" (no files).
  function route(flow, item, kit) {
    if (!kit.hasFiles(item)) return "text";
    if (!kit.canShare()) return "save";
    if (!flow.ready) return "share";
    return flow.ready.length && navigator.canShare({ files: flow.ready }) ? "share" : "save";
  }

  async function handOver(card, item, flow, kit) {
    const l = kit.L();
    const files = route(flow, item, kit) === "share" ? flow.ready || [] : [];
    // The question is stored before anything leaves the tab: a phone may reload the Console
    // while the person is in the posting app, and the share promise never settles then.
    put(card.group, { ...flow, stage: "ask", resumed: false });
    flows.set(card.group, { ...flows.get(card.group), stage: "go" });
    // Both calls start inside the tap: the clipboard first, then the share sheet or the link.
    const copied = item.body ? navigator.clipboard.writeText(item.body) : Promise.resolve();
    let shared = Promise.resolve();
    if (files.length) shared = navigator.share({ files, text: item.body || item.title });
    else if (item.url) window.open(item.url, "_blank", "noopener,noreferrer");
    else if (kit.canShare() && item.body && !kit.hasFiles(item)) shared = navigator.share({ text: item.body });
    try {
      await copied;
      if (item.body) kit.toast(l.copied);
    } catch (err) { kit.toast(err.message); }
    try { await shared; }
    catch (err) {
      if (err.name === "AbortError") { put(card.group, { ...flow, stage: "go" }); return; }
      kit.toast(err.message);
    }
    put(card.group, { ...flow, stage: "ask", resumed: false });
    kit.redraw();
  }

  async function answer(card, item, state, kit) {
    const updated = await kit.change(item, state);
    if (updated) advance(card, item.key, kit);
  }

  const line = (cls, text) => h("div", { class: cls, text });

  function askSheet(card, item, flow, step, kit) {
    const l = kit.L();
    const target = item.target || l.no_target;
    const box = h("div", { class: "page-next-sheet", role: "group", "aria-label": l.next },
      step,
      flow.resumed ? line("page-next-resumed", l.resumed(target)) : null,
      line("page-next-question", l.posted_question(target)),
      line("page-next-hint dim", l.ask_hint),
      h("div", { class: "page-next-actions" },
        kit.button(`✅ ${l.yes_next}`, () => answer(card, item, "done", kit), "btn primary page-next-yes"),
        kit.button(l.again, () => { put(card.group, { ...flow, stage: "go", resumed: false }); kit.redraw(); }, "btn page-next-again"),
        kit.button(l.skip_this, () => answer(card, item, "skipped", kit), "btn page-next-skip")));
    // Back from the posting app, the question may sit below a long list: bring it into view once.
    if (flow.resumed && !flow.shown) {
      flow.shown = true;
      requestAnimationFrame(() => box.scrollIntoView({ block: "center" }));
    }
    return box;
  }

  function goSheet(card, item, flow, step, kit) {
    const l = kit.L();
    const target = item.target || l.no_target;
    const pending = prepare(flow, item, kit);
    const how = () => l.how(route(flow, item, kit), target, Boolean(item.url));
    const label = () => l.hand_over(route(flow, item, kit) === "share", Boolean(item.url));
    const guide = line("page-next-how", how());
    const files = h("div", { class: "page-files page-next-files", "aria-label": l.attachments });
    const showFiles = () => {
      const save = route(flow, item, kit) === "save";
      files.hidden = !save;
      files.replaceChildren(...(save ? kit.storedFiles(item).map((a) => kit.button(`⬇ ${l.save_file(a.name)}`,
        () => kit.save(a), "btn page-next-save")) : []));
    };
    showFiles();
    const go = kit.button(pending && !flow.ready ? l.preparing : label(),
      () => handOver(card, item, flows.get(card.group) || flow, kit), "btn primary page-next-go");
    if (pending && !flow.ready) {
      go.disabled = true;
      const settle = () => { go.disabled = false; go.textContent = label(); guide.textContent = how(); showFiles(); };
      pending.then((ready) => { flow.ready = ready; settle(); })
        .catch((err) => { flow.ready = []; settle(); kit.toast(err.message); });
    }
    return h("div", { class: "page-next-sheet", role: "group", "aria-label": l.next },
      step,
      guide,
      files,
      item.body ? h("div", { class: "page-next-preview", text: item.body }) : null,
      h("div", { class: "page-next-actions" },
        go,
        kit.button(l.later, () => advance(card, item.key, kit), "btn page-next-later"),
        kit.button(l.stop, () => stop(card, kit), "btn page-next-stop")),
      line("page-next-hint dim", l.go_hint));
  }

  function sheet(card, kit) {
    const flow = flows.get(card.group);
    if (!flow) return null;
    const item = card.items.find((i) => i.key === flow.key && actionable(i));
    // The row was finished elsewhere (another device, the row's own buttons): pick the next one.
    if (!item) {
      const next = actionableRows(card)[0];
      if (next) flows.set(card.group, { key: next.key, stage: "go", at: Date.now() });
      else flows.delete(card.group);
      persist();
      return next ? sheet(card, kit) : null;
    }
    const l = kit.L();
    const rows = actionableRows(card);
    const step = line("page-next-step", l.step(rows.indexOf(item) + 1, rows.length, item.target || l.no_target));
    return flow.stage === "ask" ? askSheet(card, item, flow, step, kit) : goSheet(card, item, flow, step, kit);
  }

  function card(data, rows, kit) {
    const l = kit.L();
    const left = actionableRows(data).length;
    const flow = kit.tab() === "open" ? sheet(data, kit) : null;
    return h("li", { class: "page-card", "data-group": data.group },
      h("div", { class: "page-card-head" },
        h("span", { class: "page-card-title", text: data.title || data.group }),
        h("span", { class: "dim page-card-when", text: kit.when(data.created_at) })),
      h("div", { class: "page-rows" }, rows.map((item) => row(item, kit))),
      flow || (kit.tab() === "open" && left
        ? h("div", { class: "page-card-foot" },
          kit.button(`▶ ${l.start_walk(left)}`, () => start(data, kit), "btn primary page-next"),
          line("page-next-hint dim", l.start_hint))
        : null));
  }

  return { card, actionable, reset, pending };
})();
