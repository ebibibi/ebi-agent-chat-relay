// Obsidian-style live preview for the note editor: Markdown is drawn as it will read
// (headings sized, emphasis applied, links as their text, task boxes clickable) and its
// markup comes back only on the lines the cursor is on. It only decorates: the document is
// the note's exact text, so saving never rewrites what the human did not touch.
import { RangeSetBuilder } from "@codemirror/state";
import { Decoration, ViewPlugin, WidgetType } from "@codemirror/view";
import { syntaxTree } from "@codemirror/language";

const hide = Decoration.replace({});
const line = (cls) => Decoration.line({ class: cls });
const mark = (cls) => Decoration.mark({ class: cls });
const MARKS = {
  StrongEmphasis: mark("cm-lp-strong"), Emphasis: mark("cm-lp-em"), Strikethrough: mark("cm-lp-strike"),
  InlineCode: mark("cm-lp-code"), Link: mark("cm-lp-link"),
};
const FRONT_MATTER = /^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/;
const WIKILINK = /!?\[\[([^\]\n]+)\]\]/g;

class Bullet extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement("span"); s.className = "cm-lp-bullet"; s.textContent = "•"; return s; }
}
class Rule extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement("span"); s.className = "cm-lp-hr"; return s; }
}
class TaskBox extends WidgetType {
  constructor(checked) { super(); this.checked = checked; }
  eq(other) { return other.checked === this.checked; }
  toDOM() {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "cm-lp-task";
    box.checked = this.checked;
    return box;
  }
  ignoreEvent() { return false; }
}

// An embedded image: shown in place of ![alt](src) / ![[name.png]], and under it while the
// cursor is on that line. *src* is null when the note's editor cannot serve the file.
class Picture extends WidgetType {
  constructor(src, alt) { super(); this.src = src; this.alt = alt; }
  eq(other) { return other.src === this.src && other.alt === this.alt; }
  toDOM(view) {
    const missing = () => {
      const s = document.createElement("span");
      s.className = "cm-lp-img-missing";
      s.textContent = `🖼 ${this.alt || this.src || ""}`;
      return s;
    };
    if (!this.src) return missing();
    const img = document.createElement("img");
    img.className = "cm-lp-img";
    img.alt = this.alt;
    img.src = this.src;
    img.addEventListener("load", () => view.requestMeasure());
    img.addEventListener("error", () => { img.replaceWith(missing()); view.requestMeasure(); });
    return img;
  }
  ignoreEvent() { return false; }
}

// Line numbers that hold a selection: their markup stays visible so it can be edited.
function activeLines(state) {
  const lines = new Set();
  for (const r of state.selection.ranges) {
    for (let n = state.doc.lineAt(r.from).number; n <= state.doc.lineAt(r.to).number; n += 1) lines.add(n);
  }
  return lines;
}

