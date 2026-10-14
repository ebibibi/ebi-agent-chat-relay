// Selection is tied to IDs; range anchors are tied to the displayed row occurrence.
"use strict";
window.ConsoleBulk = (() => {
  const selected = new Set(), errors = new Map();
  let order = [], anchor = null, busy = false, message = '';
  const ja = () => document.documentElement.lang === 'ja';
  function sync(rows) {
    const occurrences = new Map();
    const next = rows.filter(r => r.item).map(({item}) => {
      const n = occurrences.get(item.id) || 0;
      occurrences.set(item.id, n + 1);
      return {id:item.id, key:`${item.id}:${n}`};
    });
    if (JSON.stringify(next) !== JSON.stringify(order)) {
      anchor = null;
      if (!busy) {
        const visible = new Set(next.map(r => r.id));
        let removed = 0;
        for (const id of selected) if (!visible.has(id) && !errors.has(id)) { selected.delete(id); removed++; }
        if (removed) message = ja() ? `表示から外れた${removed}件の選択を解除しました` : `Cleared ${removed} hidden selections`;
      }
    }
    order = next;
  }
  function choose(id, key, range) {
    if (busy) return;
    const visible = new Set(order.map(row => row.id));
    for (const chosen of selected) if (!visible.has(chosen)) selected.delete(chosen);
    errors.clear(); message = '';
    const start = order.findIndex(r => r.key === anchor), end = order.findIndex(r => r.key === key);
    if (range && start >= 0 && end >= 0) {
      for (const row of order.slice(Math.min(start,end), Math.max(start,end)+1)) selected.add(row.id);
    } else {
      selected.has(id) ? selected.delete(id) : selected.add(id);
      anchor = key;
    }
    renderList();
  }
  function click(event, id, key) {
    if (busy) return true;
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      event.preventDefault(); choose(id,key,event.shiftKey); return true;
    }
    anchor = key;
    return false;
  }
  function checkbox(item, key) {
    return h('input', {type:'checkbox', class:'bulk-check', checked:selected.has(item.id), disabled:busy,
      'aria-label':(ja()?'まとめて操作する対象に選択: ':'Select for bulk action: ')+(item.title || item.id),
      onclick:e => { e.stopPropagation(); choose(item.id,key,e.shiftKey); }});
  }
  function clear() { if (busy) return; selected.clear(); errors.clear(); anchor=null; message=''; renderList(); }
  async function finish() {
    if (busy || !selected.size) return;
    const ids = window.ConsoleFinish.targets([...selected]);
    busy = true; errors.clear(); message = ja()?'完了処理中…':'Completing…'; renderList();
    const result = await window.ConsoleFinish.completeMany(ids, (n) => {
      message = ja()?`完了処理中 ${n}/${ids.length}`:`Completing ${n}/${ids.length}`; toolbar();
    });
    selected.clear();
    for (const [id,error] of result.errors) {selected.add(id); errors.set(id,error);}
    message = ja()?`${result.completed.length}件を完了${errors.size ? `・${errors.size}件失敗（再試行できます）` : ''}`:
      `${result.completed.length} completed${errors.size ? ` · ${errors.size} failed (retry available)` : ''}`;
    await refresh();
    busy=false; renderList();
  }
  function toolbar() {
    const bar = $('bulk-bar'); if (!bar) return;
    bar.dataset.active = String(Boolean(selected.size || busy || message || errors.size));
    const targets = window.ConsoleFinish.targets([...selected]);
    const count = ja()?`${selected.size}件選択・完了対象 ${targets.length}件（子を含む）`:
      `${selected.size} selected · ${targets.length} to complete (including children)`;
    bar.replaceChildren(h('span',{id:'bulk-count',text:selected.size?count:
      (ja()?'チェックまたはShift+クリックで複数選択':'Checkbox or Shift-click to select multiple')}),
      h('button',{id:'bulk-finish',class:'btn',type:'button',disabled:busy||!selected.size,
        text:busy?(ja()?'処理中…':'Completing…'):errors.size?(ja()?'失敗分を再試行':'Retry failed'):(ja()?'まとめて完了':'Complete selected'),onclick:finish}),
      h('button',{id:'bulk-clear',class:'btn',type:'button',disabled:busy||!selected.size,text:ja()?'選択解除':'Clear selection',onclick:clear}),
      h('span',{id:'bulk-status',role:'status',text:message}),
      h('ul',{id:'bulk-errors',hidden:!errors.size},...[...errors].map(([id,error])=>h('li',{
        text:`${state.byId.get(id)?.title || id}: ${error}`}))));
  }
  return {sync,click,checkbox,toolbar,clear,has:id=>selected.has(id)};
})();
