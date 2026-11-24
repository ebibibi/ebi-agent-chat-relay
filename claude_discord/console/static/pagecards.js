// One card per piece of content on a page (#1564). Items sharing a `group` are drawn as one card
// with a row per target: rows an automation posted ("auto") show their outcome and link, rows a
// person finishes show their buttons. "Next ▶" walks a person through the rows still to post:
// the text goes to the clipboard, the files to the posting app through the share sheet, and on
// return "Posted?" moves on. Nothing here knows a network by name. Everything is drawn as text.
"use strict";

window.ConsolePageCards = (() => {
  // Per card (by group): which row "Next" is on and whether it is waiting for "Posted?".
  const flows = new Map();

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
  function start(card, kit) {
    const first = actionableRows(card)[0];
    if (!first) return;
    flows.set(card.group, { key: first.key, stage: "go" });
    kit.redraw();
  }

  function stop(card, kit) { flows.delete(card.group); kit.redraw(); }

  // The row after *key* that still needs a person, wrapping round to rows skipped over earlier.
  function after(card, key) {
    const rows = actionableRows(card);
    const index = card.items.findIndex((i) => i.key === key);
    return rows.find((i) => card.items.indexOf(i) > index) || rows.find((i) => i.key !== key) || null;
  }

  function advance(card, key, kit) {
    const fresh = kit.cardOf(card.group) || card;
    const next = after(fresh, key);
    if (next) flows.set(card.group, { key: next.key, stage: "go" });
    else { flows.delete(card.group); kit.toast(kit.L().card_finished); }
    kit.redraw();
  }

  // Files are fetched before the tap: the share sheet must open within the tap's activation,
  // and a phone browser refuses it once a download has taken a few seconds.
  function prepare(flow, item, kit) {
    if (!flow.files && kit.canShare() && kit.hasFiles(item)) flow.files = kit.filesOf(item);
    return flow.files || null;
  }

  async function handOver(card, item, flow, kit) {
    const l = kit.L();
    const files = flow.ready || [];
    // Both calls start inside the tap: the clipboard first, then the share sheet or the link.
    const copied = item.body ? navigator.clipboard.writeText(item.body) : Promise.resolve();
    let shared = Promise.resolve();
    if (files.length && navigator.canShare({ files })) shared = navigator.share({ files, text: item.body || item.title });
    else if (item.url) window.open(item.url, "_blank", "noopener,noreferrer");
    else if (kit.canShare() && item.body) shared = navigator.share({ text: item.body });
    try {
      await copied;
      if (item.body) kit.toast(l.copied);
    } catch (err) { kit.toast(err.message); }
    try { await shared; }
    catch (err) { if (err.name === "AbortError") return; kit.toast(err.message); }
    flows.set(card.group, { ...flow, stage: "ask" });
    kit.redraw();
  }

  async function answer(card, item, state, kit) {
    const updated = await kit.change(item, state);
    if (updated) advance(card, item.key, kit);
  }

  function sheet(card, kit) {
    const flow = flows.get(card.group);
    const item = flow && card.items.find((i) => i.key === flow.key && actionable(i));
    if (!flow) return null;
    if (!item) { flows.delete(card.group); return null; }
    const l = kit.L();
    const rows = actionableRows(card);
    const step = h("div", { class: "page-next-step", text: l.step(rows.indexOf(item) + 1, rows.length, item.target || l.no_target) });
    if (flow.stage === "ask") {
      return h("div", { class: "page-next-sheet", role: "group", "aria-label": l.next },
        step, h("div", { class: "page-next-question", text: l.posted_question(item.target || l.no_target) }),
        h("div", { class: "page-next-actions" },
          kit.button(`✅ ${l.yes_next}`, () => answer(card, item, "done", kit), "btn primary page-next-yes"),
          kit.button(l.again, () => { flows.set(card.group, { ...flow, stage: "go" }); kit.redraw(); }, "btn page-next-again"),
          kit.button(l.skip, () => answer(card, item, "skipped", kit), "btn page-next-skip")));
    }
    const pending = prepare(flow, item, kit);
    const go = kit.button(pending && !flow.ready ? l.preparing : l.hand_over(Boolean(pending), Boolean(item.url)),
      () => handOver(card, item, flow, kit), "btn primary page-next-go");
    if (pending && !flow.ready) {
      go.disabled = true;
      pending.then((files) => { flow.ready = files; go.disabled = false; go.textContent = l.hand_over(true, Boolean(item.url)); })
        .catch((err) => { flow.ready = []; go.disabled = false; go.textContent = l.hand_over(false, Boolean(item.url)); kit.toast(err.message); });
    }
    return h("div", { class: "page-next-sheet", role: "group", "aria-label": l.next },
      step,
      item.body ? h("div", { class: "page-next-preview", text: item.body }) : null,
      h("div", { class: "page-next-actions" },
        go,
        kit.button(l.later, () => advance(card, item.key, kit), "btn page-next-later"),
        kit.button(l.stop, () => stop(card, kit), "btn page-next-stop")));
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
          kit.button(`▶ ${l.next} (${left})`, () => start(data, kit), "btn primary page-next"))
        : null));
  }

  return { card, actionable, reset: () => flows.clear() };
})();
