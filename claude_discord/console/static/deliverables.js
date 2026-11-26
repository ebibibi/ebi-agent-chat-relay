// "📦 Deliverables" in the situation panel: everything a thread family produced, newest first,
// with the thread each came from (#1537). Files the relay's bot attached are collected by the
// server; PRs, releases and notes are what the family's agents declared in their records.
// Images preview in place, videos play in place (fetched only when asked for: a phone should
// not download every clip on open), text documents open in the preview, anything downloads.
// Replaced versions fold under their own heading. The section node is rebuilt only when its
// contents change, so a playing video survives the panel's periodic redraw.
// Loaded after preview.js and live.js and before situation.js, which calls section().
"use strict";
window.ConsoleDeliverables = (() => {
  const REFRESH_MS = 60_000;
  const VISIBLE = 3; // per group; the rest of a group folds
  // Groups in the order the person looks for them; links (PRs, releases) fold by default (#1548).
  const GROUPS = [
    { key: "video", kinds: ["video"] },
    { key: "image", kinds: ["image"] },
    { key: "doc", kinds: ["note", "doc", "file"] },
    { key: "link", kinds: ["pr", "release", "link"], folded: true },
  ];
  const STRINGS = {
    en: { files: "🗂️ Files", head: "📦 Deliverables", none: "Nothing yet", more: (n) => `${n} more`, replaced: (n) => `Replaced (${n})`,
      play: "Play", preview: "Preview", download: "Download", open: "Open", missing: "file not found in the thread",
      unread: (n) => `${n} thread(s) could not be read`, failed: "Could not load",
      groups: { video: "🎬 Videos", image: "🖼️ Images", doc: "📄 Documents and notes", link: "🔗 PRs and links" } },
    ja: { files: "🗂️ ファイル", head: "📦 成果物", none: "まだありません", more: (n) => `ほか ${n}件`, replaced: (n) => `置き換え済み（${n}件）`,
      play: "再生", preview: "プレビュー", download: "ダウンロード", open: "開く", missing: "スレッドにファイルが見つかりません",
      unread: (n) => `${n}件のスレッドを読めませんでした`, failed: "読み込めませんでした",
      groups: { video: "🎬 動画", image: "🖼️ 画像", doc: "📄 文書・ノート", link: "🔗 PR・リンク" } },
  };
  const L = () => STRINGS[document.documentElement.lang === "ja" ? "ja" : "en"];
  const ICON = { pr: "🔀", release: "🏷️", note: "📝", doc: "📄", video: "🎬", image: "🖼️", link: "🔗", file: "📎" };

  const cache = new Map(); // item id -> {at, data}
  const pending = new Set();
  const nodes = new Map(); // item id -> {key, node}
  let onChange = () => {};

  async function load(id) {
    if (pending.has(id)) return;
    const hit = cache.get(id);
    if (hit && Date.now() - hit.at < REFRESH_MS) return;
    pending.add(id);
    try {
      const data = await api(`/items/${encodeURIComponent(id)}/deliverables`);
      const before = JSON.stringify(cache.get(id)?.data);
      cache.set(id, { at: Date.now(), data });
      if (JSON.stringify(data) !== before) onChange();
    } catch {
      cache.set(id, { at: Date.now(), data: cache.get(id)?.data || null });
    } finally {
      pending.delete(id);
    }
  }

  const entries = (id) => cache.get(id)?.data?.deliverables || [];

  const ext = (name) => String(name || "").toLowerCase().split(/[?#]/)[0].split(".").pop();
  function kindOf(e) {
    if (e.kind) return e.kind;
    if (e.source !== "file") return /\/pull\/\d+/.test(e.url || "") ? "pr" : (e.url || "").startsWith("/#note/") ? "note" : "link";
    const type = e.content_type || "";
    if (type.startsWith("video/") || ["mp4", "mov", "webm", "m4v"].includes(ext(e.name))) return "video";
    if (type.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "bmp", "avif"].includes(ext(e.name))) return "image";
    return "file";
  }
  const own = (url) => { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } };
  const blob = (url) => window.ConsolePreview.fetchBlob(url);
  const dataUrl = (b) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(b);
  });
  // Discord's CDN is an allowed image source; the Console's own files need its credentials.
  const imageSrc = (e) => own(e.url) ? blob(e.url).then(dataUrl) : Promise.resolve(e.url);

  async function save(e) {
    if (!own(e.url)) { window.open(e.url, "_blank", "noopener,noreferrer"); return; }
    try {
      const href = URL.createObjectURL(await blob(e.url));
      const link = h("a", { href, download: e.name });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (err) { toast(`${L().failed}: ${err.message}`); }
  }

  function thumb(e) {
    const tile = h("button", { type: "button", class: "dlv-thumb", title: e.name, "aria-label": e.name },
      h("span", { text: ICON.image }));
    imageSrc(e).then((src) => tile.replaceChildren(h("img", { src, alt: e.name, loading: "lazy" })))
      .catch(() => tile.replaceChildren(h("span", { text: "⚠️" })));
    tile.addEventListener("click", () => window.ConsoleLightbox && window.ConsoleLightbox(e.name, imageSrc(e), () => save(e)));
    return tile;
  }

  function player(e) {
    const button = h("button", { type: "button", class: "btn dlv-play", text: `▶ ${L().play}` });
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const src = URL.createObjectURL(await blob(e.url));
        const video = h("video", { class: "dlv-video", src, controls: "", playsinline: "", preload: "metadata" });
        button.replaceWith(video);
        video.play().catch(() => { /* a click is not always enough to autoplay */ });
      } catch (err) { button.disabled = false; toast(`${L().failed}: ${err.message}`); }
    });
    return button;
  }

  function actions(e, kind) {
    const l = L();
    if (e.source !== "file") {
      if (!e.url) return [];
      const inside = e.url.startsWith("/#");
      return [h("a", { class: "btn dlv-act", href: e.url, ...(inside ? {} : { target: "_blank", rel: "noopener noreferrer" }), text: `↗ ${l.open}` })];
    }
    const P = window.ConsolePreview;
    const out = [];
    if (kind === "video") out.push(player(e));
    if (kind !== "video" && kind !== "image" && P && P.kind(e.name)) {
      out.push(h("button", { type: "button", class: "btn dlv-act", text: `👁 ${l.preview}`,
        onclick: () => P.open(e.name, () => blob(e.url), () => save(e), { url: e.url, attachments: [] }) }));
    }
    out.push(h("button", { type: "button", class: "btn dlv-act", text: `⬇ ${l.download}`, onclick: () => save(e) }));
    return out;
  }

  function row(e, threadOf) {
    const l = L();
    const kind = kindOf(e);
    const name = e.title || e.name || e.url;
    const thread = threadOf(e.thread_id);
    const meta = [e.at ? shortDate(e.at) : "", e.title && e.name && e.title !== e.name ? e.name : ""].filter(Boolean).join(" · ");
    return h("li", { class: "sit-row dlv-row" + (e.superseded ? " superseded" : ""), "data-kind": kind },
      kind === "image" && e.source === "file" ? thumb(e) : null,
      h("div", { class: "dlv-main" },
        h("div", { class: "dlv-title", text: `${ICON[kind] || ICON.file} ${name}` }),
        e.note ? h("div", { class: "dlv-note", text: e.note }) : null,
        e.missing ? h("div", { class: "dlv-note sit-unexplained", text: l.missing }) : null,
        h("div", { class: "dim sit-detail dlv-meta" },
          thread ? h("button", { type: "button", class: "linkish dlv-thread", text: thread.label,
            onclick: () => openItem(thread.id) }) : null,
          meta ? h("span", { text: thread ? ` · ${meta}` : meta }) : null),
        h("div", { class: "dlv-actions" }, ...actions(e, kind))));
  }

  // The section for the panel of *id*; *threadOf* maps a thread id to {id, label} or null.
  const time = (e) => Date.parse(e.at) || 0;
  const groupOf = (e) => GROUPS.find((g) => g.kinds.includes(kindOf(e))) || GROUPS[2];

  // The section for the panel of *id*; *threadOf* maps a thread id to {id, label} or null.
  // Grouped by kind, newest first in each group; replaced versions fold at the end.
  function section(id, threadOf) {
    const l = L();
    const data = cache.get(id)?.data || null;
    const list = [...entries(id)].sort((a, b) => time(b) - time(a));
    const key = JSON.stringify([LANG, data, list.map((e) => threadOf(e.thread_id)?.label)]);
    const kept = nodes.get(id);
    if (kept && kept.key === key) return kept.node;
    const current = list.filter((e) => !e.superseded);
    const replaced = list.filter((e) => e.superseded);
    const rows = (items) => items.map((e) => row(e, threadOf));
    const fold = (summary, items, cls = "") => items.length ? h("li", { class: "sit-row" },
      h("details", { class: `dlv-fold ${cls}`.trim() }, h("summary", { class: "dim", text: summary }),
        h("ul", { class: "sit-list" }, ...rows(items)))) : null;
    const groups = GROUPS.map((g) => {
      const items = current.filter((e) => groupOf(e) === g);
      if (!items.length) return null;
      const label = `${l.groups[g.key]} (${items.length})`;
      if (g.folded) return h("li", { class: "dlv-group", "data-group": g.key },
        h("details", { class: "dlv-fold" }, h("summary", { class: "dlv-group-head", text: label }),
          h("ul", { class: "sit-list" }, ...rows(items))));
      return h("li", { class: "dlv-group", "data-group": g.key },
        h("div", { class: "dlv-group-head", text: label }),
        h("ul", { class: "sit-list" }, ...rows(items.slice(0, VISIBLE)),
          fold(l.more(items.length - VISIBLE), items.slice(VISIBLE))));
    });
    const unread = (data?.unread || []).length;
    const node = h("section", { class: "sit-section deliverables" },
      h("h3", { class: "sit-head", text: list.length ? `${l.head} (${current.length})` : l.head }),
      list.length ? h("ul", { class: "sit-list" }, ...groups, fold(l.replaced(replaced.length), replaced))
        : h("div", { class: "dim sit-empty", text: l.none }),
      unread ? h("div", { class: "dim sit-detail", text: l.unread(unread) }) : null);
    nodes.set(id, { key, node });
    return node;
  }

  // Only the files the bot attached in the family's threads, newest first, in one flat list.
  function files(id, threadOf) {
    const l = L();
    const list = entries(id).filter((e) => e.source === "file").sort((a, b) => time(b) - time(a));
    const key = JSON.stringify([LANG, list, list.map((e) => threadOf(e.thread_id)?.label)]);
    const kept = nodes.get(`files:${id}`);
    if (kept && kept.key === key) return kept.node;
    const node = h("section", { class: "sit-section dlv-files" },
      list.length ? h("ul", { class: "sit-list" }, ...list.map((e) => row(e, threadOf)))
        : h("div", { class: "dim sit-empty", text: l.none }));
    nodes.set(`files:${id}`, { key, node });
    return node;
  }
  const fileCount = (id) => entries(id).filter((e) => e.source === "file").length;

  return { load, entries, section, files, fileCount, label: () => L().files, set onChange(fn) { onChange = fn; } };
})();
