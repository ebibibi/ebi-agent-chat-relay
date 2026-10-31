// Keep the mobile composer inside the visible viewport, including the soft keyboard.
// Move existing controls into one panel; drafts, uploads and handlers keep their DOM nodes.
"use strict";
window.ConsoleMobile = (() => {
  const narrow = matchMedia('(max-width: 900px)');
  let itemId = null;
  function viewport() {
    if (!narrow.matches) return;
    const vv = window.visualViewport;
    if (vv && vv.scale > 1.1) return; // do not fight pinch zoom
    const messages = document.getElementById('msgs');
    const top = messages?.scrollTop || 0;
    const bottom = messages && nearBottom(messages);
    const height = vv?.height || innerHeight;
    document.documentElement.style.setProperty('--mobile-height', `${height}px`);
    document.documentElement.style.setProperty('--mobile-top', `${vv?.offsetTop || 0}px`);
    // innerHeight shrinks with the visual viewport on some phones; the layout viewport does not.
    const full = Math.max(innerHeight, document.documentElement.clientHeight);
    const typing = matchMedia('(pointer: coarse)').matches && document.activeElement?.matches('textarea, input:not([type=checkbox], [type=radio], [type=button])');
    document.documentElement.classList.toggle('keyboard-open', full - height > 120 || Boolean(typing));
    requestAnimationFrame(() => {
      if (messages?.isConnected) messages.scrollTop = bottom ? messages.scrollHeight : top;
    });
  }
  function layout(item, rebuilt) {
    const pane = $('detail'), head = $('d-head'), row = head.querySelector('.d-head-row'), drawer = $('d-drawer');
    let panel = $('mobile-tools');
    if (!panel) {
      panel = h('section', {id:'mobile-tools', class:'mobile-tools', hidden:true},
        h('button', {class:'btn mobile-tools-close', type:'button', onclick:toggleDrawer}),
        h('div', {class:'mobile-actions controls'}));
      pane.append(panel);
    }
    const team = pane.querySelector('.team-settings');
    const actions = panel.querySelector('.mobile-actions');
    if (rebuilt) actions.replaceChildren();
    if (narrow.matches) {
      if (itemId !== item.id) {state.drawerOpen = false; itemId = item.id;}
      for (const node of [...row.children]) {
        if (!node.matches('[aria-label=close], .d-status, .d-title-input, .detail-nav-toggle, .d-more, .stop')) actions.append(node);
      }
      // Finish leads the panel so it is in the same spot in every thread.
      const finish = head.querySelector(':scope > .d-finish');
      if (finish) actions.prepend(finish);
      const stop = head.querySelector('.stop');
      if (stop) {stop.dataset.compact='true';stop.textContent=stop.disabled?'⏳':'■';}
      const more = head.querySelector('.d-more');
      more.textContent='•••'; more.setAttribute('aria-label',T.details);
      more.setAttribute('aria-expanded',String(state.drawerOpen));
      panel.setAttribute('aria-label',T.details);
      panel.querySelector('.mobile-tools-close').textContent=LANG==='ja'?'閉じる':'Close';
      if (team && team.parentElement !== panel) panel.append(team);
      if (drawer.parentElement !== panel) panel.append(drawer);
      drawer.hidden=false;
      panel.hidden=!state.drawerOpen;
    } else {
      panel.hidden=true;
      if (!rebuilt) for (const node of [...actions.children]) (node.matches('.d-finish') ? head : row).append(node);
      if (head.nextElementSibling !== drawer) head.after(drawer);
      if (team && drawer.nextElementSibling !== team) drawer.after(team);
      drawer.hidden=!state.drawerOpen;
    }
    const box = $('reply');
    if (box && box.dataset.mobile !== String(narrow.matches)) {
      box.dataset.mobile=String(narrow.matches);box.rows=narrow.matches?1:3;grow(box);
    }
  }
  narrow.addEventListener('change', () => {viewport();renderDetail();});
  window.addEventListener('resize', viewport);
  window.visualViewport?.addEventListener('resize', viewport);
  window.visualViewport?.addEventListener('scroll', viewport);
  document.addEventListener('focusin', viewport);
  document.addEventListener('focusout', () => setTimeout(viewport, 0));
  document.addEventListener('keydown', e => {
    if (narrow.matches && state.drawerOpen && e.key==='Escape') {
      e.preventDefault();e.stopImmediatePropagation();toggleDrawer();
    }
  }, true);
  // Swipe right goes back through the layers Escape closes. Elsewhere in the list swipe right
  // opens the menu and swipe left closes it. Text boxes and sideways-scrolling code keep their drags.
  // A list row follows the finger like Gmail: right finishes, left toggles the pin, each over its
  // own colour, and acts only when let go past a third of the row.
  let swipe = null;
  const SETTLE_MS = 200;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const scrollsX = n => {
    for (; n && n !== document.body; n = n.parentElement)
      if (n.scrollWidth > n.clientWidth && /auto|scroll/.test(getComputedStyle(n).overflowX)) return true;
    return false;
  };
  // A poll re-renders the list mid-drag, so the row is looked up again rather than held.
  const rowNode = k => document.querySelector(`#list .row[data-item-id="${CSS.escape(k.id)}"][data-occurrence="${k.n}"]`);
  function paint(k, dx, settling) {
    const list = $('list'), row = rowNode(k);
    if (!row) return;
    const item = state.byId.get(k.id), ja = LANG === 'ja';
    const kind = dx < 0 ? 'pin' : item?.bucket === 'done' ? '' : 'finish';
    row.classList.add('swiping');
    row.classList.toggle('settling', Boolean(settling));
    row.style.transform = dx ? `translateX(${dx}px)` : '';
    list.dataset.swipe = kind;
    list.dataset.swipeLabel = kind === 'pin' ? (item?.pinned_at ? (ja ? 'ピンを外す' : 'Unpin') : (ja ? '📌 ピン留め' : '📌 Pin'))
      : kind ? (ja ? '✓ 完了' : '✓ Finish') : '';
    list.classList.toggle('swipe-past', Math.abs(dx) >= row.offsetWidth / 3);
    list.style.setProperty('--swipe-top', `${row.offsetTop}px`);
    list.style.setProperty('--swipe-height', `${row.offsetHeight}px`);
  }
  function unpaint(k) {
    const list = $('list'), row = rowNode(k);
    row?.classList.remove('swiping', 'settling');
    if (row) row.style.transform = '';
    delete list.dataset.swipe;
    list.classList.remove('swipe-past');
  }
  async function release(k, dx) {
    const row = rowNode(k), item = state.byId.get(k.id);
    const past = row && Math.abs(dx) >= row.offsetWidth / 3;
    const finish = past && dx > 0 && item?.bucket !== 'done', pin = past && dx < 0;
    // Finishing slides the row out the way it was pushed; anything else springs back.
    paint(k, finish ? row.offsetWidth : 0, true);
    await new Promise(r => setTimeout(r, reduced.matches ? 0 : SETTLE_MS));
    if (!finish) unpaint(k);
    try {
      if (finish) await window.ConsoleFinish.finish(k.id);
      else if (pin) await togglePin(k.id);
    } finally { unpaint(k); }
  }
  document.addEventListener('touchstart', e => {
    if (swipe) stop();
    const t = e.touches[0];
    const row = !state.open && e.target.closest('#list .row');
    swipe = narrow.matches && e.touches.length === 1 && !(window.visualViewport?.scale > 1.1)
      && !e.target.closest('input:not([type=checkbox], [type=radio]), textarea, select, [contenteditable], .cm-editor') && !scrollsX(e.target)
      ? {x: t.clientX, y: t.clientY, at: e.timeStamp, row: row && {id: row.dataset.itemId, n: row.dataset.occurrence}, drag: false} : null;
    // On the touched node, not the document: once a poll re-renders the list, the touched row is
    // detached and its events no longer bubble up. Not passive, so a sideways drag stops the scroll.
    if (swipe) track(e.target, true);
  }, {passive: true});
  function track(el, on) {
    const method = on ? 'addEventListener' : 'removeEventListener';
    el[method]('touchmove', move, {passive: false});
    el[method]('touchend', end);
    el[method]('touchcancel', cancel);
    if (on) swipe.el = el;
  }
  function stop() { if (swipe) track(swipe.el, false); swipe = null; }
  function move(e) {
    if (!swipe?.row || $('nav').classList.contains('open')) return;
    const t = e.touches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y;
    if (!swipe.drag) {
      if (Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) { stop(); return; }
      if (Math.abs(dx) < 10) return;
      swipe.drag = true;
    }
    e.preventDefault();
    paint(swipe.row, dx);
  }
  function cancel() {
    if (swipe?.drag) release(swipe.row, 0);
    stop();
  }
  function end(e) {
    if (!swipe) return;
    const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y, slow = e.timeStamp - swipe.at > 600;
    if (swipe.drag) { release(swipe.row, dx); stop(); return; }
    const nav = $('nav'), back = state.open || location.hash.startsWith('#note/'), row = swipe.row;
    stop();
    if (slow || Math.abs(dx) < 70 || Math.abs(dx) < 2 * Math.abs(dy) || getSelection().toString()) return;
    if (nav.classList.contains('open')) { if (dx < 0) nav.classList.remove('open'); }
    else if (row || dx < 0 || document.querySelector('.note-edit')) return;
    else if (back) document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    else nav.classList.add('open');
  }
  viewport();
  return {layout};
})();
