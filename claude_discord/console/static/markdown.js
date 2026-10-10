// Discord-flavoured Markdown → DOM nodes for the Relay Console.
// Builds elements with createElement/textContent only — never innerHTML — so a
// message can format itself but cannot inject markup. Links are http(s), plus /console/api/files/ downloads.
"use strict";

const MD = (() => {
  const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
  const text = (s) => document.createTextNode(s);

  // ------------------------------------------------------------ inline
  // One alternation per construct; the first group that matched decides.
  const INLINE = new RegExp([
    /``([^`]+?)``|`([^`\n]+)`/.source,                                    // 1,2 code
    // The one same-origin target: a delivered file (live.js downloads it with the token).
    /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|\/console\/api\/files\/[^\s)]+)\)/.source, // 3,4 [text](url)
    /<(https?:\/\/[^\s>]+)>/.source,                                       // 5 <url>
    /(https?:\/\/[^\s<]*[^\s<.,:;"')\]!?*_~|])/.source,                   // 6 bare url
    /\*\*\*([^*][\s\S]*?)\*\*\*/.source,                                   // 7 bold italic
    /\*\*([^*][\s\S]*?)\*\*/.source,                                       // 8 bold
    /__([^_][\s\S]*?)__/.source,                                           // 9 underline
    /~~([\s\S]+?)~~/.source,                                               // 10 strike
    /\|\|([\s\S]+?)\|\|/.source,                                           // 11 spoiler
    /\*([^\s*](?:[\s\S]*?[^\s*])?)\*/.source,                              // 12 italic
    /(?<![\w])_([^\s_](?:[\s\S]*?[^\s_])?)_(?![\w])/.source,               // 13 italic
    /<@[!&]?(\d+)>/.source,                                                // 14 mention
    /<#(\d+)>/.source,                                                     // 15 channel
    /<a?:(\w+):\d+>/.source,                                               // 16 custom emoji
  ].join("|"), "g");

  function link(href, label) {
    const a = el("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.append(...(typeof label === "string" ? [text(label)] : label));
    return a;
  }
  function wrap(tag, inner, cls) { const e = el(tag, cls); e.append(...inline(inner)); return e; }

  function inline(src) {
    const out = [];
    let last = 0;
    // matchAll scans a copy of the regex: the recursive calls below must not move this one.
    for (const m of src.matchAll(INLINE)) {
      if (m.index > last) out.push(text(src.slice(last, m.index)));
      last = m.index + m[0].length;
      if (m[1] !== undefined || m[2] !== undefined) { const c = el("code"); c.textContent = m[1] ?? m[2]; out.push(c); }
      else if (m[3] !== undefined) out.push(link(m[4], inline(m[3])));
      else if (m[5] !== undefined) out.push(link(m[5], m[5]));
      else if (m[6] !== undefined) out.push(link(m[6], m[6]));
      else if (m[7] !== undefined) { const b = el("strong"); b.append(wrap("em", m[7])); out.push(b); }
      else if (m[8] !== undefined) out.push(wrap("strong", m[8]));
      else if (m[9] !== undefined) out.push(wrap("u", m[9]));
      else if (m[10] !== undefined) out.push(wrap("s", m[10]));
      else if (m[11] !== undefined) {
        const s = wrap("span", m[11], "spoiler");
        s.addEventListener("click", () => s.classList.add("shown"));
        out.push(s);
      }
      else if (m[12] !== undefined) out.push(wrap("em", m[12]));
      else if (m[13] !== undefined) out.push(wrap("em", m[13]));
      else if (m[14] !== undefined) { const s = el("span", "mention"); s.textContent = "@" + m[14].slice(-4); out.push(s); }
      else if (m[15] !== undefined) { const s = el("span", "mention"); s.textContent = "#" + m[15].slice(-4); out.push(s); }
      else if (m[16] !== undefined) out.push(text(`:${m[16]}:`));
    }
    if (last < src.length) out.push(text(src.slice(last)));
    return out;
  }

  // ------------------------------------------------------------ blocks
  const FENCE = /^\s*```(\S*)\s*$/;
  const HEADING = /^(#{1,3})\s+(.*)$/;
  const SUBTEXT = /^-#\s+(.*)$/;
  const QUOTE = /^>\s?(.*)$/;
  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const startsBlock = (line) => FENCE.test(line) || HEADING.test(line) || SUBTEXT.test(line) ||
    QUOTE.test(line) || LIST.test(line);

  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  function table(lines) {
    const t = el("table");
    const head = el("thead");
    const tr = el("tr");
    for (const c of cells(lines[0])) tr.append(wrap("th", c));
    head.append(tr);
    const body = el("tbody");
    for (const line of lines.slice(2)) {
      const row = el("tr");
      for (const c of cells(line)) row.append(wrap("td", c));
      body.append(row);
    }
    t.append(head, body);
    const box = el("div", "table-wrap");
    box.append(t);
    return box;
  }

  function list(lines) {
    // One open list per indentation level; a deeper item nests in the last <li>.
    const roots = [];
    const stack = [];
    for (const line of lines) {
      const [, ws, marker, body] = line.match(LIST);
      const indent = ws.replace(/\t/g, "  ").length;
      const ordered = /\d/.test(marker);
      while (stack.length && indent < stack[stack.length - 1].indent) stack.pop();
      // "- a" followed by "1. b" at the same depth is a new list, not more of the old one.
      if (stack.length && indent === stack[stack.length - 1].indent && stack[stack.length - 1].ordered !== ordered) stack.pop();
      let cur = stack[stack.length - 1];
      if (!cur || indent > cur.indent) {
        const node = el(ordered ? "ol" : "ul");
        if (ordered) node.start = parseInt(marker, 10);
        if (cur) (cur.node.lastElementChild || cur.node).append(node); else roots.push(node);
        cur = { indent, node, ordered };
        stack.push(cur);
      }
      cur.node.append(wrap("li", body));
    }
    return roots;
  }

  function blocks(src) {
    const lines = src.replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = line.match(FENCE))) {
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;
        const pre = el("pre");
        const code = el("code");
        if (m[1]) code.dataset.lang = m[1];
        code.textContent = body.join("\n");
        pre.append(code);
        out.push(pre);
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        const rows = [line, lines[i + 1]];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(lines[i++]);
        out.push(table(rows));
        continue;
      }
      if ((m = line.match(HEADING))) { out.push(wrap(`h${m[1].length + 2}`, m[2])); i++; continue; }
      if ((m = line.match(SUBTEXT))) { out.push(wrap("div", m[1], "subtext")); i++; continue; }
      if (/^>>>\s?/.test(line)) {
        const q = el("blockquote");
        q.append(...blocks([line.replace(/^>>>\s?/, ""), ...lines.slice(i + 1)].join("\n")));
        out.push(q);
        break;
      }
      if (QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && (m = lines[i].match(QUOTE))) { body.push(m[1]); i++; }
        const q = el("blockquote");
        q.append(...blocks(body.join("\n")));
        out.push(q);
        continue;
      }
      if (LIST.test(line)) {
        const items = [];
        while (i < lines.length && LIST.test(lines[i])) items.push(lines[i++]);
        out.push(...list(items));
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i]))) para.push(lines[i++]);
      const p = el("p");
      para.forEach((l, n) => { if (n) p.append(el("br")); p.append(...inline(l)); });
      out.push(p);
    }
    return out;
  }

  return { render: (src) => { const f = document.createDocumentFragment(); f.append(...blocks(src || "")); return f; } };
})();
