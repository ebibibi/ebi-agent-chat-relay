// Inner side of the HTML preview (preview.js). This page is served with its own policy
// (server.py: inline styles, embedded images and fonts; no network, no scripts but this file)
// and sits in an iframe without allow-same-origin, so it cannot reach the console. The
// delivered page goes into a nested iframe with an empty sandbox, which inherits that policy:
// its CSS applies, its scripts and outside requests do not run.
"use strict";

window.addEventListener("message", (e) => {
  if (e.source !== window.parent || typeof e.data !== "string") return;
  document.getElementById("page").srcdoc = e.data;
});
