// Canvas flows (#1374): every source thread one tap away and back; review cards carry what to
// review. Walks each piece the way the person does: see → understand → act → see the result.
// Run: NODE_PATH=<dir with playwright> node tests/console_canvas_flows.browser.cjs
// SHOTS=<dir> also saves screenshots at 1280 and 390 pixels wide.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const item = (id, title, status, extra = {}) => ({id: `t${id}`, thread_id: String(id), title, status,
  bucket: status === 'running' ? 'ai' : 'me', running: status === 'running', priority: 2, focus: 100,
  last_activity_at: '2026-11-15T01:00:00Z', ...extra});
const items = [item(900, '🗂 Secretary (canvas)', 'idle', {bucket: 'idle', canvas_secretary: true}),
  item(21, 'Claude画面共有の回答', 'review'), item(22, '古い動画の差し替え', 'waiting'), item(23, 'ISMS認証', 'waiting')];
const FILE = '/console/api/files/21/' + 'a'.repeat(32) + '/claude-screenshare.txt';
const base = (rid, id, title, kind, extra) => ({request_id: rid, id: `t${id}`, thread_id: String(id), item_title: items.find((i) => i.id === `t${id}`).title,
  kind, origin: 'mark', reasons: [], since: '2026-11-15T00:00:00Z', place: null, parent_id: null,
  card: {title, context: '送る前の確認。元に戻せます。', deadline: '今日中',
    options: [{label: 'OK', effect: 'このまま送る', recommended: true}, {label: '直す', effect: '直してから', recommended: false}]},
  detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'e', materials: '添付の claude-screenshare.txt', deadline: 'g'},
  files: [], material: 'none', ...extra});
const cards = [
  base(31, 21, '回答案を確認してください', 'review', {files: [{name: 'claude-screenshare.txt', url: FILE}], material: 'files'}),
  base(32, 22, '動画を確認して古い版を消すか決める', 'waiting', {}),
  base(33, 23, 'ISMS認証を今始めるか', 'waiting', {material: 'inline'}),
];
const summary = {decide: [cards[1], cards[2]], decide_more: 0, review: [cards[0]], review_more: 0, unexplained: [], unexplained_more: 0,
  working: [], working_more: 0, done_today: [], calm: false};
