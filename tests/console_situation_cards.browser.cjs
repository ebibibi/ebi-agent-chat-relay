// A family's "needed from you" shows its open decision requests as the secretary's cards
// (ADR-0024): a child's question appears once, in the same card, answered from the panel.
// Run: NODE_PATH=<dir with playwright> node tests/console_situation_cards.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const fixture = (id, title, parent_id, status) => ({id, title, parent_id, status, thread_id: id,
  bucket: 'me', running: false, priority: 2, focus: 100, last_activity_at: '2026-11-14T11:00:00Z'});
const items = [fixture('p', 'サイト公開をまとめる', null, 'waiting'),
  fixture('c', 'データ移行', 'p', 'waiting'), fixture('w', '古い質問のスレッド', 'p', 'waiting')];
const card = {request_id: 21, id: 'c', thread_id: 'c', item_title: 'データ移行', kind: 'waiting', origin: 'mark',
  reasons: [], since: '2026-11-14T10:00:00Z', place: null, parent_id: 'p',
  card: {title: '移行前に旧データを書き出すか', context: '旧環境を11/17に消す。消すとほぼ戻せない。',
    options: [{label: 'A 消す', effect: '追加作業なし', recommended: true},
      {label: 'B 書き出す', effect: '30分の作業', recommended: false}], deadline: '11/17の削除の前'},
  detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'e', materials: 'f', deadline: 'g'}};
const secretary = {decide: [card], decide_more: 0, review: [], review_more: 0, unexplained: [], unexplained_more: 0,
  working: [], working_more: 0, done_today: [], calm: false};
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, locale: 'ja-JP'});
  const errors = [], sent = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('http://console.test/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8')});
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
      return route.fulfill({contentType: type, body: fs.readFileSync(root + file)});
    }
    const id = decodeURIComponent((pathname.match(/\/items\/([^/]+)\//) || [])[1] || '');
    let data = {};
    if (pathname.endsWith('/reply')) { sent.push({id, body: route.request().postDataJSON()}); data = {delivery: 'queued'}; }
    else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
    else if (pathname.endsWith('/situation')) data = {situation: {asks: ['古い書き方の質問'], doing: [], decided: [],
      updated_at: '2026-11-14T11:00:00Z'}, waits: []};
    else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary};
    else if (pathname.endsWith('/messages')) data = {messages: []};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.removeItem('situationHidden'); });
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length === 3);
  await page.evaluate(() => { setView('tree'); return openItem('p'); });

  const asks = page.locator('#d-side .sit-section.asks');
  await asks.locator('.sec-card').waitFor();
  assert.equal(await asks.locator('.sec-card').count(), 1, 'one card for the child question');
  assert.match(await asks.locator('.sec-card').innerText(), /移行前に旧データを書き出すか/);
  // The child with a request is not listed again; the one without is marked as unexplained.
  assert.equal(await asks.locator('.sit-ask', {hasText: 'データ移行'}).count(), 0);
  assert.match(await asks.locator('.sit-ask', {hasText: '古い質問のスレッド'}).innerText(), /説明なし/);
  assert.match(await asks.locator('.sit-ask', {hasText: '古い書き方の質問'}).innerText(), /説明なし/);

  // Answered from the panel, it goes to the child that asked, closing that request.
  await asks.getByRole('button', {name: 'A 消す'}).click();
  await page.locator('#d-side .sec-card.sent').waitFor();
  assert.deepEqual(sent.at(-1), {id: 'c', body: {text: '「移行前に旧データを書き出すか」への回答: A 消す — 追加作業なし', request_id: 21}});
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Situation panel cards: one card per request, unexplained apart, answered in place: passed');
})().catch((e) => { console.error(e); process.exit(1); });
