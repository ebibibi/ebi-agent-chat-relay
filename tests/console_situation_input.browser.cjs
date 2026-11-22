// The focused custom-answer editor stays connected across real board refreshes.
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

  // Typing survives actual board refreshes that change the situation and rebuild its cards.
  await asks.getByRole('button', {name: '✏️ 別の答え'}).click();
  const editor = asks.locator('.sec-reply');
  await editor.fill('検討中の回答をここに書きます');
  await editor.evaluate(n => { n.focus(); n.setSelectionRange(2, 5, 'backward'); window.originalEditor = n;
    window.editorBlurs = 0; n.addEventListener('blur', () => window.editorBlurs++);
    n.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true, data: 'へんかん'})); });
  for (let i = 0; i < 4; i++) {
    items[2].title = `更新された進捗 ${i}`;
    await page.evaluate(() => refresh());
    await asks.getByText(`更新された進捗 ${i}`, {exact: false}).first().waitFor();
    assert.deepEqual(await editor.evaluate(n => ({same: n === window.originalEditor,
      focused: document.activeElement === n, value: n.value, start: n.selectionStart,
      end: n.selectionEnd, direction: n.selectionDirection, blurs: window.editorBlurs})),
    {same: true, focused: true, value: '検討中の回答をここに書きます', start: 2, end: 5, direction: 'backward', blurs: 0});
  }
  await editor.evaluate(n => n.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true, data: '変換'})));
  assert.ok(await editor.evaluate(n => n.rows >= 6 && n.getBoundingClientRect().height >= 140), 'six-line card editor');
  assert.ok(await page.locator('#sit-reply').evaluate(n => n.rows >= 6 && n.getBoundingClientRect().height >= 140), 'six-line panel composer');
  // Other cards update while this one is being composed; this is not a frozen panel.
  const otherCard = {...card, request_id: 22, id: 'w', thread_id: 'w',
    card: {...card.card, title: '追加された判断事項'}};
  secretary.decide.push(otherCard);
  await page.evaluate(() => refresh());
  await asks.getByText('追加された判断事項', {exact: false}).first().waitFor();
  assert.equal(await editor.evaluate(n => n === window.originalEditor && document.activeElement === n), true);
  secretary.decide.pop();
  await page.evaluate(() => refresh());
  assert.equal(await asks.locator('.sec-card').count(), 1);
  // A deliberate focus change must stay deliberate: refreshing must not grab focus back.
  await page.locator('#sit-reply').fill('親への返信');
  await page.evaluate(() => refresh());
  assert.equal(await page.locator('#sit-reply').evaluate(n => document.activeElement === n), true);
  assert.equal(await editor.inputValue(), '検討中の回答をここに書きます');

  // The same connected editor survives refresh on a phone's situation tab.
  await page.setViewportSize({width: 390, height: 844});
  await page.waitForFunction(() => document.getElementById('d-body').dataset.layout === 'tabs');
  await page.locator('.sit-tab', {hasText: '📍 現在地'}).click();
  await editor.focus();
  await editor.evaluate(n => { window.originalEditor = n; n.setSelectionRange(1, 3); });
  items[2].title = 'スマホでも進捗が更新';
  await page.evaluate(() => refresh());
  assert.equal(await editor.evaluate(n => n === window.originalEditor && document.activeElement === n && n.selectionStart === 1 && n.selectionEnd === 3), true);
  assert.ok(await editor.evaluate(n => n.getBoundingClientRect().width >= 250 && n.getBoundingClientRect().height >= 140));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no phone horizontal overflow');
  // A composing Ctrl+Enter must not submit an incomplete Japanese answer.
  await editor.evaluate(n => n.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, isComposing: true, bubbles: true})));
  assert.equal(sent.length, 0);

  // The secretary home uses the same editor without stealing focus on each board poll.
  await page.setViewportSize({width: 1440, height: 900});
  await page.evaluate(() => { state.open = null; setView(ConsoleSecretary.VIEW); });
  const homeEditor = page.locator('#list .sec-reply');
  await homeEditor.fill('ホームの回答');
  await homeEditor.evaluate(n => { window.homeEditor = n; n.setSelectionRange(1, 3); });
  await page.evaluate(() => refresh());
  assert.equal(await homeEditor.evaluate(n => n === window.homeEditor && document.activeElement === n && n.selectionStart === 1 && n.selectionEnd === 3), true);

  // A genuinely removed card must disappear even while it is being edited.
  secretary.decide = [];
  await page.evaluate(() => refresh());
  assert.equal(await page.locator('#list .sec-reply').count(), 0);
  // Reopening a request restores the draft; sending still targets that exact request.
  secretary.decide = [card];
  await page.evaluate(() => refresh());
  await page.locator('#list').getByRole('button', {name: '✏️ 別の答え'}).click();
  assert.equal(await homeEditor.inputValue(), 'ホームの回答');
  await homeEditor.press('Control+Enter');
  await page.locator('#list .sec-card.sent').waitFor();
  assert.deepEqual(sent.at(-1), {id: 'c', body: {text: '「移行前に旧データを書き出すか」への回答: ホームの回答', request_id: 21}});
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Situation comment input: refresh, IME, selection, mobile, home, removal and sending: passed');
})().catch((e) => { console.error(e); process.exit(1); });
