// The screen AI (#1401): chosen on the canvas, carried by every send from it, never stored.
// Leaving the canvas withdraws it; the classic screen sends without it; a reload forgets it.
// Run: NODE_PATH=<dir with playwright> node tests/console_screen_ai.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const item = (id, title, status, extra = {}) => ({id: `t${id}`, thread_id: String(id), title, status, bucket: 'me',
  running: false, priority: 2, focus: 100, last_activity_at: '2026-11-15T09:00:00Z', ...extra});
const items = [item(900, '🗂 Secretary (canvas)', 'idle', {bucket: 'idle', canvas_secretary: true}), item(11, 'GA v2', 'waiting')];
const card = {request_id: 61, id: 't11', thread_id: '11', item_title: 'GA v2', kind: 'waiting', origin: 'mark', reasons: [],
  since: '2026-11-15T08:00:00Z', place: null, parent_id: null, files: [], material: 'inline',
  card: {title: '公開するか', context: '戻せる', deadline: '今日', options: [{label: 'A する', effect: '公開', recommended: true},
    {label: 'B しない', effect: '待つ', recommended: false}]}, detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'e', materials: 'f', deadline: 'g'}};
const summary = {decide: [card], decide_more: 0, review: [], review_more: 0, unexplained: [], unexplained_more: 0, working: [], working_more: 0, done_today: [], calm: false};
const widgets = [{key: 'now', kind: 'markdown', title: 'いまやること', size: 'l', note: '', updated_at: '2026-11-15T09:00:00Z', thread_id: null,
  data: {text: 'x'}, actions: [{label: 'あとで', say: '後で'}]}, {key: 'd11', kind: 'decision', title: '判断', size: 'm', note: '',
  updated_at: '2026-11-15T09:00:00Z', thread_id: null, data: {thread_id: '11'}, actions: []}];
const status = {item_id: 't900', running: false, paused: false, pending: 0, usage: {turns: 0, wakes: 0, input_tokens: 0, max_wakes: 8}, min_interval: 600, available: true};
const catalogs = {
  claude: {backends: ['claude', 'codex'], models: [{value: 'sonnet'}, {value: 'opus'}], efforts: ['low', 'high'], connections: [], default: {backend: 'claude', model: 'sonnet', effort: 'medium'}},
  codex: {backends: ['claude', 'codex'], models: [{value: 'gpt-6-astra'}], efforts: ['low', 'high'], connections: [{id: 'subscription', label: 'ChatGPT'}, {id: 'azure', label: 'Azure'}], default: {backend: 'claude', model: 'sonnet', effort: 'medium'}},
};
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1280, height: 800}, locale: 'ja-JP'});
  const errors = [], calls = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('http://console.test/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const pathname = url.pathname;
    if (pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8')});
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
      return route.fulfill({contentType: type, body: fs.readFileSync(root + file)});
    }
    let data = {};
    if (req.method() !== 'GET') calls.push({path: pathname, body: req.postDataJSON()});
    if (pathname.endsWith('/canvas/ai-catalog')) data = catalogs[url.searchParams.get('backend')];
    else if (pathname.endsWith('/canvas')) data = {widgets, secretary: status, session: null};
    else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary: summary};
    else if (pathname.endsWith('/messages')) data = {messages: []};
    else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
    else data = {status: 'delivered'};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
  await page.reload();
  await page.locator('.cv-grid').waitFor();
  const bar = page.locator('.cv-head .sai-bar');
  await bar.waitFor();
  assert.match(await bar.innerText(), /この画面のAI/);
  assert.match(await bar.innerText(), /claude · sonnet · medium/, 'shows the default when nothing is chosen');
  assert.equal(await bar.evaluate((n) => n.classList.contains('custom')), false);

  // Choose codex, a model and a connection: the bar stands out.
  await bar.locator('select').first().selectOption('codex');
  await page.locator('.cv-head .sai-bar.custom').waitFor();
  await page.locator('.cv-head .sai-bar select[aria-label="モデル"]').selectOption('gpt-6-astra');
  await page.locator('.cv-head .sai-bar select[aria-label="接続"]').selectOption('azure');
  const expected = {backend: 'codex', model: 'gpt-6-astra', connection: 'azure'};
  const heartbeat = calls.filter((c) => c.path.endsWith('/canvas/screen-ai')).at(-1);
  assert.deepEqual(heartbeat.body.ai, expected, 'the secretary gets it for its wakes');
  if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/screen-ai-1280.png`});

  // Every send from the screen carries it: a decision card, a widget button, the reply box.
  await page.locator('.cv-k-decision').getByRole('button', {name: 'A する'}).click();
  await page.waitForFunction(() => document.querySelector('.cv-k-decision .sec-card.sent'));
  assert.deepEqual(calls.find((c) => c.path.endsWith('/items/t11/reply')).body.ai, expected);
  await page.locator('.cv-w[data-key="now"] .cv-actions button').click();
  await page.waitForTimeout(300);
  assert.deepEqual(calls.find((c) => c.path.endsWith('/canvas/widgets/now/action')).body.ai, expected);
  await page.waitForFunction(() => state.open === 't900');
  await page.locator('#detail textarea').first().fill('秘書へ');
  await page.locator('#detail textarea').first().press('Control+Enter');
  await page.waitForTimeout(300);
  assert.deepEqual(calls.filter((c) => c.path.endsWith('/items/t900/reply')).at(-1).body.ai, expected);

  // Nothing is stored in the browser.
  const stored = await page.evaluate(() => JSON.stringify(localStorage));
  assert.ok(!/gpt-6-astra|azure/.test(stored), 'not in localStorage');

  // Leaving the canvas withdraws it; the classic screen sends without it.
  await page.getByRole('button', {name: '↩ 通常の画面'}).click();
  await page.waitForTimeout(300);
  assert.equal(calls.filter((c) => c.path.endsWith('/canvas/screen-ai')).at(-1).body.ai, null);
  await page.evaluate(() => openItem('t11'));
  await page.locator('#detail textarea').first().fill('通常画面から');
  await page.locator('#detail textarea').first().press('Control+Enter');
  await page.waitForTimeout(300);
  assert.equal(calls.filter((c) => c.path.endsWith('/items/t11/reply')).at(-1).body.ai, undefined);

  // Back in the canvas after a reload: forgotten.
  await page.evaluate(() => localStorage.setItem('consoleMode', 'canvas'));
  await page.reload();
  await page.locator('.cv-head .sai-bar').waitFor();
  assert.equal(await page.locator('.cv-head .sai-bar.custom').count(), 0);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Screen AI: chosen, carried by every send from the screen, never stored, withdrawn on leave: passed');
})().catch((e) => { console.error(e); process.exit(1); });
