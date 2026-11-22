// The page an identity-aware proxy sends the "Reconnect" window back to once the browser is
// signed in again (net.js opens it). Reaching it at all means the proxy's cookie is fresh:
// tell the Console tab, then close. It doubles as net.js's probe URL, so it must stay tiny.
"use strict";
(() => {
  const ja = (navigator.language || "").toLowerCase().startsWith("ja");
  document.documentElement.lang = ja ? "ja" : "en";
  document.getElementById("msg").textContent = ja
    ? "サインインしました。このウィンドウは閉じてかまいません。"
    : "Signed in. You can close this window.";
  if (window.BroadcastChannel) new BroadcastChannel("ccdb-net").postMessage("signed-in");
  // Closes only a window a script opened (the Reconnect one); a tab opened by hand stays.
  setTimeout(() => window.close(), 400);
})();
