// Secretary mode: the switch, the decision home, answering from a card, and the trial records.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_secretary.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

const ago = (minutes) => new Date(Date.now() - minutes * 60000).toISOString().replace(/\.\d+Z$/, 'Z');
const row = (id, title, status, bucket, extra = {}) =>
  ({id, thread_id: id.slice(1), title, status, bucket, priority: 2, tags: [], last_activity_at: ago(30), ...extra});
const items = [
  row('t1', 'Release 5.3', 'waiting', 'me', {focus: 220}),
  row('t2', 'Customer form', 'action', 'me', {focus: 120}),
  row('t3', 'Docs sync', 'running', 'ai', {running: true}),
  row('t4', 'Weekly digest', 'candidate', 'candidate'),
];
const secretary = {
  decide: [
    {id: 't1', title: 'Release 5.3', thread_id: '1', status: 'waiting', ask: 'Merge PR 12 and release 5.3?',
      why: 'CI is green; the release notes are drafted', suggest: 'Yes, merge and release', options: ['Yes, merge and release', 'Wait until Monday'],
      reasons: ['priority'], since: ago(90), place: null, parent_id: null},
    {id: 't2', title: 'Customer form', thread_id: '2', status: 'action', ask: 'Sign in to the portal and approve',
      why: null, suggest: null, options: [], reasons: [], since: ago(30), place: null, parent_id: null},
  ],
  more: 2, later: 1, review: [], review_more: 0,
  working: [{id: 't3', title: 'Docs sync', since: ago(3)}], working_more: 0,
  done_today: [{id: 't4', title: 'Weekly digest', since: ago(60), candidate: true}], calm: false,
};

async function open(t, storage = {}) {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, locale: 'ja-JP'});
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const posted = [];
  await page.route('http://console.test/**', async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), contentType:
        file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    if (req.method() === 'POST') posted.push({path: p, body: req.postDataJSON()});
    let data = {};
    if (p.endsWith('/board')) data = {items, slots: {}, places: [], views: [], secretary};
    else if (p.endsWith('/messages')) data = {messages: []};
    else if (p.endsWith('/workspace/plan')) data = {available: false};
    else if (p.endsWith('/workspace')) data = {available: false, entries: []};
    else if (p.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
    else if (p.endsWith('/reply')) data = {status: 'delivered'};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.addInitScript((s) => { for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); }, storage);
  await page.goto('http://console.test/');
  await page.waitForFunction(() => state.items.length === 4);
  return {page, posted, errors};
}

test('classic mode keeps its home and gains only the switch', {timeout: 30000}, async (t) => {
  const {page, errors} = await open(t);
  assert.equal(await page.evaluate(() => state.view), 'focus');
  assert.equal(await page.locator('.mode-switch').count(), 1);
  assert.equal(await page.locator('.sec-block').count(), 0);
  assert.doesNotMatch(await page.locator('#nav-views').innerText(), /🗂 秘書/);
  assert.deepEqual(errors, []);
});

test('the secretary home answers from a card and records the trial', {timeout: 30000}, async (t) => {
  const {page, posted, errors} = await open(t);
  await page.locator('.mode-switch').getByRole('button', {name: '秘書'}).click();
  await page.waitForFunction(() => location.hash === '#secretary');
  assert.equal(await page.locator('#view-title').innerText(), '🗂 秘書');
  const cards = page.locator('.sec-card');
  assert.equal(await cards.count(), 2);
  assert.match(await cards.first().innerText(), /Merge PR 12 and release 5\.3\?/);
  assert.match(await page.locator('.sec-decide').innerText(), /ほか 2 件/);
  assert.match(await page.locator('.sec-working').innerText(), /Docs sync/);
  assert.match(await page.locator('.sec-done').innerText(), /Weekly digest/);
  assert.equal(await page.locator('#empty').isHidden(), true);

  // One click sends the recommended answer to the thread, through the ordinary reply endpoint.
  await cards.first().locator('.sec-suggest').click();
  await page.locator('.sec-card.sent').waitFor();
  const reply = posted.find((p) => p.path.endsWith('/items/t1/reply'));
  assert.deepEqual(reply.body, {text: 'Yes, merge and release'});
  const answer = posted.find((p) => p.path.endsWith('/secretary/events') && p.body.kind === 'answer');
  assert.equal(answer.body.item_id, 't1');
  assert.equal(answer.body.mode, 'secretary');
  assert.ok(answer.body.waited_seconds >= 5300);

  // A few words of one's own go the same way.
  await cards.nth(1).locator('textarea').fill('承認しました');
  await cards.nth(1).getByRole('button', {name: '送る'}).click();
  await page.waitForFunction(() => document.querySelectorAll('.sec-card.sent').length === 2);
  assert.deepEqual(posted.find((p) => p.path.endsWith('/items/t2/reply')).body, {text: '承認しました'});

  // The original thread is one click away, and opening it is recorded.
  await cards.nth(1).getByRole('button', {name: /元のスレッドを開く/}).click();
  await page.waitForFunction(() => state.open === 't2');
  assert.ok(posted.some((p) => p.path.endsWith('/secretary/events') && p.body.kind === 'open' && p.body.item_id === 't2'));
  assert.deepEqual(errors, []);
});

test('secretary mode is the home after a reload, and switching back restores Focus', {timeout: 30000}, async (t) => {
  const {page} = await open(t, {consoleMode: 'secretary', view: 'me'});
  assert.equal(await page.evaluate(() => state.view), 'secretary');
  await page.locator('.mode-switch').getByRole('button', {name: '従来'}).click();
  await page.waitForFunction(() => state.view === 'focus');
  assert.equal(await page.locator('.sec-block').count(), 0);
});
