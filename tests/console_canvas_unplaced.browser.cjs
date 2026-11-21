// Canvas mode (#1499): an open decision request shows on the canvas even when the resident
// secretary has not placed a decision widget for it (it wakes a few times a day at most, so a card
// opened after its last wake would otherwise wait for the next day). Cards it did place stay in
// their widget and are not repeated; a card that closes leaves without a wake; the rest fold
// after three. Run: NODE_PATH=<dir with playwright> node tests/console_canvas_unplaced.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const item = (id, title, status, extra = {}) => ({id: `t${id}`, thread_id: String(id), title, status,
  bucket: status === 'running' ? 'ai' : 'me', running: status === 'running', priority: 2, focus: 100,
  last_activity_at: '2026-11-14T11:00:00Z', ...extra});
const items = [item(900, '🗂 Secretary (canvas)', 'idle', {bucket: 'idle', canvas_secretary: true}),
  item(11, 'Placed thread', 'waiting'), item('1573596993845076108', 'Routine check (no parent)', 'waiting'),
  item(13, 'Third', 'waiting'), item(14, 'Fourth', 'waiting'), item(15, 'Fifth', 'action')];
const card = (rid, thread, title, kind = 'waiting') => ({request_id: rid, id: `t${thread}`, thread_id: String(thread),
  item_title: title, kind, origin: 'mark', reasons: [], since: '2026-11-14T10:00:00Z', place: null, parent_id: null,
  card: {title: `Q ${title}`, context: 'ctx', options: [{label: `Yes ${rid}`, effect: 'e', recommended: true},
    {label: `No ${rid}`, effect: 'e', recommended: false}], deadline: 'today'}, detail: {}});
const routine = card(231, '1573596993845076108', 'Routine check (no parent)');
const all = [card(31, 11, 'Placed thread'), routine, card(33, 13, 'Third'), card(34, 14, 'Fourth'),
  card(35, 15, 'Fifth', 'action')];
const summary = (cards) => ({decide: cards, decide_more: 0, review: [], review_more: 0, unexplained: [],
  unexplained_more: 0, working: [], working_more: 0, done_today: [], calm: false});
const widgets = [
  {key: 'now', kind: 'markdown', title: 'Now', size: 'l', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {text: 'do the placed one'}, actions: []},
  {key: 'd-11', kind: 'decision', title: 'Placed', size: 'm', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {thread_id: '11'}, actions: []},
];
const status = {item_id: 't900', running: false, paused: false, pending: 0,
  usage: {turns: 16, wakes: 8, input_tokens: 0, max_wakes: 8}, min_interval: 600, available: true};

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, locale: 'ja-JP'});
  const errors = [];
  let open = all;
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('http://console.test/**', async (route) => {
    const req = route.request();
    const pathname = new URL(req.url()).pathname;
    if (pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8')});
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
      return route.fulfill({contentType: type, body: fs.readFileSync(root + file)});
    }
    let data = {};
    if (pathname.endsWith('/canvas') && req.method() === 'GET') data = {widgets, secretary: status};
    else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary: summary(open)};
    else if (pathname.endsWith('/messages')) data = {messages: []};
    else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
  await page.reload();
  await page.locator('.cv-grid').waitFor();

  // The secretary placed only thread 11; the four others still show, with the same card.
  const section = page.locator('.cv-unplaced');
  await section.waitFor();
  assert.match(await section.locator('h2').innerText(), /4件/);
  if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/canvas-unplaced.png`});
  assert.equal(await section.locator('.sec-card').count(), 3, 'three first, the rest folded');
  assert.match(await section.innerText(), /Q Routine check \(no parent\)/, 'a routine thread without a parent shows');
  assert.equal(await section.getByText('Q Placed thread').count(), 0, 'a placed card is not repeated');
  assert.equal(await page.locator('.cv-k-decision .sec-card').count(), 1, 'the placed card stays in its widget');
  await section.getByRole('button', {name: 'ほか 1 件を表示'}).click();
  await page.waitForFunction(() => document.querySelectorAll('.cv-unplaced .sec-card').length === 4);

  // The card is answered on the board: it leaves the canvas at the next refresh, no wake needed.
  open = all.filter((c) => c.request_id !== 231);
  await page.evaluate(() => refresh());
  await page.waitForFunction(() => !/Routine check/.test(document.querySelector('.cv-unplaced')?.innerText || ''));
  assert.match(await section.locator('h2').innerText(), /3件/);

  // Only placed cards open: nothing extra is drawn.
  open = [all[0]];
  await page.evaluate(() => refresh());
  await page.waitForFunction(() => !document.querySelector('.cv-unplaced'));
  assert.equal(await page.locator('.cv-k-decision .sec-card').count(), 1);

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('console_canvas_unplaced: ok');
})().catch((err) => { console.error(err); process.exit(1); });
