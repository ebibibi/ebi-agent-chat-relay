// Draggable column borders: the nav and the detail pane can be resized and the widths are
// remembered per browser. Double-click a border to go back to the default. Wide screens only;
// below the breakpoint the panes stack and the handles are hidden by CSS.
"use strict";

(() => {
  const app = document.getElementById("app");
  if (!app) return;

  const KEY = "columns";
  const STEP = 24; // px per arrow key press
  const LIMITS = { nav: [140, 420], detail: [320, 1600] };
  const VARS = { nav: "--nav-w", detail: "--detail-w" };

  const load = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { return {}; }
  };
  const widths = load();

  const clamp = (side, px) => Math.round(Math.min(LIMITS[side][1], Math.max(LIMITS[side][0], px)));
  const apply = (side) => {
    if (typeof widths[side] === "number") app.style.setProperty(VARS[side], widths[side] + "px");
    else app.style.removeProperty(VARS[side]);
  };
  const save = () => localStorage.setItem(KEY, JSON.stringify(widths));

  // The width a pane actually renders at, which can differ from the stored value when the
  // window is too narrow for it (CSS caps the detail pane so the list keeps its minimum).
  const current = (side) => {
    const pane = side === "nav" ? document.getElementById("nav") : document.getElementById("detail");
    return pane ? pane.getBoundingClientRect().width : LIMITS[side][0];
  };
  const set = (side, px) => { widths[side] = clamp(side, px); apply(side); };

  const handle = (side, label) => {
    const h = document.createElement("div");
    h.className = "col-resize col-resize-" + side;
    h.setAttribute("role", "separator");
    h.setAttribute("aria-orientation", "vertical");
    h.setAttribute("aria-label", label);
    h.tabIndex = 0;

    h.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      const startW = current(side);
      app.classList.add("resizing");
      const move = (ev) => {
        const dx = ev.clientX - startX;
        set(side, side === "nav" ? startW + dx : startW - dx);
      };
      const up = () => {
        h.removeEventListener("pointermove", move);
        h.removeEventListener("pointerup", up);
        h.removeEventListener("pointercancel", up);
        app.classList.remove("resizing");
        set(side, current(side)); // store what fits, not how far past the cap the pointer went
        save();
      };
      h.addEventListener("pointermove", move);
      h.addEventListener("pointerup", up);
      h.addEventListener("pointercancel", up);
    });

    h.addEventListener("dblclick", () => { delete widths[side]; apply(side); save(); });

    h.addEventListener("keydown", (e) => {
      const grow = side === "nav" ? "ArrowRight" : "ArrowLeft";
      const shrink = side === "nav" ? "ArrowLeft" : "ArrowRight";
      if (e.key !== grow && e.key !== shrink) return;
      e.preventDefault();
      e.stopPropagation(); // keep the console's own shortcuts out of it
      set(side, current(side) + (e.key === grow ? STEP : -STEP));
      save();
    });

    app.appendChild(h);
  };

  const ja = document.documentElement.lang === "ja";
  handle("nav", ja ? "メニューの幅" : "Menu width");
  handle("detail", ja ? "詳細の幅" : "Detail width");
  apply("nav");
  apply("detail");
})();
