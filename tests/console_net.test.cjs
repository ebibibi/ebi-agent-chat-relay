// The pure parts of net.js: the reload loop guard, what counts as a proxy redirect, and the
// key that finds the same text box again after a reload.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const store = new Map();
const ctx = {
  window: {}, URL, Event: class {}, MutationObserver: class { observe() {} disconnect() {} },
  setTimeout, clearTimeout, setInterval, clearInterval,
  location: {href: 'https://console.test/', origin: 'https://console.test', hash: ''},
  history: {replaceState() {}},
  sessionStorage: {getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k)},
  document: {readyState: 'complete', documentElement: {lang: 'en'}, addEventListener() {}, querySelectorAll: () => []},
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('claude_discord/console/static/net.js', 'utf8'), ctx);
const N = ctx.window.ConsoleNet._test;
const G = {max: 3, windowMs: 600000, gapMs: 30000};

test('the first reload is always allowed', () => {
  assert.equal(N.allowReload([], 1_000_000, G), true);
});
test('a second reload waits for the gap', () => {
  assert.equal(N.allowReload([1_000_000], 1_000_000 + 10_000, G), false);
  assert.equal(N.allowReload([1_000_000], 1_000_000 + 30_000, G), true);
});
test('three reloads in the window stop automatic reloads until they age out', () => {
  const t = [0, 100_000, 200_000];
  assert.equal(N.allowReload(t, 300_000, G), false);
  assert.equal(N.allowReload(t, 600_001, G), true);
});
test('a manual-mode redirect, and only that, means the proxy wants a sign-in', () => {
  assert.equal(N.isRedirect({type: 'opaqueredirect', status: 0}), true);
  assert.equal(N.isRedirect({type: 'basic', status: 302}), true);
  assert.equal(N.isRedirect({type: 'basic', status: 200}), false);
  assert.equal(N.isRedirect({type: 'basic', status: 401}), false);
});
test('a text box is found again by id, else by what describes it', () => {
  const attrs = {placeholder: 'Reply', 'aria-label': 'Answer'};
  const el = (id) => ({id, tagName: 'TEXTAREA', className: 'sec-reply', getAttribute: (n) => attrs[n] ?? null});
  assert.equal(N.fieldKey(el('reply')), '#reply');
  assert.equal(N.fieldKey(el('')), 'textarea|sec-reply||Answer|Reply');
});
test('a fetch to another origin is passed through untouched', async () => {
  let seen = null;
  ctx.fetch = async (input) => { seen = input; return {ok: true}; };
  await ctx.window.ConsoleNet.fetch('https://cdn.example/a.png');
  assert.equal(seen, 'https://cdn.example/a.png');
});
test('a same-origin failure with a redirecting probe is a sign-in, with a dead probe a network error', async () => {
  ctx.document.body = {append() {}};
  ctx.document.createElement = () => ({setAttribute() {}, append() {}, addEventListener() {}, remove() {}, firstChild: {}});
  ctx.fetch = async (input) => {
    if (String(input).includes('reauth')) throw new TypeError('Failed to fetch');
    throw new TypeError('Failed to fetch');
  };
  await assert.rejects(ctx.window.ConsoleNet.fetch('/console/api/board'), (e) => e.kind === 'network' && !/Failed to fetch/.test(e.message));
  ctx.fetch = async (input) => {
    if (String(input).includes('reauth')) return {type: 'opaqueredirect', status: 0};
    throw new TypeError('Failed to fetch');
  };
  let reloads = 0;
  ctx.location.reload = () => { reloads += 1; };
  await assert.rejects(ctx.window.ConsoleNet.fetch('/console/api/board'), (e) => e.kind === 'signin');
  assert.equal(ctx.window.ConsoleNet.pending(), true);
  // Nobody is typing: it saves the view and reloads at once, and records the reload for the guard.
  assert.equal(reloads, 1);
  assert.equal(JSON.parse(store.get('ccdb.net.reloads')).length, 1);
  assert.equal(JSON.parse(store.get('ccdb.net.saved')).hash, '');
});
