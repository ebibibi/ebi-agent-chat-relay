// One card per piece of content on a page (#1564). Items sharing a `group` are drawn as one card
// with a row per target: rows an automation posted ("auto") show their outcome and link, rows a
// person finishes show a few plain buttons, usable on any row in any order (#1589):
//   ⬇ save the file (a normal download, its size shown) → 📋 copy the text → ↗ open the composer
//   → ✅ posted.
// The file is never read into the page's memory to hand it to a share sheet: a gigabyte video
// could not be prepared that way on a phone, and a person picks the saved file in the posting
// app instead. A card lists each file once at its top, saying which rows use it, and a row only
// points at it ("use video 1"): the same video is downloaded once, not once per row (#1601).
// Nothing here knows a network by name. Everything is drawn as text.
"use strict";

window.ConsolePageCards = (() => {
  // A file this large is the wrong thing to save on a phone (#1589).
  const LARGE_BYTES = 500 * 1024 * 1024;

  const actionable = (item) => item.state === "open" && (item.mode !== "auto" || item.stalled);
  const NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩";
  const savedKey = (url) => `page-saved:${url}`;
  const wasSaved = (url) => { try { return Boolean(localStorage.getItem(savedKey(url))); } catch { return false; } };
  const noteSaved = (url) => { try { localStorage.setItem(savedKey(url), String(Date.now())); } catch { /* blocked */ } };

  // The files a card's open rows use, each once (by URL: the server keeps one copy per content),
  // in the order the rows list them, with the targets that use each.
  function cardFiles(data, kit) {
    const files = new Map();
    for (const item of data.items.filter(actionable)) {
      for (const a of kit.storedFiles(item)) {
        if (!files.has(a.url)) files.set(a.url, { file: a, targets: [] });
        const target = item.target || kit.L().no_target;
        if (!files.get(a.url).targets.includes(target)) files.get(a.url).targets.push(target);
      }
    }
    return [...files.values()].map((entry, i, all) => ({ ...entry, mark: all.length > 1 ? NUMBERS[i] || `${i + 1}.` : "" }));
  }

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

  // The card's files at its top: one save button each, which rows use it, saved on this device.
  function fileList(files, kit, redraw) {
    if (!files.length) return null;
    const l = kit.L();
    return h("div", { class: "page-card-files", "aria-label": l.card_files },
      h("div", { class: "page-card-files-head dim", text: l.card_files }),
      files.map(({ file, targets, mark }) => {
        const bytes = size(file.size);
        const saved = wasSaved(file.url);
        const save = kit.button(`⬇ ${mark ? `${mark} ` : ""}${l.save_kind(file.kind, bytes)}`, () => {
          noteSaved(file.url);
          kit.save(file);
          redraw();
        }, "btn page-file-save" + (saved ? "" : " primary"));
        save.setAttribute("aria-label", `${l.save}: ${file.name}`);
        return h("div", { class: "page-card-file", "data-url": file.url },
          save,
          h("span", { class: "page-card-file-use", text: l.used_by(targets.join("・")) }),
          saved ? h("span", { class: "page-card-file-saved", text: `✓ ${l.saved_here}` }) : null,
          Number(file.size) >= LARGE_BYTES ? h("p", { class: "page-row-large", text: l.large(bytes) }) : null);
      }));
  }

  // On a row: which of the card's files to pick in the posting app (no save button of its own).
  function rowFiles(item, files, kit) {
    const l = kit.L();
    const marks = kit.storedFiles(item).map((a) => files.find((f) => f.file.url === a.url)).filter(Boolean)
      .map((f) => f.mark);
    if (!marks.length) return null;
    return h("span", { class: "page-row-uses dim", text: l.uses(marks.filter(Boolean).join("・")) });
  }

  function actions(item, kit, files) {
    const l = kit.L();
    const { button, link } = kit;
    if (kit.tab() !== "open") {
      return item.state === "open" ? [] : [button(l.reopen, () => kit.change(item, "open"), "btn page-reopen")];
    }
    if (!actionable(item)) return [];
    const labels = kit.labels();
    return [rowFiles(item, files, kit),
      item.body ? button(`📋 ${l.copy}`, () => kit.copy(item.body), "btn page-copy") : null,
      item.link ? button(`🔗 ${l.copy_link}`, () => kit.copy(item.link), "btn page-copy-link") : null,
      item.url ? link(`↗ ${labels.open}`, item.url, "btn page-open") : null,
      button(`✅ ${labels.done}`, () => kit.change(item, "done"), "btn primary page-done"),
      button(labels.skip, () => kit.change(item, "skipped"), "btn page-skip")];
  }

  function row(item, kit, files) {
    const l = kit.L();
    const cls = ["page-row", `is-${item.state}`, item.mode === "auto" ? "is-auto" : "",
      item.faded ? "faded" : ""].filter(Boolean).join(" ");
    const buttons = actions(item, kit, files).filter(Boolean);
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
    const files = todo ? cardFiles(data, kit) : [];
    return h("li", { class: "page-card", "data-group": data.group },
      h("div", { class: "page-card-head" },
        h("span", { class: "page-card-title", text: data.title || data.group }),
        h("span", { class: "dim page-card-when", text: kit.when(data.created_at) })),
      // How to post a row, said once per card instead of under every button.
      todo ? h("p", { class: "page-card-howto dim", text: l.howto }) : null,
      fileList(files, kit, kit.redraw),
      h("div", { class: "page-rows" }, rows.map((item) => row(item, kit, files))));
  }

  return { card, actionable, size };
})();
