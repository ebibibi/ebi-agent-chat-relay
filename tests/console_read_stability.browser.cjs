// A reader scrolled up in a conversation keeps the text they are reading still while polls
// re-render the messages and the family list above them changes height.
// Run with Playwright installed: node --test tests/console_read_stability.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==', 'base64');
const fixture = (id, title, parent_id, status = 'idle') => ({ id, title, parent_id, status, thread_id: id,
  bucket: status === 'running' ? 'ai' : 'idle', running: status === 'running', priority: 2,
  last_activity_at: '2026-11-09T10:00:00Z' });
const FILE = '/console/api/files/1/0123456789abcdef0123456789abcdef/shot.png';
const message = (id, n) => ({ id: `${id}-${n}`, kind: n % 2 ? 'agent' : 'human', is_bot: Boolean(n % 2),
  created_at: '2026-11-09T10:00:00Z',
  content: n % 5 === 1 ? `報告 ${n}\n\n[shot.png](${FILE})\n\n` + '本文の行です。'.repeat(30) : `メッセージ ${n} ` + 'あいうえお'.repeat(40) });

for (const width of [1440, 390]) {
  test(`the text being read stays still across polls at width ${width}`, async (t) => {
    const browser = await chromium.launch({ headless: true,
      executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
    t.after(() => browser.close());
    const page = await browser.newPage({ viewport: { width, height: width > 900 ? 1100 : 844 }, locale: 'ja-JP' });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let tail = '書きかけ';
    let report = '短い報告';
    const items = [fixture('p', '親'), fixture('c', '子', 'p', 'running'), fixture('d', '別の子', 'p', 'running')];
    await page.route('http://console.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/' || pathname.startsWith('/static/')) {
        const file = pathname === '/' ? 'index.html' : path.basename(pathname);
        return route.fulfill({ body: fs.readFileSync(path.join(root, file)),
          contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
      }
      if (pathname.startsWith('/console/api/files/')) return route.fulfill({ body: PNG, contentType: 'application/octet-stream' });
      const id = decodeURIComponent((pathname.match(/\/items\/([^/]+)\//) || [])[1] || '');
      let data = {};
      if (pathname.endsWith('/board')) data = { items, slots: {}, places: [] };
      else if (pathname.endsWith('/messages')) data = { messages: id === 'c'
        ? [...Array.from({ length: 30 }, (_, n) => message(id, n)), { id: 'c-tail', kind: 'agent', is_bot: true, content: tail, created_at: '2026-11-09T10:00:00Z' }]
        : [{ id: `${id}-r`, kind: 'agent', is_bot: true, content: report, created_at: '2026-11-09T10:00:00Z' }] };
      else if (pathname.endsWith('/live')) data = { running: true, live: { status: 'running', activities: [] } };
      else if (pathname.endsWith('/situation')) data = { situation: { asks: [], doing: [], decided: [] }, waits: [] };
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    });
    await page.goto('http://console.test/');
    await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length === 3);
    await page.evaluate(() => openItem('c'));
    await page.waitForFunction(() => document.querySelectorAll('#msgs .file-thumb img').length >= 3);
    // Read from the middle of the conversation.
    await page.evaluate(() => { const m = $('msgs'); m.scrollTop = (m.scrollHeight - m.clientHeight) / 2; });
    await page.evaluate(() => {
      // Watch the first paragraph in view: any movement is what the reader sees.
      // Each move says which render was last and what the messages looked like then.
      const box = $('msgs');
      const edge = box.getBoundingClientRect().top;
      const anchor = [...box.querySelectorAll('.msg')].find(n => n.getBoundingClientRect().top > edge + 40);
      const where = () => anchor.isConnected ? Math.round(anchor.getBoundingClientRect().top) : null;
      window.__moves = [];
      let label = '';
      for (const name of ['fillMessages', 'renderDetail', 'renderList']) {
        const original = window[name];
        window[name] = function (...args) { label = name; return original.apply(this, args); };
      }
      let previous = where();
      const check = () => {
        const y = where();
        if (y !== previous) {
          window.__moves.push(`${previous}->${y} after ${label} scrollTop=${box.scrollTop} ` +
            `images=${box.querySelectorAll('.file-thumb img').length}`);
          previous = y;
        }
      };
      // What is painted: layout, then the resize observers (the Console's own first), then paint.
      // A rAF callback runs before layout and would see the frame before it is settled.
      const painted = new ResizeObserver(check);
      const observe = () => [box, ...box.children, $('d-family'), $('d-head'), $('live-slot')]
        .forEach(node => node && painted.observe(node));
      observe();
      new MutationObserver(observe).observe(box, { childList: true });
      box.addEventListener('scroll', check);
    });
    for (let i = 0; i < 4; i++) {
      tail += ' 続きの文章。'.repeat(20);
      // A child moves on: its latest report is refetched, and the family list above the
      // conversation grows or shrinks with it.
      report = i % 2 ? '短い報告' : '報告が伸びました。'.repeat(20);
      for (const item of items) item.last_activity_at = `2026-11-09T10:0${i + 1}:00Z`;
      await page.evaluate(async () => { await refresh(); await loadMessages('c'); });
      await page.waitForTimeout(2300); // a live tick and the family reports in between
    }
    assert.deepEqual(await page.evaluate(() => window.__moves.slice(0, 10)), []);
    if (width === 390) {
      // The phone's viewport reports while the reader scrolls; that must not undo the scroll.
      const target = await page.evaluate(() => { $('msgs').scrollTop -= 300; return $('msgs').scrollTop; });
      await page.evaluate(() => window.visualViewport.dispatchEvent(new Event('scroll')));
      await page.waitForTimeout(100);
      assert.equal(await page.evaluate(() => $('msgs').scrollTop), target);
    }
    assert.deepEqual(errors, []);
  });
}

// Beside the 📍 panel a parent's family list is one line that unfolds on request. It used to be
// hidden by the panel and shown again by family.js whenever a child's report arrived: the
// conversation under it jumped by the list's height, and once hidden for good the children
// could not be checked from the conversation at all.
test('a parent\'s family list stays one steady line beside the panel', async (t) => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'ja-JP' });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Many children: their reports arrive three at a time, each batch a separate redraw.
  const items = [fixture('p', '親'), ...Array.from({ length: 9 }, (_, n) => fixture(`k${n}`, `子 ${n}`, 'p', 'running'))];
  await page.route('http://console.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/' || pathname.startsWith('/static/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({ body: fs.readFileSync(path.join(root, file)),
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
    }
    const id = decodeURIComponent((pathname.match(/\/items\/([^/]+)\//) || [])[1] || '');
    let data = {};
    if (pathname.endsWith('/board')) data = { items, slots: {}, places: [] };
    else if (pathname.endsWith('/messages')) {
      await new Promise(r => setTimeout(r, 150));
      data = { messages: id === 'p' ? Array.from({ length: 30 }, (_, n) => message(id, n))
        : [{ id: `${id}-r`, kind: 'agent', is_bot: true, content: `${id} の報告`, created_at: '2026-11-09T10:00:00Z' }] };
    } else if (pathname.endsWith('/situation')) data = { situation: { asks: [], doing: [], decided: [] }, waits: [] };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => localStorage.removeItem('situationHidden'));
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length === 10);
  await page.evaluate(() => {
    openItem('p');
    window.__heights = new Set();
    // Every height the list takes on a frame; more than one is a jump the reader sees.
    const frame = () => { const f = $('d-family'); if (f && f.isConnected) window.__heights.add(f.hidden ? 0 : f.offsetHeight); requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
  });
  await page.locator('#d-side').getByText('k8 の報告', { exact: false }).waitFor();
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#d-family .family-heading').isVisible(), true);
  assert.equal(await page.locator('#d-family .family-tasks').isVisible(), false);
  const heights = await page.evaluate(() => [...window.__heights].filter(Boolean));
  assert.equal(heights.length, 1, JSON.stringify(heights));
  // Unfolding shows every child, and the choice is remembered.
  await page.locator('#d-family .family-heading').click();
  assert.equal(await page.locator('#d-family .family-task').count(), 9);
  assert.equal(await page.evaluate(() => localStorage.getItem('familyListFolded')), '0');
  assert.deepEqual(errors, []);
});
