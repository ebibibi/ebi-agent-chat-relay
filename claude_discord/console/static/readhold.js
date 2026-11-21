// Keeps the line being read where it is on the screen. Much can change the conversation's
// geometry while someone reads: the family list or the header above it grows or shrinks
// when a child reports, a thumbnail loads, the live panel below opens, the window or the
// phone keyboard resizes. Each of those moved the text. After layout and before paint, this
// puts the first message in view back at the height it was on screen. A reader at the end
// stays at the end. The browser's own scroll anchoring is off for the conversation
// (app.css): it only follows changes inside the scroller and would fight this one.
// Loaded before app.js, which attaches it to each conversation it builds.
"use strict";
window.ConsoleReadHold = (() => {
  const END_PX = 80; // the same "at the end" as app.js nearBottom
  let box = null, pinned = true, anchor = null, sizes = null, children = null;
  let lastTop = 0; // where the conversation was left, by the reader or by settle()

  const atEnd = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < END_PX;
  const shown = () => box && box.isConnected && box.clientHeight > 0;

  function capture() {
    const edge = box.getBoundingClientRect().top;
    for (const node of box.children) {
      const rect = node.getBoundingClientRect();
      if (rect.bottom > edge) return { node, mid: node.dataset.mid, y: rect.top };
    }
    return null;
  }

  // Where the reader is, taken whenever they (or anything) scroll.
  function record() {
    if (!shown()) return;
    pinned = atEnd(box);
    anchor = pinned ? null : capture();
    lastTop = box.scrollTop;
  }

  function settle() {
    if (!shown()) return;
    // Moved by code since (jump to the latest, a thread's first render): that is the new place.
    if (Math.abs(box.scrollTop - lastTop) >= 1) { record(); return; }
    if (pinned) {
      box.scrollTop = box.scrollHeight;
      lastTop = box.scrollTop;
      return;
    }
    if (!anchor) return;
    let node = anchor.node;
    if (node.parentElement !== box) {
      // Re-rendered: the same message under a new node.
      node = anchor.mid ? box.querySelector(`:scope > [data-mid="${CSS.escape(anchor.mid)}"]`) : null;
      if (!node) { record(); return; }
      anchor = { node, mid: anchor.mid, y: anchor.y };
    }
    const moved = node.getBoundingClientRect().top - anchor.y;
    if (Math.abs(moved) >= 1) box.scrollTop += moved;
    lastTop = box.scrollTop;
  }

  function watch(node) { if (node.nodeType === 1) sizes.observe(node); }

  function attach(el) {
    sizes?.disconnect();
    children?.disconnect();
    box = el;
    pinned = true;
    anchor = null;
    lastTop = el.scrollTop;
    el.addEventListener("scroll", record, { passive: true });
    if (!window.ResizeObserver || !window.MutationObserver) return;
    // The conversation's own size (something above or below it changed) and each
    // message's size (an image or a file row arrived) both move the text.
    sizes = new ResizeObserver(settle);
    sizes.observe(el);
    for (const node of el.children) watch(node);
    children = new MutationObserver((records) => {
      for (const r of records) {
        r.addedNodes.forEach(watch);
        r.removedNodes.forEach((node) => { if (node.nodeType === 1) sizes.unobserve(node); });
      }
      settle();
    });
    children.observe(el, { childList: true });
  }

  return { attach, record };
})();
