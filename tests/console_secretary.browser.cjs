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
  row('t5', 'Old thread', 'waiting', 'me', {focus: 90}),
];
const detail = {about: 'Release 5.3 of the relay.', now: 'CI is green and the notes are drafted.',
  ask: 'Merge PR 12 and publish?', choices: 'A merge now / B wait', recommendation: 'A: nothing is pending.',
  materials: '| check | result |\n|---|---|\n| CI | **green** |', deadline: 'Today; otherwise Monday.'};
const card = (requestId, id, kind, title, options, extra = {}) => ({request_id: requestId, id, thread_id: id.slice(1),
  item_title: items.find((i) => i.id === id).title, kind, origin: 'mark', reasons: [], since: ago(90), place: null,
  parent_id: null, detail, card: {title, context: 'The relay ships a release; it can be rolled back in a minute.',
    options, deadline: 'Today 18:00'}, ...extra});
const secretary = {
  decide: [
    card(11, 't1', 'waiting', 'Publish release 5.3 now?', [
      {label: 'A Merge', effect: 'Ships today; can be rolled back', recommended: true},
      {label: 'B Wait', effect: 'Ships Monday with the next batch', recommended: false}], {reasons: ['priority']}),
    card(12, 't2', 'action', 'Approve the customer form in the portal', [
      {label: 'Done', effect: 'The agent sends the form', recommended: false}]),
    card(13, 't1', 'waiting', 'Third question', [
      {label: 'Yes', effect: 'Go', recommended: true}, {label: 'No', effect: 'Stop', recommended: false}]),
    card(14, 't1', 'waiting', 'Fourth question', [
      {label: 'Yes', effect: 'Go', recommended: true}, {label: 'No', effect: 'Stop', recommended: false}]),
  ],
  decide_more: 0, review: [], review_more: 0,
  unexplained: [{id: 't5', title: 'Old thread', status: 'waiting', since: ago(600)}], unexplained_more: 0,
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
  await page.waitForFunction(() => state.items.length === 5);
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
  const cards = page.locator('.sec-decide .sec-card');
  assert.equal(await cards.count(), 3);  // the first three; the rest one click away
  const first = await cards.first().innerText();
  assert.match(first, /Publish release 5\.3 now\?/);
  assert.match(first, /何の話：The relay ships a release/);
  assert.match(first, /A Merge.*おすすめ/s);
  assert.match(first, /期限：Today 18:00/);
  assert.match(await page.locator('.sec-unexplained').innerText(), /Old thread/);
  assert.match(await page.locator('.sec-working').innerText(), /Docs sync/);
  assert.match(await page.locator('.sec-done').innerText(), /Weekly digest/);
  assert.equal(await page.locator('#empty').isHidden(), true);
  await page.locator('.sec-decide').getByRole('button', {name: 'ほか 1 件を表示'}).click();
  assert.equal(await cards.count(), 4);

  // The folded detail renders the seven parts, Markdown included.
  await cards.first().locator('summary').click();
  assert.match(await cards.first().locator('.sec-detail').innerText(), /6\. 判断材料/);
  assert.equal(await cards.first().locator('.sec-detail table').count(), 1);

  // One click answers: the question restated, the choice, and the request it closes.
  await cards.first().getByRole('button', {name: 'A Merge'}).click();
  await page.locator('.sec-card.sent').waitFor();
  const reply = posted.find((p) => p.path.endsWith('/items/t1/reply'));
  assert.deepEqual(reply.body, {text: '「Publish release 5.3 now?」への回答: A Merge — Ships today; can be rolled back',
    request_id: 11});
  const answer = posted.find((p) => p.path.endsWith('/secretary/events') && p.body.kind === 'answer');
  assert.equal(answer.body.item_id, 't1');
  assert.equal(answer.body.mode, 'secretary');
  assert.ok(answer.body.waited_seconds >= 5300);

  // An answer of one's own goes the same way.
  await cards.nth(1).getByRole('button', {name: '✏️ 別の答え'}).click();
  await cards.nth(1).locator('textarea').fill('承認しました');
  await cards.nth(1).getByRole('button', {name: '送る'}).click();
  await page.waitForFunction(() => document.querySelectorAll('.sec-card.sent').length === 2);
  assert.deepEqual(posted.find((p) => p.path.endsWith('/items/t2/reply')).body,
    {text: '「Approve the customer form in the portal」への回答: 承認しました', request_id: 12});

  // The original thread is one click away, and opening it is recorded.
  await cards.nth(1).getByRole('button', {name: /Customer form/}).click();
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
