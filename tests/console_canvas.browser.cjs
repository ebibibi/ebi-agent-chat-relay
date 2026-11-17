// Canvas mode (ADR-0025): the secretary's widgets fill the left, its conversation the right.
// Covers: starting the secretary, every widget kind drawn as text, a decision card answering the
// asking thread, a widget button reaching the secretary, dismissing, back to the classic screen,
// and the narrow layout. Run: NODE_PATH=<dir with playwright> node tests/console_canvas.browser.cjs
// SHOTS=<dir> also saves screenshots at 1280 and 390 pixels wide.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const item = (id, title, status, extra = {}) => ({id: `t${id}`, thread_id: String(id), title, status,
  bucket: status === 'running' ? 'ai' : 'me', running: status === 'running', priority: 2, focus: 100,
  last_activity_at: '2026-11-14T11:00:00Z', ...extra});
const items = [item(900, '🗂 Secretary (canvas)', 'idle', {bucket: 'idle', canvas_secretary: true, context: {pct: 12, used: 24000, window: 200000}}),
  item(11, 'Ebi Workspace 販売ページの更新', 'waiting'), item(12, 'GA v2 統括', 'running')];
const card = {request_id: 31, id: 't11', thread_id: '11', item_title: 'Ebi Workspace 販売ページの更新', kind: 'waiting',
  origin: 'mark', reasons: [], since: '2026-11-14T10:00:00Z', place: null, parent_id: null,
  card: {title: '販売ページの差分を反映するか', context: '無料部分の2か所だけ変わる。戻せる。',
    options: [{label: 'A 反映する', effect: '今夜公開。戻せる', recommended: true},
      {label: 'B 直す', effect: '文面を直してから', recommended: false}], deadline: '今夜中'},
  detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'e', materials: 'f', deadline: 'g'}};
const secretarySummary = {decide: [card], decide_more: 0, review: [], review_more: 0, unexplained: [],
  unexplained_more: 0, working: [], working_more: 0, done_today: [], calm: false};
