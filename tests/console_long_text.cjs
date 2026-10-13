const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { File } = require('node:buffer');
const calls = [];
const context = {
  window: {}, File, localStorage: { getItem: () => 'test-token' },
  fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ url: '/console/api/files/0/token/message.txt' }) };
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('claude_discord/console/static/paste.js', 'utf8'), context);
(async () => {
  const prepare = context.window.ConsolePaste.prepareText;
  const short = 'a'.repeat(4000);
  assert.equal((await prepare(short, [])).text, short);
  assert.equal(calls.length, 0);
  const text = '日本語🎉\n'.repeat(30000);
  const existing = ['/console/api/files/0/other/spec.pdf'];
  const result = await prepare(text, existing);
  assert.ok(result.text.length < 4000);
  assert.equal(result.files.length, 2);
  assert.equal(existing.length, 1);
  assert.equal(await calls[0].options.body.text(), text);
  assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token');
  assert.equal(calls[0].options.headers['X-Console-Request'], '1');
  assert.equal(calls[0].options.headers['X-Filename'], 'message.txt');
  context.fetch = async () => ({ ok: false, status: 503, json: async () => ({ error: 'upload failed' }) });
  await assert.rejects(prepare(text, []), /upload failed/);
  // Run the actual reply/capture entry points: failed uploads must keep drafts.
  const apiCalls = [];
  Object.assign(context, {
    api: async (url, options) => {
      apiCalls.push({ url, body: options.body });
      return { item: { id: 'new', thread_id: 1 } };
    },
    toast: () => {}, grow: () => {}, refresh: async () => {},
    setTimeout: () => {}, loadMessages: () => {}, renderList: () => {},
    autostart: () => true, setView: () => {},
    state: { drafts: { item: text }, view: 'ai' }, T: {},
  });
  context.window.ConsoleSlash = { run: async () => false };
  const app = fs.readFileSync('claude_discord/console/static/app.js', 'utf8');
  vm.runInContext(app.slice(app.indexOf('async function sendReply('), app.indexOf('async function startItem(')), context);
  vm.runInContext(app.slice(app.indexOf('async function capture('), app.indexOf('// Captured work goes')), context);
  const box = { value: text, focus: () => {} };
  await context.sendReply('item', box);
  assert.equal(box.value, text);
  assert.equal(box.disabled, false);
  assert.equal(context.state.drafts.item, text);
  assert.equal(apiCalls.length, 0);
  assert.equal(await context.capture(text), false);
  assert.equal(apiCalls.length, 0);
  context.fetch = async () => ({ ok: true, json: async () => ({ url: '/console/api/files/0/token/message.txt' }) });
  await context.sendReply('item', box);
  assert.equal(box.value, '');
  assert.equal(apiCalls[0].body.files.length, 1);
  assert.ok(JSON.stringify(apiCalls[0].body).length < 4000);
  assert.equal(await context.capture(text), true);
  assert.equal(apiCalls[1].body.files.length, 1);
  assert.ok(JSON.stringify(apiCalls[1].body).length < 4000);
})().catch(err => { console.error(err); process.exitCode = 1; });
