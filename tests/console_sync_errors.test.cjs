const { test } = require("node:test");
const assert = require("node:assert/strict");
const { classifySyncFailure } = require("../claude_discord/console/static/sync_error.js");

test("distinguishes network reconnects, auth, server, and local render failures", () => {
  assert.equal(
    classifySyncFailure(new TypeError("fetch failed"), false),
    "reconnecting",
  );
  assert.equal(
    classifySyncFailure(Object.assign(new Error("login required"), { status: 401 }), false),
    "auth",
  );
  assert.equal(
    classifySyncFailure(Object.assign(new Error("HTTP 503"), { status: 503 }), false),
    "server",
  );
  assert.equal(
    classifySyncFailure(new Error("render failed"), true),
    "render",
  );
});

test("treats an invalid successful response as a server response error", () => {
  assert.equal(
    classifySyncFailure(
      Object.assign(new Error("invalid board"), { syncFailureKind: "response" }),
      false,
    ),
    "server",
  );
});
