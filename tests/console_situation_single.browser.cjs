// A thread with no parent and no children still shows its open decision request beside the
// conversation (#1396): the agent said "added to the decision card" and the person could not
// see it. Only the cards, no empty "running" or "decided". A thread without a request shows
// no panel. On a phone the panel is a tab, so the conversation keeps the screen.
// Run: NODE_PATH=<dir with playwright> node tests/console_situation_single.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const fixture = (id, title, status) => ({id, title, parent_id: null, status, thread_id: id.slice(1),
  bucket: 'me', running: false, priority: 2, focus: 100, last_activity_at: '2026-11-15T09:00:00Z'});
const items = [fixture('t41', 'tailscale を入れられない端末から Console に入る', 'waiting'), fixture('t42', 'ほかの単独スレッド', 'idle')];
const card = {request_id: 51, id: 't41', thread_id: '41', item_title: items[0].title, kind: 'waiting', origin: 'mark',
  reasons: [], since: '2026-11-15T08:00:00Z', place: null, parent_id: null, files: [], material: 'inline',
  card: {title: '一時的に公開する方式を選ぶ', context: 'Tailscale を入れられない端末から Console に入る件。後から戻せる。',
    options: [{label: 'A Cloudflare Access', effect: '今の認証のまま外から入れる', recommended: true},
      {label: 'B 見送る', effect: '何も変えない', recommended: false}], deadline: '急ぎではない'},
  detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'e', materials: 'f', deadline: 'g'}};
const secretary = {decide: [card], decide_more: 0, review: [], review_more: 0, unexplained: [], unexplained_more: 0,
  working: [], working_more: 0, done_today: [], calm: false};
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
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
      let data = {};
      if (pathname.endsWith('/reply')) { sent.push(route.request().postDataJSON()); data = {delivery: 'queued'}; }
      else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      else if (pathname.endsWith('/situation')) data = {situation: null, waits: []};
      else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary};
      else if (pathname.endsWith('/messages')) data = {messages: [{id: 1, kind: 'agent', author: 'AI', content: '判断カードに追加しました。', created_at: '2026-11-15T08:00:00Z'}]};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.removeItem('situationHidden'); localStorage.setItem('consoleMode', 'classic'); });
    await page.reload();
    await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length === 2);

    // No request: no panel.
    await page.evaluate(() => openItem('t42'));
    await page.waitForTimeout(400);
    assert.equal(await page.locator('#d-side').isVisible(), false, 'nothing to show, no panel');

    await page.evaluate(() => openItem('t41'));
    if (width <= 900) {
      // A phone: the panel is a tab with the count; the conversation keeps the screen until asked.
      const tab = page.locator('#d-tabs button', {hasText: '1'}).first();
      await tab.waitFor();
      if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/single-${width}-chat.png`});
      await tab.click();
    }
    const asks = page.locator('#d-side .sit-section.asks');
    await asks.locator('.sec-card').waitFor();
    assert.match(await asks.innerText(), /一時的に公開する方式を選ぶ/);
    assert.equal(await page.locator('#d-side .sit-section.running').count(), 0, 'no empty running');
    assert.equal(await page.locator('#d-side .sit-section.decided').count(), 0, 'no empty decided');
    assert.equal(await page.locator('#d-side-reply').isVisible(), false, 'the conversation has its own reply box');
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/single-${width}.png`});
    await asks.getByRole('button', {name: 'A Cloudflare Access'}).click();
    await page.locator('#d-side .sec-card.sent').waitFor();
    assert.equal(sent.at(-1).request_id, 51);
    assert.deepEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log('A single thread shows its decision request beside the conversation: passed');
})().catch((e) => { console.error(e); process.exit(1); });
