// Canvas mode, part two (#1360): the focus session and today's timetable.
// Covers: the prepared-session banner, the session in the secretary's order with its material,
// deciding with a key (the answer reaches the asking thread), S for later, the summary sent at
// the end, the schedule widget's three lanes, and any widget full screen (Esc closes).
// Run: NODE_PATH=<dir with playwright> node tests/console_canvas_session.browser.cjs
// SHOTS=<dir> also saves screenshots at 1280 and 390 pixels wide.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const item = (id, title, status, extra = {}) => ({id: `t${id}`, thread_id: String(id), title, status,
  bucket: status === 'running' ? 'ai' : 'me', running: status === 'running', priority: 2, focus: 100,
  last_activity_at: '2026-11-14T11:00:00Z', ...extra});
const items = [item(900, '🗂 Secretary (canvas)', 'idle', {bucket: 'idle', canvas_secretary: true}),
  item(11, 'GA v2 統括', 'waiting'), item(12, 'Ebi Workspace 販売ページ', 'review'), item(13, 'YouTube 字幕', 'running')];
const card = (rid, id, title, kind, options) => ({request_id: rid, id: `t${id}`, thread_id: String(id), item_title: title,
  kind, origin: 'mark', reasons: [], since: '2026-11-14T10:00:00Z', place: null, parent_id: null,
  card: {title: kind === 'review' ? '販売ページの差分を見て反映するか' : 'hccjp を消す前に、中のデータを書き出して残しますか？',
    context: kind === 'review' ? '公開後も元に戻せます。' : '11/17 に削除します。消すとほぼ戻せません。', options, deadline: '11/17 削除前'},
  detail: {about: 'a', now: 'b', ask: 'c', choices: 'd', recommendation: 'おすすめは A', materials: 'm', deadline: 'g'}});
const cards = [
  card(41, 12, 'Ebi Workspace 販売ページ', 'review', [{label: 'A 反映する', effect: '今夜公開', recommended: true}, {label: 'B 直す', effect: '明日', recommended: false}]),
  card(40, 11, 'GA v2 統括', 'waiting', [{label: 'A 残さない', effect: '追加作業なし', recommended: true}, {label: 'B 書き出す', effect: '30分〜1時間', recommended: false}]),
];
const summary = {decide: [cards[1]], decide_more: 0, review: [cards[0]], review_more: 0, unexplained: [], unexplained_more: 0,
  working: [], working_more: 0, done_today: [], calm: false};
const session = {title: 'GA v2 と販売ページ', minutes: 15, prepared_at: '2026-11-14T11:00:00Z', items: [
  {thread_id: '11', group: 'GA v2 本番', why: '期限が近い順', materials: [
    {kind: 'table', title: '中に残っているもの', data: {columns: ['種類', '件数'], rows: [['DB items', '約60件'], ['ファイル', '356個']]}},
    {kind: 'timeline', title: '人の受験記録', data: {entries: [{time: '11/6', label: '受験開始', state: 'done', minutes: null, thread_id: null}]}}]},
  {thread_id: '12', group: 'Ebi Workspace', why: '', materials: []}]};