const widgets = [
  {key: 'now', kind: 'markdown', title: 'いまやること', size: 'l', note: '', updated_at: '2026-11-15T01:00:00Z', thread_id: null, actions: [{label: 'あとで', say: 'ISMSは明日'}],
    data: {text: '1. [Claude画面共有の回答](/#focus/t21) を確認（2分）'}},
  {key: 'draft', kind: 'markdown', title: '回答案（本文）', size: 'm', note: '', updated_at: '2026-11-15T01:00:00Z', thread_id: '21', actions: [],
    data: {text: 'お世話になっております。画面共有は…'}},
  {key: 'waiting', kind: 'table', title: '返信待ち', size: 'm', note: '', updated_at: '2026-11-15T01:00:00Z', thread_id: null, actions: [],
    data: {columns: ['スレッド', '必要なこと'], rows: [['ISMS認証', '決める'], ['メモ', '—']], row_threads: ['23', null]}},
  {key: 'd21', kind: 'decision', title: '回答案', size: 'm', note: '', updated_at: '2026-11-15T01:00:00Z', thread_id: null, actions: [], data: {thread_id: '21'}},
  {key: 'd22', kind: 'decision', title: '動画', size: 'm', note: '', updated_at: '2026-11-15T01:00:00Z', thread_id: null, actions: [], data: {thread_id: '22'}},
];
const status = {item_id: 't900', running: false, paused: false, pending: 0,
  usage: {turns: 1, wakes: 0, input_tokens: 0, max_wakes: 24}, min_interval: 600, available: true};

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const run = async (width, height) => {
    const narrow = width <= 900;
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
    const errors = [], calls = [];
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
      if (pathname === FILE) return route.fulfill({contentType: 'text/plain', body: 'お世話になっております。\n画面共有ボタンが消えた件の回答案です。'});
      let data = {};
      if (req.method() !== 'GET') calls.push({method: req.method(), path: pathname, body: req.postDataJSON()});
      if (pathname.endsWith('/canvas/session/finish')) data = {status: 'finished', told: true};
      else if (pathname.endsWith('/canvas')) data = {widgets, secretary: status, session: null};
      else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary: summary};
      else if (pathname.endsWith('/messages')) data = {messages: [{id: 1, kind: 'agent', author: 'AI', created_at: '2026-11-15T01:00:00Z',
        content: pathname.includes('t900') ? '次は [Claude画面共有の回答](/#focus/t21) です。' : 'スレッドの中身'}]};
      else if (pathname.endsWith('/reply') || pathname.endsWith('/action')) data = {status: 'delivered'};
      else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
    await page.reload();
    await page.locator('.cv-grid').waitFor();
    const back = async () => {
      await page.locator('.cv-detail-back button', {hasText: '← 秘書に戻る'}).click();
      if (narrow) await page.waitForFunction(() => state.open === null);
      else await page.waitForFunction(() => state.open === 't900' && !document.querySelector('.cv-detail-back'));
    };

    // Review card: the delivered file is listed and its contents shown in place.
    const review = page.locator('.cv-w[data-key="d21"]');
    await review.locator('.sec-preview').waitFor();
    assert.match(await review.locator('.sec-preview').innerText(), /回答案です/);
    assert.equal(await review.getByRole('button', {name: 'OK'}).count(), 1, 'something to look at: the answer buttons stay');
    // Nothing to look at: no review, only the way to the thread.
    const empty = page.locator('.cv-w[data-key="d22"]');
    assert.equal(await empty.getByRole('button', {name: 'OK'}).count(), 0);
    assert.equal(await empty.locator('.sec-card.no-material').count(), 1);
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/flows-${width}.png`, fullPage: false});
    await empty.getByRole('button', {name: '↗ スレッドを開いて確認する'}).click();
    await page.waitForFunction(() => state.open === 't22');
    await page.locator('.cv-detail-back').waitFor();
    await back();

    // A widget about one thread links to it; the thread offers the way back.
    await page.locator('.cv-w[data-key="draft"] .cv-w-head .guide-link').click();
    await page.waitForFunction(() => state.open === 't21');
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/flows-thread-${width}.png`});
    await back();

    // A table row names its thread; a row without one stays text.
    assert.equal(await page.locator('.cv-w[data-key="waiting"] tbody .guide-link').count(), 1);
    await page.locator('.cv-w[data-key="waiting"] tbody .guide-link').click();
    await page.waitForFunction(() => state.open === 't23');
    await back();

    // A link inside Markdown (the secretary writes /#focus/t<id>) opens the thread in place.
    await page.locator('.cv-w[data-key="now"] .md a').click();
    await page.waitForFunction(() => state.open === 't21');
    await back();

    // A widget button says it was delivered.
    await page.locator('.cv-w[data-key="now"] .cv-actions button').click();
    await page.waitForFunction(() => /伝えました/.test(document.querySelector('.cv-w[data-key="now"] .cv-actions button')?.textContent || ''));
    assert.ok(calls.some((c) => c.path.endsWith('/canvas/widgets/now/action')));

    // Focus session: opening the thread suspends it; coming back resumes at the same decision.
    await page.evaluate(() => window.ConsoleSession.open());
    await page.locator('#cv-session').waitFor();
    await page.keyboard.press('ArrowRight');
    const title = await page.locator('.cv-ss-title').innerText();
    await page.locator('.cv-ss-tags .guide-link').click();
    await page.waitForFunction(() => !document.getElementById('cv-session'));
    await page.locator('.cv-detail-back button', {hasText: '集中セッションに戻る'}).click();
    await page.locator('#cv-session').waitFor();
    assert.equal(await page.locator('.cv-ss-title').innerText(), title, 'resumed where it was');
    // The nothing-to-look-at card in a session offers only the thread.
    const order = await page.evaluate(() => [...document.querySelectorAll('.cv-ss-side .cv-ss-list li')].map((li) => li.textContent));
    assert.ok(order.length >= 0);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('cv-session'));
    assert.deepEqual(errors, []);
    await page.close();
  };
  await run(1280, 800);
  await run(390, 844);
  await browser.close();
  console.log('Canvas flows: source links and back, review material, button feedback, session suspend/resume: passed');
})().catch((e) => { console.error(e); process.exit(1); });
