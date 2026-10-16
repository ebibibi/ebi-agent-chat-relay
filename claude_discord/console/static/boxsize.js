// Draggable reply box: on a desktop the top edge of the reply form can be dragged up or down
// and the height is remembered per browser. The box still grows with what is typed; the dragged
// height is its floor, and it may go past the usual 40vh cap. Double-click the edge to go back
// to the default. Narrow screens and touch keep the plain auto-growing box.
"use strict";

window.ConsoleBoxSize = (() => {
  const KEY = "replyHeight";
  const STEP = 24; // px per arrow key press
  const MIN = 64;
  const wide = window.matchMedia("(min-width: 901px) and (pointer: fine)");

  let height = Number(localStorage.getItem(KEY)) || 0;

  // Leave room for the header and a few lines of conversation above the box.
  const max = () => Math.max(MIN, window.innerHeight - 220);
  const clamp = (px) => Math.round(Math.min(max(), Math.max(MIN, px)));
  const save = () => {
    if (height) localStorage.setItem(KEY, String(height));
    else localStorage.removeItem(KEY);
  };

  // The height the box must not shrink below, and the cap it may grow to. 0 = no override.
  function floor(box) {
    if (box.id !== "reply" || !height || !wide.matches) {
      box.style.removeProperty("max-height");
      return 0;
    }
    const h = clamp(height);
    box.style.maxHeight = `max(40vh, ${h}px)`;
    return h;
  }

  function attach(form, box, grow) {
    const ja = document.documentElement.lang === "ja";
    const edge = document.createElement("div");
    edge.className = "reply-resize";
    edge.setAttribute("role", "separator");
    edge.setAttribute("aria-orientation", "horizontal");
    edge.setAttribute("aria-label", ja ? "入力欄の高さ" : "Reply box height");
    edge.title = ja ? "ドラッグで高さを変更（ダブルクリックで戻す）" : "Drag to resize (double-click to reset)";
    edge.tabIndex = 0;
    const set = (px) => { height = clamp(px); grow(box); };

    edge.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      edge.setPointerCapture(e.pointerId);
      const startY = e.clientY;
      const startH = box.getBoundingClientRect().height;
      document.body.classList.add("row-resizing");
      const move = (ev) => set(startH + (startY - ev.clientY)); // dragging up makes it taller
      const up = () => {
        edge.removeEventListener("pointermove", move);
        edge.removeEventListener("pointerup", up);
        edge.removeEventListener("pointercancel", up);
        document.body.classList.remove("row-resizing");
        save();
      };
      edge.addEventListener("pointermove", move);
      edge.addEventListener("pointerup", up);
      edge.addEventListener("pointercancel", up);
    });

    edge.addEventListener("dblclick", () => { height = 0; save(); grow(box); });

    edge.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();
      e.stopPropagation(); // keep the console's own shortcuts out of it
      set(box.getBoundingClientRect().height + (e.key === "ArrowUp" ? STEP : -STEP));
      save();
    });

    form.prepend(edge);
  }

  return { attach, floor };
})();
