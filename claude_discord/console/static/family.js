// A thread family keeps the conversation with the parent and delegated work together.
// Loaded before app.js. No message bodies are interpreted as HTML.
"use strict";
window.ConsoleFamily = (() => {
  const STRINGS = {
    en: { title: 'Delegated work', parent: 'Parent conversation', back: 'Back to parent',
      root: 'Main conversation', hint: 'Talk to the parent; it coordinates the child threads.',
      childHint: 'This is a child thread. Send decisions and new requests to the parent.',
      direct: 'Reply directly to this child', directReply: 'Direct reply to this child… (Ctrl+Enter to send)', reply: 'Reply to the parent… (Ctrl+Enter to send)',
      recent: 'Latest report', next: 'AI suggested next step', empty: 'No report yet',
      attention: 'Needs parent attention', details: 'View thread',
      counts: (c) => `${c.total} tasks · ${c.running} running · ${c.attention} need attention · ${c.done} done` },
    ja: { title: '子スレッドの作業', parent: '親との会話', back: '親との会話に戻る',
      root: 'メインの会話', hint: '依頼や判断は親へ。親が子スレッドの作業をまとめます。',
      childHint: '子スレッドを表示中です。判断や追加の依頼は親に伝えましょう。',
      direct: 'この子に直接返信する', directReply: 'この子に直接返信中…（Ctrl+Enterで送信）', reply: '親に返信…（Ctrl+Enterで送信）',
      recent: '最新の報告', next: 'AIが推定した次の作業', empty: 'まだ報告はありません',
      attention: '親の確認待ち', details: 'スレッドを見る',
      counts: (c) => `全${c.total}件 · 実行中 ${c.running} · 確認待ち ${c.attention} · 完了 ${c.done}` },
  };
  const L = () => STRINGS[document.documentElement.lang === 'ja' ? 'ja' : 'en'];
  const needsAttention = (i) => !i.running && !i.queued && ['waiting', 'review', 'action', 'error'].includes(i.status);
  function model(items, id) {
    const byId = new Map(items.map(i => [i.id, i]));
    const selected = byId.get(id);
    if (!selected) return null;
    let root = selected;
    const ancestors = new Set([id]);
    while (byId.has(root.parent_id) && !ancestors.has(root.parent_id)) {
      root = byId.get(root.parent_id);
      ancestors.add(root.id);
    }
    const rows = [], seen = new Set([root.id]);
    const children = new Map();
    for (const item of items) {
      if (!children.has(item.parent_id)) children.set(item.parent_id, []);
      children.get(item.parent_id).push(item);
    }
    const walk = (parent, depth) => {
      for (const item of children.get(parent) || []) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        rows.push({item, depth});
        walk(item.id, depth + 1);
      }
    };
    walk(root.id, 0);
    const counts = {total: rows.length, running: 0, attention: 0, done: 0};
    for (const {item} of rows) {
      if (item.running || item.queued) counts.running++;
      if (needsAttention(item)) counts.attention++;
      if (item.status === 'done') counts.done++;
    }
    return {root, parent: byId.get(selected.parent_id) || null, rows, counts};
  }
  // Keep each family at its first matching row's position. Include ancestors as
  // context, but do not pull unrelated/filtered siblings into a selection range.
  function groupRows(rows) {
    const matching = new Set(rows.filter(r=>r.item).map(r=>r.item.id));
    const emitted = new Set(), out = [];
    for (const row of rows) {
      if (!row.item) { out.push(row); continue; }
      const family = model(state.items,row.item.id);
      if (!family || emitted.has(family.root.id)) continue;
      emitted.add(family.root.id);
      const included = new Set([family.root.id]);
      for (const {item} of family.rows) if (matching.has(item.id)) {
        let current = item;
        const seen = new Set();
        while (current && !seen.has(current.id)) {
          seen.add(current.id); included.add(current.id); current = state.byId.get(current.parent_id);
        }
      }
      const entries = [{item:family.root,depth:0}, ...family.rows.map(r=>({...r,depth:r.depth+1}))];
      for (const entry of entries) {
        if (!included.has(entry.item.id)) continue;
        // Tree already applied the collapse state; other lists always group matches.
        out.push({...entry,children:state.view==='tree' ? state.items.filter(i=>i.parent_id===entry.item.id).length : 0});
      }
    }
    return dropEmptyGroups(out);
  }
  function excerpt(messages) {
    const report = [...messages].reverse().find(m =>
      (m.kind === 'agent' || (!m.kind && m.is_bot)) && m.content);
    return report ? report.content.replace(/\s+/g, ' ').trim().slice(0, 240) : '';
  }
  const reports = new Map(), pending = new Set();
  let folded = false;
  function label(item) {
    return item.paused_at && !item.running ? T.paused : needsAttention(item) ? L().attention : item.queued ? T.queued : T.st[item.status] || item.status;
  }
  function card({item, depth}, selected) {
    const cached = reports.get(item.id);
    const recent = cached && cached.version === version(item) && cached.text;
    const summary = recent || (item.ai && item.ai.next_step ? `${L().next}: ${item.ai.next_step}` : '') || item.note || L().empty;
    return h('li', {class: 'family-task' + (selected === item.id ? ' current' : '') +
      (needsAttention(item) ? ' attention' : '') + (item.status === 'done' ? ' complete' : ''),
      style: {marginLeft: `${Math.min(depth, 5) * 14}px`}},
      h('button', {class: 'family-open', type: 'button', onclick: () => openItem(item.id),
        title: L().details},
        h('span', {class: 'family-task-title', text: item.title || item.thread_name || item.id}),
        h('span', {class: 'family-status' + (item.running ? ' live' : ''),
          text: `${STATUS_ICON[item.status] || '·'} ${label(item)}`})),
      h('p', {class: 'family-report', text: String(summary).slice(0, 240),
        title: recent ? L().recent : item.ai && item.ai.next_step ? L().next : ''}),
      h('span', {class: 'dim family-time', text: rel(item.last_activity_at)}));
  }
  function render(item) {
    const slot = $('d-family');
    const family = model(state.items, item.id);
    slot.hidden = !family || (!family.parent && !family.rows.length);
    const reply = $('reply');
    const child = family && family.parent;
    // Default child inspection to read-only, with an explicit escape hatch.
    const form = reply && reply.closest('form');
    if (form) form.hidden = Boolean(child && state.directReply !== item.id);
    if (reply) reply.placeholder = child ? L().directReply : family.rows.length ? L().reply : T.reply_ph;
    if (slot.hidden) { slot.replaceChildren(); delete slot.dataset.signature; return; }
    const signature = JSON.stringify([item.id, folded, state.directReply, family.root.title,
      family.rows.map(({item, depth}) => [item, depth, reports.get(item.id)])]);
    if (slot.dataset.signature === signature) {
      if (!folded) loadReports(family.rows);
      return;
    }
    slot.dataset.signature = signature;
    const rootButton = h('button', {class: 'btn family-parent', type: 'button',
      onclick: () => openItem(family.root.id), text: `${L().root}: ${family.root.title || family.root.id}`});
    const context = child ? h('div', {class: 'family-context'},
      h('p', {class: state.directReply === item.id ? 'family-direct-active' : 'dim',
        text: state.directReply === item.id ? `${L().direct}: ${item.title || item.id}` : L().childHint}), rootButton,
      h('button', {class: 'btn', type: 'button', onclick: () => openItem(family.parent.id), text: L().back}),
      h('button', {class: 'btn family-direct', type: 'button',
        onclick: () => { state.directReply = item.id; renderDetail(); $('reply')?.focus(); }, text: L().direct}))
      : h('p', {class: 'dim family-hint', text: L().hint});
    const heading = h('button', {class: 'family-heading', type: 'button',
      'aria-expanded': String(!folded), onclick: () => { folded = !folded; render(item); }},
      h('strong', {text: `${folded ? '▸' : '▾'} ${L().title}`}),
      h('span', {class: 'family-counts', text: L().counts(family.counts)}));
    const list = h('ul', {class: 'family-tasks', hidden: folded}, ...family.rows.map(row => card(row, item.id)));
    const scroll = slot.querySelector('.family-tasks')?.scrollTop || 0;
    slot.replaceChildren(context, heading, list);
    list.scrollTop = scroll;
    if (!folded) loadReports(family.rows);
  }
  const version = (item) => `${item.last_activity_at || ''}|${item.status}|${Boolean(item.running)}`;
  async function loadReports(rows) {
    // Limit concurrent requests, cache by board activity, and never mark a child read.
    const wanted = rows.filter(({item}) => item.thread_id && !pending.has(item.id) &&
      reports.get(item.id)?.version !== version(item)).slice(0, Math.max(0, 3 - pending.size));
    await Promise.allSettled(wanted.map(async ({item}) => {
      pending.add(item.id);
      try {
        const data = await api(`/items/${encodeURIComponent(item.id)}/messages?limit=20`);
        reports.set(item.id, {version: version(item), text: excerpt(data.messages)});
      } catch { reports.set(item.id, {version: version(item), text: ''}); }
      finally { pending.delete(item.id); }
    }));
    if (wanted.length && state.open) {
      const current = state.byId.get(state.open);
      if (current) render(current);
    }
  }
  function rowTag(item) {
    const family = model(state.items, item.id);
    if (family.parent) return h('button', {class: 'tag family-link', type: 'button',
      onclick: (e) => { e.stopPropagation(); openItem(family.root.id); },
      text: `↳ ${family.root.title || family.root.id}`, title: L().parent});
    if (family.rows.length) return h('span', {class: 'tag family-link', text: `⑂ ${family.counts.done}/${family.counts.total}`,
      title: L().counts(family.counts)});
    return null;
  }
  return {model, groupRows, excerpt, render, rowTag};
})();
