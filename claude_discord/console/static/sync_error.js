// Classify board refresh failures by the stage that failed. A network error is
// retried by the regular poll; a valid response means later failures are local UI work.
(function (root) {
  "use strict";

  function classifySyncFailure(error, boardResponseReceived) {
    if (boardResponseReceived) return "render";
    if (error && error.syncFailureKind === "response") return "server";
    if (error && error.status === 401) return "auth";
    if (error && Number.isInteger(error.status)
        && error.status >= 400 && error.status < 600) return "server";
    return "reconnecting";
  }

  const api = { classifySyncFailure };
  root.ConsoleSyncErrors = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