const widgets = [
  {key: 'now', kind: 'markdown', title: '今やること', size: 'l', note: '決めること1件・5分', updated_at: '2026-11-14T11:00:00Z',
    data: {text: '**販売ページの差分**を見て決める（5分）<script>window.pwned = 1</script>'},
    actions: [{label: 'あとで', say: '販売ページは明日にして'}]},
  {key: 'd-11', kind: 'decision', title: '販売ページ', size: 'm', note: '', updated_at: '2026-11-14T11:00:00Z', data: {thread_id: '11'}, actions: []},
  {key: 'diff', kind: 'diff', title: '変わる所', size: 'm', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {diff: '@@ 更新履歴 @@\n- 2.3.1\n+ 2.4.0'}, actions: []},
  {key: 'plan', kind: 'timeline', title: '今夜の順番', size: 's', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {entries: [{time: '22:40', label: '販売ページ', state: 'now', minutes: 5, thread_id: '11'},
      {time: '22:45', label: '寝る', state: 'todo', minutes: null, thread_id: null}]}, actions: []},
  {key: 'ai', kind: 'agents', title: 'AIの仕事', size: 's', note: '', updated_at: '2026-11-14T11:00:00Z', data: {thread_ids: []}, actions: []},
  {key: 'n', kind: 'metric', title: '状態', size: 's', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {value: '1', label: '決めること', sub: 'AI作業中 1', percent: 30}, actions: []},
  {key: 't', kind: 'table', title: '表', size: 'm', note: '', updated_at: '2026-11-14T11:00:00Z',
    data: {columns: ['件名', '所要'], rows: [['販売ページ', '5分'], ['<b>x</b>']]}, actions: []},
];
const status = (started) => ({item_id: started ? 't900' : null, running: false, paused: false, pending: 0,
  usage: {turns: 3, wakes: 2, input_tokens: 120000, max_wakes: 24}, min_interval: 600, available: true});

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const run = async (width, height) => {
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
    const errors = [], calls = [];
    let started = false;
    let shown = widgets.map((w) => ({...w}));
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
      if (req.method() !== 'GET') calls.push({method: req.method(), path: pathname, body: req.postDataJSON()});
      if (pathname.endsWith('/canvas/secretary')) { started = true; data = {item_id: 't900'}; }
      else if (pathname.endsWith('/canvas') && req.method() === 'GET') data = {widgets: started ? shown : [], secretary: status(started)};
      else if (pathname.includes('/canvas/widgets/') && req.method() === 'DELETE') {
        shown = shown.filter((w) => !pathname.endsWith(`/${w.key}`)); data = {status: 'removed'};
      } else if (pathname.endsWith('/board')) data = {items: started ? items : items.slice(1), slots: {}, places: [], secretary: secretarySummary};
      else if (pathname.endsWith('/messages')) data = {messages: [{id: 1, kind: 'agent', author: 'AI', content: 'おかえりなさい。今夜決めることは1件、5分です。',
        created_at: '2026-11-14T11:00:00Z'}]};
      else if (pathname.endsWith('/reply') || pathname.endsWith('/action')) data = {status: 'delivered'};
      else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
    await page.reload();
    await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length >= 2);

    // Not started yet: the canvas offers to start it, and nothing else.
    await page.locator('.cv-start button').waitFor();
    assert.equal(await page.locator('body.canvas').count(), 1);
    assert.equal(await page.locator('#nav').isVisible(), false, 'the thread menu is hidden');
    await page.locator('.cv-start button').click();
    await page.locator('.cv-grid').waitFor();
    assert.ok(calls.some((c) => c.path.endsWith('/canvas/secretary')));

    assert.equal(await page.locator('.cv-w').count(), widgets.length);
    assert.equal(await page.evaluate(() => window.pwned), undefined, 'Markdown cannot run script');
    assert.equal(await page.locator('.cv-table td', {hasText: '<b>x</b>'}).count(), 1, 'cells are text');
    assert.match(await page.locator('.cv-usage').innerText(), /自動更新 2\/24回/);
    assert.match(await page.locator('.cv-agents').innerText(), /GA v2 統括/);
    assert.equal(await page.locator('.cv-diff .add').count(), 1);

    if (width > 900) {
      await page.waitForFunction(() => state.open === 't900');
      assert.equal(await page.locator('#detail').isVisible(), true, 'the secretary talks on the right');
    } else {
      assert.equal(await page.locator('#detail').isVisible(), false, 'narrow: the canvas comes first');
      await page.locator('.cv-chat').waitFor();
    }
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/canvas-${width}.png`, fullPage: false});

    // The decision card answers the asking thread, as in secretary mode.
    await page.locator('.cv-k-decision').getByRole('button', {name: 'A 反映する'}).click();
    await page.waitForFunction(() => document.querySelector('.cv-k-decision .sec-card.sent'));
    const reply = calls.find((c) => c.path.endsWith('/items/t11/reply'));
    assert.equal(reply.body.request_id, 31);

    // A widget button reaches the secretary as the person's message.
    await page.locator('.cv-w[data-key="now"] .cv-actions button').click();
    await page.waitForFunction(() => true);
    await page.waitForTimeout(200);
    const action = calls.find((c) => c.path.endsWith('/canvas/widgets/now/action'));
    assert.deepEqual(action.body, {index: 0});

    // Dismissing removes the widget.
    await page.locator('.cv-w[data-key="t"] .cv-x:not(.cv-expand)').click();
    await page.waitForFunction(() => !document.querySelector('.cv-w[data-key="t"]'));

    if (width <= 900) {
      await page.locator('.cv-chat').click();
      await page.waitForFunction(() => state.open === 't900');
      assert.equal(await page.locator('#detail').isVisible(), true);
      if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/canvas-${width}-chat.png`});
      await page.evaluate(() => closeItem());
      await page.waitForFunction(() => state.open === null);
    }

    // The wake limits change from the canvas, no restart.
    const answers = ['8', '15'];
    const onDialog = (d) => d.accept(answers.shift());
    page.on('dialog', onDialog);
    await page.locator('.cv-head .chip', {hasText: '⚙'}).click();
    await page.waitForFunction(() => true);
    await page.waitForTimeout(300);
    page.off('dialog', onDialog);
    const set = calls.find((c) => c.path.endsWith('/canvas/limits'));
    assert.deepEqual(set && set.body, {max_wakes_per_day: 8, min_interval: 900});

    // Back to the classic screen.
    await page.getByRole('button', {name: '↩ 通常の画面'}).click();
    await page.waitForFunction(() => !document.body.classList.contains('canvas'));
    assert.equal(await page.evaluate(() => localStorage.getItem('consoleMode')), 'classic');
    assert.deepEqual(errors, []);
    await page.close();
  };
  await run(1280, 800);
  await run(390, 844);
  await browser.close();
  console.log('Canvas mode: start, widgets as text, decisions, buttons, dismiss, narrow, back: passed');
})().catch((e) => { console.error(e); process.exit(1); });