function build(view, imageUrl) {
  const { state } = view;
  const doc = state.doc;
  const active = activeLines(state);
  const fm = FRONT_MATTER.exec(doc.sliceString(0, Math.min(doc.length, 20000)));
  const fmEnd = fm ? fm[0].length : 0;
  const isActive = (pos) => active.has(doc.lineAt(pos).number);
  const decos = []; // [from, to, decoration]; sorted before building
  const resolve = (src, wikilink) => {
    try { return imageUrl ? imageUrl(src, wikilink) : null; } catch { return null; }
  };
  // Replace the markup with the picture, or show it after the markup on the cursor's line.
  const picture = (from, to, src, alt) => {
    const widget = new Picture(src, alt);
    if (isActive(from)) decos.push([to, to, Decoration.widget({ widget, side: 1 })]);
    else decos.push([from, to, Decoration.replace({ widget })]);
  };

  if (fmEnd) {
    for (let n = 1; n <= doc.lineAt(Math.max(0, fmEnd - 1)).number; n += 1) decos.push([doc.line(n).from, doc.line(n).from, line("cm-lp-fm")]);
  }
  for (const { from, to } of view.visibleRanges) {
    const text = doc.sliceString(from, to);
    const wikilinks = [...text.matchAll(WIKILINK)].map((m) => [from + m.index, from + m.index + m[0].length]);
    // Markdown reads "[[a]]" as brackets around a link-like "[a]": wikilinks are drawn below.
    const inWikilink = (pos) => wikilinks.some(([f, t]) => pos >= f && pos < t);
    syntaxTree(state).iterate({ from: Math.max(from, fmEnd), to, enter: (node) => {
      const name = node.name;
      if (node.from < fmEnd) return;
      if (inWikilink(node.from) && /^(Link|Image|LinkMark|URL|LinkTitle|LinkLabel)$/.test(name)) return false;
      const heading = /^ATXHeading(\d)$/.exec(name);
      if (heading) { decos.push([doc.lineAt(node.from).from, doc.lineAt(node.from).from, line(`cm-lp-h cm-lp-h${heading[1]}`)]); return; }
      if (name === "FencedCode" || name === "CodeBlock") {
        for (let n = doc.lineAt(node.from).number; n <= doc.lineAt(node.to).number; n += 1) decos.push([doc.line(n).from, doc.line(n).from, line("cm-lp-codeblock")]);
        return false;
      }
      if (name === "Blockquote") {
        for (let n = doc.lineAt(node.from).number; n <= doc.lineAt(node.to).number; n += 1) decos.push([doc.line(n).from, doc.line(n).from, line("cm-lp-quote")]);
        return;
      }
      if (name === "Image") {
        const url = node.node.getChild("URL");
        const label = /^!\[([^\]]*)\]/.exec(doc.sliceString(node.from, node.to));
        if (!url) return;
        picture(node.from, node.to, resolve(doc.sliceString(url.from, url.to), false), label ? label[1] : "");
        return isActive(node.from) ? undefined : false;
      }
      if (MARKS[name] && node.from < node.to) decos.push([node.from, node.to, MARKS[name]]);
      if (isActive(node.from)) return;
      switch (name) {
        case "HeaderMark": case "QuoteMark": {
          const end = doc.sliceString(node.to, node.to + 1) === " " ? node.to + 1 : node.to;
          decos.push([node.from, end, hide]);
          break;
        }
        case "EmphasisMark": case "StrikethroughMark":
          decos.push([node.from, node.to, hide]);
          break;
        case "CodeMark":
          if (node.node.parent && node.node.parent.name === "InlineCode") decos.push([node.from, node.to, hide]);
          break;
        case "LinkMark": case "URL": case "LinkTitle":
          if (node.node.parent && node.node.parent.name === "Link") decos.push([node.from, node.to, hide]);
          break;
        case "ListMark": {
          const task = node.node.nextSibling;
          const text = doc.sliceString(node.from, node.to);
          if (task && task.name === "Task") decos.push([node.from, node.to + 1, hide]);
          else if (/^[-*+]$/.test(text)) decos.push([node.from, node.to, Decoration.replace({ widget: new Bullet() })]);
          break;
        }
        case "TaskMarker":
          decos.push([node.from, node.to, Decoration.replace({ widget: new TaskBox(/x/i.test(doc.sliceString(node.from, node.to))) })]);
          break;
        case "HorizontalRule":
          decos.push([node.from, node.to, Decoration.replace({ widget: new Rule() })]);
          break;
        default:
      }
    } });
    // [[target|alias]]: the brackets (and the target when an alias names it) are hidden.
    for (const m of text.matchAll(WIKILINK)) {
      const start = from + m.index;
      if (start < fmEnd) continue;
      const end = start + m[0].length;
      const inner = start + m[0].indexOf("[[") + 2;
      const bar = m[1].indexOf("|");
      const target = m[1].split("|")[0].split("#")[0].trim();
      if (m[0].startsWith("!") && /\.(png|jpe?g|gif|webp)$/i.test(target)) {
        picture(start, end, resolve(target, true), bar >= 0 ? m[1].slice(bar + 1) : target);
        continue;
      }
      decos.push([inner, end - 2, mark("cm-lp-link")]);
      if (isActive(start)) continue;
      decos.push([start, inner, hide]);
      if (bar >= 0) decos.push([inner, inner + bar + 1, hide]);
      decos.push([end - 2, end, hide]);
    }
  }
  // One line decoration per line (classes merged), then ranges by start, as RangeSetBuilder
  // wants. Replacements may not overlap; marks may.
  const lines = new Map();
  const ranges = [];
  for (const [f, t, d] of decos) {
    if (d.spec.attributes === undefined && d.spec.class && f === t && d.startSide < -1e8) lines.set(f, `${lines.get(f) || ""} ${d.spec.class}`.trim());
    else ranges.push([f, t, d]);
  }
  for (const [f, cls] of lines) ranges.push([f, f, line(cls)]);
  ranges.sort((a, b) => a[0] - b[0] || a[2].startSide - b[2].startSide || a[1] - b[1]);
  const builder = new RangeSetBuilder();
  let covered = -1; // end of the last replaced range
  for (const [f, t, d] of ranges) {
    const replaces = d === hide || Boolean(d.spec.widget);
    if (replaces && f < covered) continue;
    builder.add(f, t, d);
    if (replaces) covered = t;
  }
  return builder.finish();
}

const makePlugin = (imageUrl) => ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = build(view, imageUrl); }
  update(u) {
    if (u.docChanged || u.selectionSet || u.viewportChanged || syntaxTree(u.startState) !== syntaxTree(u.state)) {
      this.decorations = build(u.view, imageUrl);
    }
  }
}, {
  decorations: (v) => v.decorations,
  eventHandlers: {
    // A click on a task box toggles "[ ]" ↔ "[x]" without moving the cursor onto the line.
    mousedown(e, view) {
      if (!(e.target instanceof HTMLInputElement) || !e.target.classList.contains("cm-lp-task")) return false;
      e.preventDefault();
      const pos = view.posAtDOM(e.target);
      const marker = view.state.doc.sliceString(pos, pos + 3);
      if (!/^\[[ xX]\]$/.test(marker)) return false;
      view.dispatch({ changes: { from: pos + 1, to: pos + 2, insert: marker[1] === " " ? "x" : " " } });
      return true;
    },
  },
});

// *imageUrl(src, wikilink)* turns an embed's target into a URL the page may load, or null.
export const livePreview = (imageUrl) => [makePlugin(imageUrl)];
