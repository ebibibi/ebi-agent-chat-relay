// The filter language: what the filter box and every saved view speak.
//   words           every word appears in the title, project, Workspace entry, thread or folder
//   "two words"     the phrase appears as written
//   tag:teams-exporter            the item carries that tag (exact)
//   status:waiting,review         a comma means OR — any of the values
//   p:0  p:<=1  priority:>2       priority, with = < <= > >=
//   project:site  place:office  backend:codex  model:opus  origin:console  dir:ccdb  title:draft
//   list:me  (the list an item lives in: focus lists aside, the bucket — me, ai, todo, done …)
//   is:running|pinned|overdue|paused|child|thread|capture|done|open
//   has:tag|due|project|place|workspace
//   -anything       negates the term:  -tag:billing  -is:done  -draft
// Terms are ANDed. A key the language does not know is read as plain text, so a pasted
// "https://…" or "10:30" still finds what it says. Values compare case-insensitively.
// Loaded before app.js (matches) and views.js.
"use strict";

window.ConsoleQuery = (() => {
  const TEXT_FIELDS = (i) => [i.title, i.project, i.workspace_path, i.thread_name, i.working_dir];
  const lower = (v) => (v == null ? "" : String(v)).toLowerCase();
  const contains = (field, values) => values.some((v) => lower(field).includes(v));

  const IS = {
    running: (i) => Boolean(i.running),
    pinned: (i) => Boolean(i.pinned_at),
    overdue: (i) => Boolean(i.overdue),
    paused: (i) => Boolean(i.paused_at),
    child: (i) => Boolean(i.parent_id),
    thread: (i) => Boolean(i.thread_id),
    capture: (i) => !i.thread_id,
    done: (i) => i.bucket === "done",
    open: (i) => i.bucket !== "done",
  };
  const HAS = {
    tag: (i) => Boolean(i.tags?.length),
    due: (i) => Boolean(i.due_at),
    project: (i) => Boolean(i.project),
    place: (i) => Boolean(i.place),
    workspace: (i) => Boolean(i.workspace_path),
  };

  function priorityTest(values) {
    const checks = values.map((v) => {
      const m = v.match(/^(<=|>=|<|>|=)?([0-3])$/);
      if (!m) return null;
      const n = Number(m[2]);
      return { "<=": (p) => p <= n, ">=": (p) => p >= n, "<": (p) => p < n, ">": (p) => p > n }[m[1]] || ((p) => p === n);
    });
    if (checks.some((c) => !c)) return null;
    return (i) => checks.some((c) => c(i.priority ?? 2));
  }

  // key → (values) => predicate, or null when a value makes no sense for the key.
  const KEYS = {
    tag: (vs) => (i) => (i.tags || []).some((t) => vs.includes(lower(t))),
    status: (vs) => (i) => vs.includes(lower(i.status)),
    list: (vs) => (i) => vs.includes(lower(i.bucket)),
    p: priorityTest,
    project: (vs) => (i) => contains(i.project, vs) || contains(i.workspace_path, vs),
    place: (vs) => (i) => contains(i.place, vs),
    backend: (vs) => (i) => vs.includes(lower(i.backend)),
    model: (vs) => (i) => contains(i.model, vs),
    origin: (vs) => (i) => vs.includes(lower(i.origin)),
    dir: (vs) => (i) => contains(i.working_dir, vs),
    title: (vs) => (i) => contains(i.title, vs) || contains(i.thread_name, vs),
    is: (vs) => vs.every((v) => IS[v]) ? (i) => vs.some((v) => IS[v](i)) : null,
    has: (vs) => vs.every((v) => HAS[v]) ? (i) => vs.some((v) => HAS[v](i)) : null,
  };
  const ALIASES = { tags: "tag", st: "status", priority: "p", pri: "p", bucket: "list", folder: "dir" };

  // Split on spaces, keeping "quoted phrases" (and key:"quoted value") together.
  function tokens(q) {
    const out = [];
    const re = /(-?)(?:([A-Za-z]+):)?(?:"([^"]*)"?|(\S+))/g;
    let m;
    while ((m = re.exec(q))) {
      const [, neg, key, quoted, bare] = m;
      out.push({ neg: Boolean(neg), key: key ? key.toLowerCase() : null, value: quoted ?? bare ?? "",
        quoted: quoted !== undefined, raw: m[0] });
    }
    return out;
  }

  function term(tok) {
    const key = tok.key && (ALIASES[tok.key] || tok.key);
    if (key && KEYS[key]) {
      const values = tok.value.split(",").map((v) => v.trim().toLowerCase()).filter(Boolean);
      const test = values.length ? KEYS[key](values) : null;
      if (test) return { test, neg: tok.neg, key };
      return { error: tok.raw };
    }
    // Plain text, including "key:value" for a key the language does not know.
    const text = (tok.key && !tok.quoted ? tok.raw.replace(/^-/, "") : tok.value).toLowerCase();
    if (!text) return null;
    return { test: (i) => TEXT_FIELDS(i).some((f) => lower(f).includes(text)), neg: tok.neg, key: null };
  }

  const cache = new Map();
  // { terms, errors, structured } — errors are the terms that could not be read.
  function parse(q) {
    const text = (q || "").trim();
    if (cache.has(text)) return cache.get(text);
    const terms = [];
    const errors = [];
    for (const tok of tokens(text)) {
      const t = term(tok);
      if (!t) continue;
      if (t.error) errors.push(t.error); else terms.push(t);
    }
    const parsed = { terms, errors, structured: terms.some((t) => t.key) };
    if (cache.size > 200) cache.clear();
    cache.set(text, parsed);
    return parsed;
  }

  function test(item, q) {
    const { terms } = typeof q === "string" ? parse(q) : q;
    return terms.every((t) => t.test(item) !== t.neg);
  }

  // Whether the query names finished items on purpose, so "✅ Done hidden" must not hide them.
  const wantsDone = (q) => /(^|\s)(is:done|list:done|bucket:done|st(atus)?:([^\s]*,)?done)/i.test(q || "");

  return { parse, test, wantsDone };
})();
