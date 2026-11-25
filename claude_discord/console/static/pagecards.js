// One card per piece of content on a page (#1564). Items sharing a `group` are drawn as one card
// with a row per target: rows an automation posted ("auto") show their outcome and link, rows a
// person finishes show a few plain buttons, usable on any row in any order (#1589):
//   ⬇ save the file (a normal download, its size shown) → 📋 copy the text → ↗ open the composer
//   → ✅ posted.
// The file is never read into the page's memory to hand it to a share sheet: a gigabyte video
// could not be prepared that way on a phone, and a person picks the saved file in the posting
// app instead. Nothing here knows a network by name. Everything is drawn as text.
"use strict";

window.ConsolePageCards = (() => {
  // A file this large is the wrong thing to save on a phone (#1589).
  const LARGE_BYTES = 500 * 1024 * 1024;

  const actionable = (item) => item.state === "open" && (item.mode !== "auto" || item.stalled);

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

  // "23 MB" / "1.2 GB"; empty when the size is not known (items registered before #1589).
  function size(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return "";
    const mb = n / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
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

  // One save button per stored file, saying what it is and how big; a large one gets a warning.
  function saves(item, kit) {
    const l = kit.L();
    return kit.storedFiles(item).flatMap((a) => {
      const bytes = size(a.size);
      const save = kit.button(`⬇ ${l.save_kind(a.kind, bytes)}`, () => kit.save(a), "btn page-row-save");
      save.setAttribute("aria-label", `${l.save}: ${a.name}`);
      return Number(a.size) >= LARGE_BYTES ? [save, h("p", { class: "page-row-large", text: l.large(bytes) })] : [save];
    });
  }

  function actions(item, kit) {
    const l = kit.L();
    const { button, link } = kit;
    if (kit.tab() !== "open") {
      return item.state === "open" ? [] : [button(l.reopen, () => kit.change(item, "open"), "btn page-reopen")];
    }
    if (!actionable(item)) return [];
    const labels = kit.labels();
    return [...saves(item, kit),
      item.body ? button(`📋 ${l.copy}`, () => kit.copy(item.body), "btn page-copy") : null,
      item.link ? button(`🔗 ${l.copy_link}`, () => kit.copy(item.link), "btn page-copy-link") : null,
      item.url ? link(`↗ ${labels.open}`, item.url, "btn page-open") : null,
      button(`✅ ${labels.done}`, () => kit.change(item, "done"), "btn primary page-done"),
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

  function card(data, rows, kit) {
    const l = kit.L();
    const todo = kit.tab() === "open" && data.items.some(actionable);
    return h("li", { class: "page-card", "data-group": data.group },
      h("div", { class: "page-card-head" },
        h("span", { class: "page-card-title", text: data.title || data.group }),
        h("span", { class: "dim page-card-when", text: kit.when(data.created_at) })),
      // How to post a row, said once per card instead of under every button.
      todo ? h("p", { class: "page-card-howto dim", text: l.howto }) : null,
      h("div", { class: "page-rows" }, rows.map((item) => row(item, kit))));
  }

  return { card, actionable, size };
})();