const pad = (n) => String(n).padStart(2, '0');
const now = new Date();
const at = (offset) => { const d = new Date(now.getTime() + offset * 60000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const schedule = {key: 'today', kind: 'schedule', title: '今日の時間割', size: 'l', note: '空き時間に決めごとを詰めました', updated_at: '2026-11-14T11:00:00Z', actions: [],
  data: {lanes: {you: [{start: at(-60), end: at(-20), label: '夕食（カレンダー）', sub: null, thread_id: null},
    {start: at(5), end: at(20), label: '決めごと2件（15分）', sub: 'hccjp・販売ページ', thread_id: '11'}],
    ai: [{start: at(-90), end: at(40), label: 'YouTube 字幕 3本', sub: '62%', thread_id: '13'}],
    external: [{start: at(-120), end: at(30), label: 'GA v2 本番 凍結（〜11/17）', sub: null, thread_id: null}]}}};
const widgets = [schedule, {key: 'now', kind: 'markdown', title: '今やること', size: 'l', note: '', updated_at: '2026-11-14T11:00:00Z',
  data: {text: '集中セッションで2件決める（15分）'}, actions: []}];
const status = {item_id: 't900', running: false, paused: false, pending: 0,
  usage: {turns: 1, wakes: 0, input_tokens: 0, max_wakes: 24}, min_interval: 600, available: true};

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const run = async (width, height) => {
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
    const errors = [], calls = [];
    let plan = session;
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
      if (pathname.endsWith('/canvas/session/finish')) { plan = null; data = {status: 'finished', told: true}; }
      else if (pathname.endsWith('/canvas')) data = {widgets, secretary: status, session: plan};
      else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary: summary};
      else if (pathname.endsWith('/messages')) data = {messages: []};
      else if (pathname.endsWith('/reply')) data = {status: 'delivered'};
      else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
    await page.reload();
    await page.locator('.cv-grid').waitFor();

    // The timetable: three lanes, every block, a line at now.
    assert.equal(await page.locator('.cv-schedule .cv-s-lane').count(), 3);
    assert.equal(await page.locator('.cv-schedule .cv-s-block').count(), 4);
    assert.ok(await page.locator('.cv-schedule .cv-s-now').count() >= 1, 'now is marked');
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/canvas2-${width}.png`});

    // Full screen, then Esc back.
    await page.locator('.cv-w[data-key="today"] .cv-expand').click();
    await page.locator('#cv-full .cv-schedule').waitFor();
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/schedule-full-${width}.png`});
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('cv-full'));
    assert.equal(await page.locator('body.canvas').count(), 1, 'Esc closed only the full view');

    // The prepared session is offered; it runs in the secretary's order with its material.
    await page.locator('.cv-banner button').click();
    await page.locator('#cv-session').waitFor();
    assert.match(await page.locator('.cv-ss-title').innerText(), /hccjp/);
    assert.equal(await page.locator('.cv-ss-materials .cv-table td', {hasText: '356個'}).count(), 1);
    assert.equal(await page.locator('.cv-ss-tags .tag.hot').count(), 1, 'cannot be undone is flagged');
    assert.match(await page.locator('.cv-ss-side').innerText(), /販売ページの差分を見て反映するか/);
    assert.match(await page.locator('.cv-ss-side').innerText(), /YouTube 字幕/);
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/session-${width}.png`});

    await page.keyboard.press('1');
    await page.waitForFunction(() => /販売ページ/.test(document.querySelector('.cv-ss-title')?.textContent || ''));
    const reply = calls.find((c) => c.path.endsWith('/items/t11/reply'));
    assert.equal(reply.body.request_id, 40);
    assert.match(reply.body.text, /A 残さない — 追加作業なし/);
    assert.match(await page.locator('.cv-ss-side').innerText(), /A 残さない/);
    assert.equal(await page.locator('.cv-ss-tags .tag.hot').count(), 0, '"can be undone" is not flagged');

    await page.keyboard.press('s');
    await page.locator('.cv-ss-done').waitFor();
    await page.locator('.cv-ss-done button').click();
    await page.waitForFunction(() => !document.getElementById('cv-session'));
    const finish = calls.find((c) => c.path.endsWith('/canvas/session/finish'));
    assert.deepEqual(finish.body.decided.map((d) => d.answer), ['A 残さない']);
    assert.equal(finish.body.later.length, 1);
    assert.equal(calls.filter((c) => c.path.endsWith('/items/t12/reply')).length, 0, 'later sends nothing');
    assert.deepEqual(errors, []);
    await page.close();
  };
  await run(1280, 800);
  await run(390, 844);
  await browser.close();
  console.log('Canvas focus session and timetable: order, material, keys, summary, lanes, full screen: passed');
})().catch((e) => { console.error(e); process.exit(1); });
