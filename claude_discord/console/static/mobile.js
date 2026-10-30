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
  // Swipe right goes back through the layers Escape closes, or opens the menu from the list;
  // swipe left closes the menu. Text boxes and sideways-scrolling code keep their own drags.
  let swipe = null;
  const scrollsX = n => {
    for (; n && n !== document.body; n = n.parentElement)
      if (n.scrollWidth > n.clientWidth && /auto|scroll/.test(getComputedStyle(n).overflowX)) return true;
    return false;
  };
  document.addEventListener('touchstart', e => {
    const t = e.touches[0];
    swipe = narrow.matches && e.touches.length === 1 && !(window.visualViewport?.scale > 1.1)
      && !e.target.closest('input, textarea, select, [contenteditable], .cm-editor') && !scrollsX(e.target)
      ? {x: t.clientX, y: t.clientY, at: e.timeStamp} : null;
  }, {passive: true});
  document.addEventListener('touchcancel', () => {swipe = null;});
  document.addEventListener('touchend', e => {
    if (!swipe) return;
    const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y, slow = e.timeStamp - swipe.at > 600;
    swipe = null;
    if (slow || Math.abs(dx) < 70 || Math.abs(dx) < 2 * Math.abs(dy) || getSelection().toString()) return;
    const nav = $('nav');
    if (dx < 0) nav.classList.remove('open');
    else if (nav.classList.contains('open') || document.querySelector('.note-edit')) return;
    else if (state.open || location.hash.startsWith('#note/')) document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    else nav.classList.add('open');
  });
  viewport();
  return {layout};
})();
