// Shareable Console routes. Never copy query strings (which may contain credentials).
"use strict";
window.ConsoleLinks = (() => {
  const L = () => document.documentElement.lang === 'ja' ? {
    copy:'リンクをコピー', copied:'スレッドへのリンクをコピーしました',
    failed:'リンクをコピーできませんでした', missing:'リンク先のスレッドが見つかりません。',
  } : {copy:'Copy thread link', copied:'Thread link copied',
    failed:'Could not copy thread link', missing:'Thread not found.'};
  function route(href) {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin || url.pathname !== '/' || url.username || url.password) return null;
      const match = url.hash.match(/^#([a-z0-9]+)\/([A-Za-z0-9_-]+)$/);
      if (!match || !isViewKey(match[1])) return null;
      return {view:match[1], id:match[2]};
    } catch { return null; }
  }
  // A note in the viewer: /#note/<vault path>, each segment URL-encoded or not.
  function note(href) {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin || url.pathname !== '/' || url.username || url.password) return null;
      if (!url.hash.startsWith('#note/')) return null;
      const path = url.hash.slice(6).split('/').map(decodeURIComponent).join('/');
      return path ? path : null;
    } catch { return null; }
  }
  function button(item) {
    const button = h('button',{class:'btn thread-copy-link',type:'button',text:L().copy});
    button.addEventListener('click',async () => {
      button.disabled=true;
      try {
        await navigator.clipboard.writeText(`${location.origin}/#focus/${encodeURIComponent(item.id)}`);
        toast(L().copied);
      } catch (err) { toast(`${L().failed}: ${err.message}`); }
      finally { button.disabled=false; }
    });
    return button;
  }
  let navigation=0;
  async function open(target, push=false) {
    const current=++navigation;
    if (!state.byId.has(target.id)) await refresh();
    if (current !== navigation) return;
    if (!state.byId.has(target.id)) { toast(L().missing); syncHash(); return; }
    if (push) history.pushState(null,'',`#${target.view}/${target.id}`);
    state.view=target.view;
    await openItem(target.id);
  }
  function wire() {
    document.addEventListener('click',event => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const anchor=event.target.closest?.('a[href]');
      if (!anchor || anchor.hasAttribute('download')) return;
      const path=note(anchor.href);
      // Open in place: following the href itself would drop the query (and its token).
      if (path!==null && window.ConsoleNotes) { event.preventDefault(); window.ConsoleNotes.open(path); return; }
      const target=route(anchor.href);
      if (!target) return;
      event.preventDefault();
      open(target,true);
    });
    window.addEventListener('hashchange',() => {
      const target=route(location.href);
      if (target) open(target);
      else {
        navigation++;
        const view=location.hash.slice(1);
        if (isViewKey(view)) { state.view=view; closeItem(); }
      }
    });
  }
  return {route,note,button,wire};
})();
